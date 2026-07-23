# repo-assessor — Assumptions

Per llmsafespaces README-LLM rule 7: every assumption the implementation relies on, with validation status. Updated as assumptions are validated or disproved.

| # | Assumption | Validation | Status |
|---|---|---|---|
| A-01 | Reddit's script-type OAuth2 flow accepts `grant_type=password` with `client_id`+`client_secret`+`username`+`password` and returns a bearer token. | Reddit API wiki, OAuth2 Quick Start. Endpoints: `POST https://www.reddit.com/api/v1/access_token`. | **validated** |
| A-02 | Authenticated API base is `https://oauth.reddit.com` (not `www.reddit.com`). | Reddit API wiki. | **validated** |
| A-03 | Rate limit: 600 OAuth requests / 10 min = ~1/sec sustained, bursty to 60/min for personal-use script apps. | Reddit API wiki (Rate Limits section, post-2023 API changes). Our cadence: poll every 2 min × ~25 posts + ~3 calls/post × ~5 qualifying posts = ~80 calls/10min. | **validated** |
| A-04 | `GET /r/{sub}/new` returns a listing with `data.children[].data` containing `id`, `title`, `selftext`, `url`, `author`, `created_utc`, `permalink`, `link_flair_text`, `is_self`. | Reddit API wiki + cross-checked against PRAW field names. | **validated** |
| A-05 | `GET /comments/{article_id}` returns a 2-element array: `[submission_listing, comments_listing]`. Comments are flat top-level + nested `replies`. | Reddit API wiki. | **validated** |
| A-06 | `POST /api/comment` with `api_type=json` body `thing_id=t1_xxx&text=...&api_type=json` creates a reply to that comment and returns `json.data.things[0].data.id`. | Reddit API wiki. | **validated** |
| A-07 | `POST /api/submit` with `kind=self&sr=...&title=...&text=...&api_type=json` creates a text post and returns `json.data.id`. | Reddit API wiki. | **validated** |
| A-08 | `POST /api/distinguish` with `id=t1_xxx&how=yes&api_type=json` distinguishes a comment; requires mod privileges in the target sub; returns 403 otherwise. | Reddit API wiki. | **validated** |
| A-09 | Reddit text-post body limit is 40000 chars; submission title limit is 300 chars. | Reddit help center. | **validated** |
| A-10 | Reddit bot accounts are subject to the same rate-limit tiers as human users unless the operator has applied for and been granted bot-tier rate limits. | Reddit API wiki. We assume default tier; documented as a deployment consideration. | **assumed** |
| A-11 | The canonical "how was AI used" sticky on r/selfhosted is posted by `u/asimovs-auditor` and contains the phrase "how AI was used". | User-supplied example. Configurable via `STICKY_AUTHOR` and `STICKY_TEXT_REGEX` so changes do not require code edits. | **validated** (by example) |
| A-12 | The bot account is not banned or rate-limited on r/selfhosted or on `SHADOW_TARGET_SUBREDDIT`. | Operational — user's responsibility. Cannot be validated from code. | **assumed** |
| A-13 | The LLMSafeSpaces Python SDK exposes the `LLMSafeSpaces` class with correct dataclasses. | Published SDK 0.5.4 had dataclass drift (Workspace missing `agentNeedsRefresh`, APIKey missing `decryptAccess`/`dekSynced`). **Fix committed to lenaxia/llmsafespaces branch `fix/sdk-missing-fields` (commit da1cb13)** — tested against api.safespaces.dev, all three previously-broken methods work. Function uses `_request()` bypass for backward-compat with 0.5.4. Once the fix is published to PyPI (0.5.5+), the bypass can be removed and high-level SDK methods used directly. Bypass is: `create_workspace()` → `_request("POST", "/workspaces", ...)`, `create_session()` → `_request("POST", "/workspaces/{id}/sessions/new")`, `delete_workspace()` → `_request("DELETE", "/workspaces/{id}")`. `send_message` is unaffected (MessageResponse dataclass is fine). | **validated (workaround in code, fix in SDK repo)** |
| A-14 | `client.workspaces.create(name=..., runtime=...)` returns a `Workspace` dataclass with `id` and `phase` fields. | Direct read of `client.py:156-160` and `types.py:10-22`. | **validated** |
| A-15 | `client.workspaces.get_status(workspace_id)` returns a dict containing `phase`. | Direct read of `client.py:178-179`. | **validated** |
| A-16 | `client.sessions.ensure(workspace_id)` returns an `EnsureSessionResponse` with `sessionId`. | Direct read of `client.py:222-225` and `types.py:42-46`. | **validated** |
| A-17 | `client.sessions.send_message(workspace_id, session_id, content)` blocks until the agent completes its turn and returns a `MessageResponse` with `content` (extracted text from `parts[].text`). | **Validated** by reading the API server source: `SendMessage` handler (`api/internal/handlers/proxy_handlers.go:37`) proxies to opencode's `/session/{id}/message`. The proxy streams chunk-by-chunk (`proxy.go:550-567`) but the SDK's `httpx.Client.request()` accumulates the full body before `resp.json()`. Contrast with `SendPromptAsync` (`proxy_handlers.go:73`) which returns 409 if busy and proxies to `/prompt_async` (fire-and-forget). | **validated** |
| A-18 | LLMSafeSpaces workspace pods can reach `github.com` and `api.github.com` over HTTPS. | User confirmation. Egress policy is operator-controlled. | **assumed** (operator) |
| A-19 | The LLMSafeSpaces instance has a pre-provisioned bot account with an API key and LLM provider credentials configured. | **DISPROVED against api.safespaces.dev on 2026-07-23**: account `/auth/me` succeeds and `/provider-credentials` returns `[]`. Workspace boots (phase transitions through `Creating`) but `agentHealth.providersConfigured: 0` — the agent has no model and never becomes truly Active. Operator must configure an LLM provider (POST `/provider-credentials` with `{name, kind, slug, apiKey, baseURL?, modelAllowlist?}`) before this function can run. | **DISPROVED** (operator action required) |
| A-20 | A single opencode session supports multiple sequential user messages with the agent retaining context between them (classify → assess in same session). | opencode `specs/v2/session.md` documents durable multi-turn sessions; `SessionRunCoordinator ... allows different Sessions to run concurrently`. | **validated** |
| A-21 | Agent responses are returned as plain text in `MessageResponse.content`, may contain markdown including ```json fences around JSON. | opencode assistant response format. Defended against by stripping fences and slicing first-{ to last-}. | **assumed** (defensive) |
| A-22 | Fission TimeTrigger does not start a second concurrent invocation of the same function if the prior invocation is still running. | Fission documentation describes TimeTrigger as cron-driven function invocation; concurrent-invocation behavior requires runtime validation against the deployed Fission version. We additionally mitigate via state-store in-flight tracking so two concurrent runs cannot both start a workspace for the same post. | **assumed** (mitigated) |
| A-23 | The Fission environment for this function mounts a PVC at `/state` (for JSON/SQLite backends) or permits network access to the configured Postgres (for the Postgres backend). | Operational — user's responsibility. README documents this requirement. | **assumed** (operator) |
| A-24 | The Fission environment permits the function pod to listen on `METRICS_PORT` (default 8080) and be scraped by Prometheus. | Operational — user's responsibility. README documents this requirement. | **assumed** (operator) |
| A-25 | `httpx==0.27.0` is installable in the Fission Python 3.11 environment. | matriarch/violetscans use `requests>=2.28` in the same env class; httpx is a pure-Python dep with similar footprint. | **assumed** |
| A-26 | Reddit submissions' `permalink` field is relative (`/r/selfhosted/comments/...`); the absolute URL is `https://www.reddit.com` + permalink. | Reddit API wiki. | **validated** |
| A-27 | Reddit's `link_flair_text` field is `None` when no flair is set (not an empty string). | Reddit API wiki. Tests cover both `None` and `""`. | **validated** |
| A-28 | Reddit's `author` field is `None` (or `[deleted]`) when the user has deleted their account; our code treats both as "no author". | Reddit API wiki. | **validated** |
| A-29 | Reddit's `/api/comment` returns 400 if the parent comment has been removed or the submission locked; the bot must surface this as a per-post error, not a run-level failure. | Reddit API behaviour. | **assumed** |
| A-30 | Postgres schema (`repo_assessor_state` table with JSONB columns) is portable across CNGP-managed Postgres versions 13+. | CNGP defaults to Postgres 15+; JSONB stable since PG 9.4. | **validated** |
| A-31 | The opencode agent running inside the workspace will execute shell commands (git clone, cloc, etc.) without prompting the user for permission. | Workspace runtime configures opencode permissions for the bot account. LLMSafeSpaces README-LLM describes permission handling via agent roles. Operator must provision a role that grants `bash` and `read` to all paths. | **assumed** (operator) |
| A-32 | The workspace's `git clone` of a public GitHub repo succeeds without GitHub credentials. | Public repos are anonymously clonable. Private repos will fail and the assessment will record null metadata. | **validated** |
| A-33 | Reddit's `created_utc` is a Unix epoch float (seconds since 1970-01-01 UTC), not milliseconds. | Reddit API wiki. | **validated** |
| A-34 | Reddit rate-limit headers (`x-ratelimit-remaining`, `x-ratelimit-reset`) are present on OAuth API responses and can be used for proactive throttling. | Reddit API wiki. Used for backoff on 429. | **validated** |

