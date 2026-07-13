# repo-assessor

Fission function that monitors r/selfhosted for posts announcing new self-hosted projects, assesses the linked repository in an ephemeral LLMSafeSpaces workspace, and posts a structured assessment comment under the canonical "how was AI used" sticky.

## What it does

1. Polls r/selfhosted `/new` on a Fission TimeTrigger.
2. Filters posts by configurable flair rules, age, presence of a GitHub URL, and whether the bot has already processed them.
3. Finds the canonical sticky comment (default author: `u/asimovs-auditor`).
4. Spins up a single ephemeral LLMSafeSpaces workspace per run; one session per qualifying post.
5. Classifies whether the post is announcing a project (gate before assessment).
6. If yes, runs a structured repo assessment via the workspace agent.
7. Compares against a category-appropriate baseline (Sonarr, Vaultwarden, Homer, Pi-hole, Uptime Kuma).
8. Posts the formatted assessment as a reply under the sticky. In shadow mode, mirrors to a private target sub instead.

## Design

See `TESTPLAN.md` for the comprehensive test plan, `ASSUMPTIONS.md` for stated assumptions, `ADVERSARIAL.md` for the 3-phase self-review. The design follows `llmsafespaces/README-LLM.md` rules: TDD, type safety, explicit over implicit, pinned versions, no over-engineering.

## Repo layout

```
repo-assessor/
├── model.py              # frozen dataclasses, enums, exceptions
├── config.py             # env-var loader with validation
├── baselines.py          # static baseline profiles loader
├── github_urls.py        # extract + normalise github.com URLs
├── reddit_client.py      # OAuth2 + JSON API (requests-based)
├── workspace_assessor.py # LLMSafeSpaces SDK wrapper
├── comment_formatter.py  # assessment → markdown
├── metrics.py            # Prometheus collectors
├── main.py               # Fission entrypoint, orchestration
├── state/
│   ├── __init__.py       # backend factory
│   ├── json_backend.py
│   ├── sqlite_backend.py
│   └── postgres_backend.py
├── prompts/
│   ├── classify.md       # post classification prompt
│   └── assess.md         # repo assessment prompt
├── baselines.json        # Sonarr, Vaultwarden, Homer, Pi-hole, Uptime Kuma
├── tests/                # 211 tests, all green
├── TESTPLAN.md
├── ASSUMPTIONS.md
├── ADVERSARIAL.md
└── README.md
```

## Local testing

```bash
pip install -r requirements.txt -r requirements-test.txt
pytest                                  # 211 tests, ~6s
pytest -k "not main_integration"        # unit/contract only, ~2s
```

## Build

```bash
task local-build-repo-assessor
```

Produces `repo-assessor-<version>.zip` for `fission function create`.

## Deployment

### 1. Fission environment

The environment needs:
- Python 3.11 runtime
- A PVC mount at `/state` (for JSON/SQLite backends) OR network access to your Postgres (recommended; CNGP-friendly)
- Permission to listen on `METRICS_PORT` (default 8080) for Prometheus scraping
- Network egress to `oauth.reddit.com`, `www.reddit.com`, your LLMSafeSpaces instance, and (from workspace pods) `github.com`

```bash
fission env create --name python3-repo-assessor \
  --image <your-image> \
  --builder fission/python-builder \
  --mincpu 200 --maxcpu 1000 \
  --minmemory 256 --maxmemory 1024
```

Mount the state PVC via the env's PodSpec if using JSON/SQLite backends.

### 2. Secrets

Create a Fission secret with all the required env vars:

```bash
kubectl create secret generic repo-assessor-secrets --namespace fission \
  --from-literal=REDDIT_CLIENT_ID=... \
  --from-literal=REDDIT_CLIENT_SECRET=... \
  --from-literal=REDDIT_USERNAME=repo-assessor-bot \
  --from-literal=REDDIT_PASSWORD=... \
  --from-literal=REDDIT_USER_AGENT="repo-assessor/0.1 by repo-assessor-bot" \
  --from-literal=LLMSAFESPACES_URL=https://your-lss.example.com \
  --from-literal=LLMSAFESPACES_API_KEY=lsp_... \
  --from-literal=STATE_BACKEND=postgres \
  --from-literal=STATE_DATABASE_URL=postgresql://...
```

### 3. Function

```bash
fission function create --name repo-assessor \
  --env python3-repo-assessor \
  --src repo-assessor-0.1.0.zip \
  --entrypoint main.main \
  --secretmap repo-assessor-secrets \
  --minscale 0 --maxscale 1 \
  --executortimeout 1800
```

