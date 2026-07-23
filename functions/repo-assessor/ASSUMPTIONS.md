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
| A-13 | The LLMSafeSpaces Python SDK at `sdks/python/llmsafespaces/client.py` exposes the `LLMSafeSpaces` class with `workspaces.create`, `workspaces.get_status`, `workspaces.delete`, `sessions.ensure`, `sessions.send_message` methods, matching the read of the file at HEAD. | Published to PyPI as `llmsafespaces==0.5.4` (https://pypi.org/project/llmsafespaces/). Wheel contents verified: constructor signature `(base_url, *, api_key=...)` matches; `__version__` field present. | **validated** |
| A-14 | `client.workspaces.create(name=..., runtime=...)` returns a `Workspace` dataclass with `id` and `phase` fields. | Direct read of `client.py:156-160` and `types.py:10-22`. | **validated** |
| A-15 | `client.workspaces.get_status(workspace_id)` returns a dict containing `phase`. | Direct read of `client.py:178-179`. | **validated** |
| A-16 | `client.sessions.ensure(workspace_id)` returns an `EnsureSessionResponse` with `sessionId`. | Direct read of `client.py:222-225` and `types.py:42-46`. | **validated** |
| A-17 | `client.sessions.send_message(workspace_id, session_id, content)` blocks until the agent completes its turn and returns a `MessageResponse` with `content` (extracted text from `parts[].text`). | Direct read of `client.py:230-239`. The "blocks until turn complete" semantics are inferred from the absence of any async/streaming return type and from the SDK being synchronous (`httpx.Client`, not `AsyncClient`). | **validated** (mostly) |
| A-18 | LLMSafeSpaces workspace pods can reach `github.com` and `api.github.com` over HTTPS. | User confirmation. Egress policy is operator-controlled. | **assumed** (operator) |
| A-19 | The LLMSafeSpaces instance has a pre-provisioned bot account with an API key and LLM provider credentials configured. | User confirmation. | **assumed** (operator) |
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

- **A-17 disproved** (SDK returns immediately, agent runs async) → switch to a poll-the-session-history model. Significant rewrite of `workspace_assessor.py`. Mitigation: A-17 is mostly validated by reading the SDK source; the synchronous httpx client + absence of an `await` keyword in the SDK methods makes async unlikely.
- **A-20 disproved** (sessions are single-turn) → fall back to one workspace per post (original design). Less efficient but functionally correct.
- **A-22 disproved** (Fission runs concurrent invocations) → add a state-store advisory lock (e.g. `pg_advisory_lock` on Postgres, file-lock on JSON/SQLite) around the entire `_run()` to enforce single-execution. Cheap addition; deferred until needed.