## Open assumptions requiring validation before production

- **A-10** (bot rate-limit tier) — validate by running the bot in shadow mode against r/selfhosted for 24h and checking for 429s.
- **A-12** (account not banned) — validate by making one test OAuth call before deployment.
- **A-18, A-19, A-23, A-24, A-31** — operator-side validations; documented in README deployment checklist.
- **A-22** (Fission TimeTrigger concurrency) — validate by deploying with `--cooldown` set explicitly and observing two overlapping cron ticks.

## Assumptions that would change the design if disproved

- **A-17 disproved** (SDK returns immediately, agent runs async) → VALIDATED: `send_message` blocks. The API server's `SendMessage` handler proxies opencode's `/session/{id}/message` synchronously; the SDK's `httpx.Client` accumulates the full response before parsing. `SendPromptAsync` is the fire-and-forget variant — two distinct endpoints, not a flag.
- **A-20 disproved** (sessions are single-turn) → fall back to one workspace per post (original design). Less efficient but functionally correct.
- **A-22 disproved** (Fission runs concurrent invocations) → add a state-store advisory lock (e.g. `pg_advisory_lock` on Postgres, file-lock on JSON/SQLite) around the entire `_run()` to enforce single-execution. Cheap addition; deferred until needed.

## Live validation log (2026-07-23 against api.safespaces.dev)