`--maxscale 1` enforces single-writer semantics. `--executortimeout 1800` (30 min) accommodates long assessments without artificial timeout.

### 4. TimeTrigger

```bash
fission tt create --name repo-assessor-poll \
  --function repo-assessor \
  --cron "*/2 * * * *"
```

### 5. LLMSafeSpaces prep

- Provision a bot user account on your LLMSafeSpaces instance.
- Generate an API key for that account.
- Configure an LLM provider (Anthropic, OpenAI, etc.) and grant the bot permission to use it.
- Provision an agent role that grants `bash` and `read` permissions broadly (the assessment agent needs to clone repos and run git/cloc/grep).

### 6. Prometheus scraping

Expose the function pod's metrics port via a Service and ServiceMonitor (or pod annotation depending on your Prometheus stack). Metrics are at `http://<pod-ip>:8080/metrics`.

## Shadow mode (testing without exposure)

Set `SHADOW_MODE=true` and `SHADOW_TARGET_SUBREDDIT=<your-private-test-sub>`. The bot will:

1. Mirror each qualifying post to the target sub (programmatically — no LLM in the mirror).
2. Create a simulated sticky under the mirror.
3. Reply under the simulated sticky with the assessment.

The original poster is not notified. Use this to validate classifier accuracy, assessment quality, and formatting before exposing the bot to the live subreddit.

## Env var reference

See `config.py` for the canonical list. Required:

- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`, `REDDIT_USER_AGENT`
- `LLMSAFESPACES_URL`, `LLMSAFESPACES_API_KEY`

Conditionally required:

- `SHADOW_TARGET_SUBREDDIT` (when `SHADOW_MODE=true`)
- `STATE_DATABASE_URL` (when `STATE_BACKEND=postgres`)

Optional (defaults shown):

| Var | Default | Notes |
|---|---|---|
| `REDDIT_SUBREDDIT` | `selfhosted` | Source sub |
| `REDDIT_NEW_LIMIT` | `25` | Posts per poll (1..100) |
| `LLMSAFESPACES_RUNTIME` | `python` | Workspace runtime |
| `WORKSPACE_READY_TIMEOUT` | `300` | Seconds to wait for Active |
| `WORKSPACE_SESSION_CONCURRENCY` | `3` | Parallel sessions per workspace |
| `STICKY_AUTHOR` | `asimovs-auditor` | Canonical sticky author |
| `STICKY_TEXT_REGEX` | `(?i)how AI was used` | Sticky body match |
| `MAX_POST_AGE_HOURS` | `24` | Skip older posts |
| `SOURCE_FLAIR_INCLUDE` | (empty) | Comma-separated allowlist |
| `SOURCE_FLAIR_EXCLUDE` | (empty) | Comma-separated denylist |
| `STATE_BACKEND` | `json` | `json` / `sqlite` / `postgres` |
| `STATE_PATH` | `/state/repo-assessor.{json,db}` | JSON/SQLite path |
| `STATE_PRUNE_HOURS` | `48` | State entry TTL |
| `METRICS_PORT` | `8080` | Prometheus scrape port |
| `LOG_JSON` | `false` | Structured logging |
| `DRY_RUN` | `false` | Skip final Reddit write |
| `BASELINE_DEFAULT_CATEGORY` | `media` | Fallback category for `other` |

## Updating baselines

The `baselines.json` file holds static comparison profiles. To refresh a baseline (e.g. Sonarr has matured significantly):

1. Run the assessment prompt (`prompts/assess.md`) manually against `Sonarr/Sonarr` in a workspace.
2. Capture the resulting metadata + scores.
3. Update the corresponding entry in `baselines.json`.
4. Bump `version` if the schema changes.

Baselines intentionally are NOT fetched live — they're a reference comparison, not a moving target.

## Operational checks before going live

Per `ASSUMPTIONS.md`, validate before production:

- [ ] Bot account is not banned on r/selfhosted (one test OAuth call).
- [ ] Workspace pods can clone from github.com (run one test workspace manually).
- [ ] Fission TimeTrigger does not double-fire (verify with `--cooldown`).
- [ ] `--executortimeout` on the function is set high enough for the longest assessment.
- [ ] State backend is reachable and credentials work.
- [ ] Prometheus is scraping `/metrics`.
- [ ] Shadow mode has been run for ≥24h against r/selfhosted without surprises.
- [ ] Mods of r/selfhosted have approved the bot in writing.