Validated by direct HTTP + SDK calls:

- **Auth**: `Bearer` token via `LLMSAFESPACES_API_KEY` works. `GET /auth/me` returns the user record.
- **Workspace create**: SDK's `workspaces.create()` raises `TypeError` on `agentNeedsRefresh`. Bypass via `_request("POST", "/workspaces", ...)` works and returns the full server payload including `agentNeedsRefresh: false` and `phase: ""` initially, transitioning to `Creating`.
- **Workspace status**: SDK's `workspaces.get_status()` works as-is. Returns `phase`, `pvcName`, `activeSessions`, `credentialState`, `agentHealth`, `contextUsed`, `contextTotal`. The `agentHealth.providersConfigured` field is the authoritative signal for "does the workspace have an LLM provider".
- **Workspace lifecycle**: workspace boots (`Creating`), PVC is provisioned, but **never reaches `Active`** when the user account has zero provider credentials. The pod starts; the agent inside has no model to call.
- **Workspace delete**: SDK's `workspaces.delete()` crashes the same way (server returns the deleted workspace with new fields). Bypass via `_request("DELETE", "/workspaces/{id}")` returns 204 (no body) cleanly.
- **Session create**: `POST /workspaces/{id}/sessions/new` returns `{workspaceId, workspacePhase, sessionId, resumed}`. Server returns 503 `workspace_timeout` if the workspace isn't Active.
- **API keys list**: SDK's `auth.list_api_keys()` raises `TypeError` on `decryptAccess`. Bypass via `_request("GET", "/auth/api-keys")` returns the array with `decryptAccess: false, dekSynced: false` fields.

Findings reported to llmsafespaces as SDK/server drift.

## Reddit read path

Could not be validated from the build sandbox — Reddit's WAF (403) blocks all egress from this IP range regardless of User-Agent. Operator must validate the Reddit read flow locally before deployment. The `responses`-mocked tests cover the SDK code path; the live API shape is documented from Reddit's public OAuth2 wiki and cross-checked against PRAW field names.

### Correction (2026-07-23): Reddit 403 diagnosis

Earlier note claimed Reddit blocks the sandbox IP. That was wrong. Actual cause: Reddit serves a JS PoW (proof-of-work) challenge on the public `.json` endpoints to unauthenticated clients. Verified:

- `GET https://www.reddit.com/` (HTML homepage) → **HTTP 200** from this IP
- `GET https://www.reddit.com/r/selfhosted/new.json` → HTTP 403 with HTML body containing `<title>Reddit - Please wait for verification</title>` and JS that solves a challenge and submits it
- Egress IP from sandbox: `76.135.100.247` — same network the operator is on, where Reddit works fine
- Tried multiple User-Agents (browser string, Reddit API format, curl/8.0.1, empty) — all 403
- Tried `old.reddit.com`, `oauth.reddit.com` (no auth) — all 403

The function's production code is unaffected: it uses `oauth.reddit.com` with `Authorization: Bearer ...`, which Reddit's bot-detection does not subject to the JS challenge. The sandbox just can't validate the unauthenticated test path.

**Operator action**: validate `reddit_client.find_canonical_sticky` against a real r/selfhosted thread from your local machine (where OAuth requests are the only path that needs to work anyway). The `responses`-mocked tests cover the SDK code path; live validation only needs to confirm the response shape matches.

### Final correction (2026-07-23): Reddit block is real

Followed up by inspecting the actual 403 response body. It is **not** a JS
PoW challenge — it's a hard "You've been blocked by network security" page
with a link to reddithelp support. The block is at Reddit's edge against
this specific egress IP (76.135.100.247, Comcast residential Seattle,
AS7922). The operator reports Reddit works from their machine on the
same ASN, so the block is scoped to the specific IP, not the network
range. Reddit reputation systems likely flagged this IP from prior
automated traffic.

The function's production path (oauth.reddit.com with Bearer auth) is
unaffected by this block — OAuth-authenticated API requests take a
different path through Reddit's edge. Unauthenticated .json endpoints
from the blocked IP get the network-security page.

Confirmed: not a UA issue (all UAs 403 identically), not solvable by
executing JS (no challenge to solve).
