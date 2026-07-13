# repo-assessor — Test Plan

Single source of truth for what must be tested and how. Every code change adds or updates a row here before the code that satisfies it.

Levels, in order of priority:

1. **Unit** — single module in isolation. All outbound I/O mocked. Fast (<1s total).
2. **Contract** — same suite against multiple implementations of one Protocol. Catches backend drift.
3. **Integration** — full `_run()` workflow with all collaborators mocked. Asserts state transitions, ordering, summary shape.
4. **Property** — invariants over generated inputs (random strings, random assessment JSON, etc.). Catches edge cases unit tests miss.
5. **Live** — opt-in via env var (`TEST_POSTGRES_URL`, `TEST_LIVE_REDDIT`, `TEST_LIVE_LSS`). Skipped by default. Run manually before release.

## Aims (per level)

| Level | Aim |
|---|---|
| Unit | Each module's public API behaves per its contract for every input class we can enumerate. |
| Contract | All `StateStore` implementations are observationally indistinguishable for every operation in the Protocol. |
| Integration | The orchestration in `main._run()` produces the right state transitions and summary for each scenario, independent of the modules' internals. |
| Property | Invariants that should hold for *any* input: idempotency, monotonicity, no information loss, no crashes on garbage. |
| Live | Smoke-test that real APIs match our assumptions. Never blocks CI. |

## Coverage matrix

For each module: happy path (≥2 cases), error path (≥2 cases), edge cases (≥3). Numbered `T-XXX` for stable reference from code comments and commit messages.

### types.py

| ID | Level | Scenario |
|---|---|---|
| T-001 | unit | `Decision` enum has exactly 5 values with expected string forms |
| T-002 | unit | `Category.OTHER` falls back to default in `pick_baseline` |
| T-003 | unit | `Assessment` dataclass rejects score <1 or >5 at construction (`__post_init__`) |
| T-004 | unit | `ScoreEvidence` rejects empty evidence string |
| T-005 | unit | `Classification` dataclass: `is_announcement=False` allowed with any category |
| T-006 | unit | All frozen dataclasses hashable (for set/dict use in tests) |
| T-007 | unit | `RunSummary` errors list capped at 20 on construction |

### config.py

| ID | Level | Scenario |
|---|---|---|
| T-101 | unit | All required env vars present → `Config` constructed |
| T-102 | unit | Missing required env var → `ConfigError` names the var |
| T-103 | unit | `Category` parse: `BASELINE_DEFAULT_CATEGORY=security` parsed correctly |
| T-104 | unit | Invalid `BASELINE_DEFAULT_CATEGORY=foo` → `ConfigError` |
| T-105 | unit | `SHADOW_MODE=true` without `SHADOW_TARGET_SUBREDDIT` → `ConfigError` |
| T-106 | unit | `STATE_BACKEND=postgres` without `STATE_DATABASE_URL` → `ConfigError` |
| T-107 | unit | Comma-separated flair lists parse to lists (empty string → empty list) |
| T-108 | unit | Whitespace trimmed in comma-separated lists (`" a , b "` → `["a","b"]`) |
| T-109 | unit | Booleans accept `true/1/yes/on` (case-insensitive), reject other values |
| T-110 | unit | Ints reject non-numeric (`REDDIT_NEW_LIMIT=abc` → `ConfigError`) |
| T-111 | unit | `REDDIT_NEW_LIMIT` range checked (1..100, Reddit max) |
| T-112 | unit | Defaults applied when optional vars absent |

### state/ (contract — applies to all backends)

Contract tests run against all three backends. Postgres backend is skipped unless `TEST_POSTGRES_URL` is set; JSON and SQLite always run.

| ID | Level | Scenario |
|---|---|---|
| T-201 | contract | `mark_in_flight` then `get_in_flight` returns same `InFlight` |
| T-202 | contract | `clear_in_flight` makes subsequent `get_in_flight` return None |
| T-203 | contract | `list_stale_in_flight(older_than=1s)` excludes fresh entries, includes old ones |
| T-204 | contract | `set_decision` then `get_decision` round-trips all `Decision` values |
| T-205 | contract | `set_decision` overwrites prior decision (idempotent on re-decide) |
| T-206 | contract | `set_shadow_mapping` then `get_shadow_mapping` round-trips |
| T-207 | contract | `prune(older_than=48h)` removes old decisions, keeps recent |
| T-208 | contract | `prune` removes stale in-flight, keeps fresh |
| T-209 | contract | `prune` keeps shadow mappings regardless of age (mapping is forever — useful for audit) |
| T-210 | contract | Empty backend: all reads return None/[] |
| T-211 | contract | Concurrent writes from two threads (2 writers × 50 ops each) → no corruption, no lost writes (single-writer guaranteed by deployment, but the test still must not corrupt) |
| T-212 | contract | `close()` is idempotent and subsequent ops raise `StateError` |
| T-213 | contract | Unknown submission ID lookups return None (not raise) |
| T-214 | contract | `mark_in_flight` overwrites prior in-flight for same submission (e.g. session retry) |
| T-215 | contract | Datetime precision: round-trips preserve second-precision (Postgres TZ-aware → UTC normalize) |

### state/json_backend.py

| ID | Level | Scenario |
|---|---|---|
| T-301 | unit | Atomic write: temp file in same dir, rename to target on completion |
| T-302 | unit | Partial write (simulated exception mid-write) does not corrupt existing file |
| T-303 | unit | Missing parent dir → created on first write |
| T-304 | unit | File read when missing → empty state (not exception) |
| T-305 | unit | Corrupt JSON in existing file → `StateError` with file path in message |

### state/sqlite_backend.py

| ID | Level | Scenario |
|---|---|---|
| T-306 | unit | Schema created idempotently on init (open existing DB twice) |
| T-307 | unit | WAL mode enabled (`PRAGMA journal_mode=WAL`) |
| T-308 | unit | Missing parent dir → created on first connect |
| T-309 | unit | Locked DB (simulate with second connection holding write lock) → `StateError` after timeout |

### state/postgres_backend.py

| ID | Level | Scenario |
|---|---|---|
| T-310 | unit | Schema created idempotently (`CREATE TABLE IF NOT EXISTS`) |
| T-311 | unit | Indexes created |
| T-312 | unit | Connection retry on transient error (mock psycopg to fail twice then succeed) |
| T-313 | unit | `StateError` raised on persistent connection failure |
| T-314 | unit | SQL parameterized (no string interpolation); verified via mock cursor `execute` call args |
| T-315 | live | Full contract suite against real Postgres (gated on `TEST_POSTGRES_URL`) |

### baselines.py

| ID | Level | Scenario |
|---|---|---|
| T-401 | unit | `load_baselines()` returns dict keyed by all 5 non-other `Category` values |
| T-402 | unit | Each `BaselineProfile` field populated (no None for required fields) |
| T-403 | unit | `pick_baseline(MEDIA)` returns Sonarr profile |
| T-404 | unit | `pick_baseline(OTHER)` falls back to default category |
| T-405 | unit | `pick_baseline(MEDIA, default=SECURITY)` returns Sonarr (category takes priority over default) |
| T-406 | unit | Missing `baselines.json` → `StateError` with path |
| T-407 | unit | Malformed `baselines.json` → `StateError` with parse error |
| T-408 | unit | Unknown category in `baselines.json` → `StateError` (catches typos at load time) |
| T-409 | unit | Score values 1-5 in baselines (schema validation) |
| T-410 | unit | `load_baselines()` called multiple times returns independent objects (no shared mutable state) |

### github_urls.py

| ID | Level | Scenario |
|---|---|---|
| T-501 | unit | Plain `https://github.com/owner/repo` extracted |
| T-502 | unit | URL with `.git` suffix normalized |
| T-503 | unit | URL with `/issues`, `/pulls`, `/releases`, `/blob/...` normalized to bare repo |
| T-504 | unit | URL with query string and anchor stripped |
| T-505 | unit | Multiple URLs in same text all extracted and deduped |
| T-506 | unit | `gist.github.com` URLs NOT matched (separate product) |
| T-507 | unit | `raw.githubusercontent.com` URLs normalized to repo root |
| T-508 | unit | Bare `github.com/owner/repo` (no scheme) extracted and scheme added |
| T-509 | unit | `www.github.com` normalized to `github.com` |
| T-510 | unit | Markdown link `[text](https://github.com/o/r)` extracted |
| T-511 | unit | URLs in angle brackets `<https://github.com/o/r>` extracted |
| T-512 | unit | Empty/None input → `[]` / `None` |
| T-513 | unit | Same repo mentioned 5 times → 1 entry in result |
| T-514 | property | For any URL matched by the regex, `normalize_github_url(extract(...)[0])` is idempotent (normalize is a fixed point) |

### reddit_client.py

| ID | Level | Scenario |
|---|---|---|
| T-601 | unit | OAuth token fetched lazily on first call, cached for subsequent calls |
| T-602 | unit | 401 on API call triggers one token refresh + retry; second 401 → `RedditAPIError` |
| T-603 | unit | `get_new()` parses Reddit listing response into `RedditSubmission` list |
| T-604 | unit | `get_new()` with empty listing → empty list |
| T-605 | unit | Submission with `link_flair_text=None` → `flair=None` |
| T-606 | unit | Submission with `is_self=True` → `is_self=True` even if `url` is present |
| T-607 | unit | `find_canonical_sticky` matches by author + regex |
| T-608 | unit | `find_canonical_sticky` returns None when no sticky matches |
| T-609 | unit | `find_canonical_sticky` distinguishes between top-level and nested (only top-level counts) |
| T-610 | unit | `has_bot_reply` returns True when bot has any top-level comment OR reply under sticky |
| T-611 | unit | `has_bot_reply` returns False when bot has no comments |
| T-612 | unit | `reply_to_comment` POSTs to `/api/comment` with `thing_id=t1_xxx`, returns new comment ID |
| T-613 | unit | `submit_text_post` POSTs to `/api/submit` with correct fields, returns submission ID |
| T-614 | unit | `distinguish_comment` POSTs to `/api/distinguish` with `how=yes` |
| T-615 | unit | `distinguish_comment` on 403 (not mod) → no-op, logs warning (does not raise) |
| T-616 | unit | 429 response → `RedditRateLimit` raised (caller will retry per config) |
| T-617 | unit | 503 response → retried once by client internally; second 503 → `RedditAPIError` |
| T-618 | unit | 5xx other than 503 → `RedditAPIError` immediately (no retry) |
| T-619 | unit | 400 on POST `/api/comment` (e.g. removed thread) → `RedditAPIError` with body in message |
| T-620 | unit | Network timeout → `RedditAPIError` (timeout configured per call) |
| T-621 | unit | User-Agent header present on all requests |
| T-622 | unit | Author field null (deleted user) handled (returns `author=None`) |
| T-623 | unit | Truncated comment body (`[comment too long]` from Reddit's MoreComments) not returned as a top-level comment |

### workspace_assessor.py

| ID | Level | Scenario |
|---|---|---|
| T-701 | unit | `create_workspace` calls SDK `workspaces.create` with name+runtime |
| T-702 | unit | `wait_for_active` polls until phase=="Active" |
| T-703 | unit | `wait_for_active` raises `WorkspaceNotActive` after `WORKSPACE_READY_TIMEOUT` |
| T-704 | unit | `wait_for_active` returns immediately if already Active on first poll |
| T-705 | unit | `wait_for_active` raises `WorkspaceError` if phase enters "Failed" |
| T-706 | unit | `health_check` returns True when Active |
| T-707 | unit | `health_check` returns False when phase is anything else (Suspending/Suspended/Terminating/etc.) |
| T-708 | unit | `create_session` calls `sessions.ensure`, returns session ID |
| T-709 | unit | `classify` substitutes all placeholders, sends message, parses JSON response |
| T-710 | unit | `classify` raises `AssessmentParseError` when response is non-JSON prose |
| T-711 | unit | `classify` parses response wrapped in ```json fences |
| T-712 | unit | `classify` parses response with prose prefix before JSON |
| T-713 | unit | `classify` raises `AssessmentParseError` when JSON missing required keys |
| T-714 | unit | `classify` raises `AssessmentParseError` when `category` value is unknown |
| T-715 | unit | `assess` substitutes all placeholders, sends message, parses JSON |
| T-716 | unit | `assess` raises `AssessmentParseError` on malformed assessment JSON |
| T-717 | unit | `assess` raises `AssessmentParseError` on out-of-range score (0, 6, -1) |
| T-718 | unit | `assess` accepts null metadata fields (e.g. clone failed mid-step) |
| T-719 | unit | `delete_workspace` calls SDK delete; logs on failure, does not raise |
| T-720 | unit | `delete_workspace` no-op when workspace_id is None |
| T-721 | unit | Prompt template missing placeholder → `ConfigError` at first use (fail-fast) |
| T-722 | unit | Prompt template with extra placeholder not in code → ignored (warn) |
| T-723 | unit | Empty agent response → `AssessmentParseError` |
| T-724 | unit | Agent response with only whitespace → `AssessmentParseError` |

### comment_formatter.py

| ID | Level | Scenario |
|---|---|---|
| T-801 | unit | Happy path: produces non-empty markdown with all sections |
| T-802 | unit | Output mentions baseline name in headers and tables |
| T-803 | unit | Score-delta column shows `+`, `=`, `-` correctly vs baseline |
| T-804 | unit | `key_concerns` rendered as bulleted list |
| T-805 | unit | `key_strengths` rendered as bulleted list |
| T-806 | unit | `tldr` rendered as single line |
| T-807 | unit | Footer contains all three configured URLs (source, issues, LSS) |
| T-808 | unit | AI usage level rendered with associated evidence |
| T-809 | unit | Output ≤10000 chars when input is reasonable |
| T-810 | unit | Truncation triggered when output >10000: evidence fields shortened |
| T-811 | unit | Truncation fallback drops Strengths section if still over |
| T-812 | unit | Truncation fallback drops Security table if still over |
| T-813 | unit | Hard abort (raise) when truncated output <2000 chars |
| T-814 | unit | All 5 AI usage levels render correctly |
| T-815 | unit | Null metadata fields render as "—" not "None" |
| T-816 | unit | Security scores render all three rows |
| T-817 | property | For any valid `Assessment`: output is valid Reddit markdown (no unescaped pipe in table cells, no unterminated code fences) |
| T-818 | property | For any valid `Assessment`: re-formatting the output of `format_comment` parsed back (best-effort) yields the same scores |

### main.py — integration

| ID | Level | Scenario |
|---|---|---|
| T-901 | integration | Empty poll (no new posts) → no workspace created, summary `posts_polled=0` |
| T-902 | integration | All posts filtered out by flair → no workspace created |
| T-903 | integration | All posts filtered out by age → no workspace created |
| T-904 | integration | All posts filtered out (no github url) → no workspace created |
| T-905 | integration | Post already decided → skipped without workspace lookup |
| T-906 | integration | Post in-flight (state from prior crash) → skipped, stale entry pruned |
| T-907 | integration | Post already has bot reply → skipped |
| T-908 | integration | Post qualifies but no sticky found (non-shadow) → skipped, state NOT marked (will retry next poll) |
| T-909 | integration | Post qualifies, sticky found, classifier says not-announcement → marked NOT_ANNOUNCEMENT, no comment |
| T-910 | integration | Full happy path: classify→assess→format→post under sticky → marked POSTED |
| T-911 | integration | Happy path in DRY_RUN → no Reddit reply call, marked POSTED is NOT set; instead decision remains absent (or `dry_run` decision added) |
| T-912 | integration | Happy path in SHADOW_MODE → submit_text_post + distinguish (optional) + reply under simulated sticky; marked SHADOW_POSTED; shadow_mapping recorded |
| T-913 | integration | Shadow post creation fails (4xx from Reddit) → marked ERROR, no reply attempted |
| T-914 | integration | Classifier returns unparseable JSON → marked ERROR, no assess call, session abandoned |
| T-915 | integration | Assess returns unparseable JSON → marked ERROR, no comment |
| T-916 | integration | Workspace create fails → no posts marked ERROR (workspace is run-level); run aborts with summary error |
| T-917 | integration | Workspace stuck not-Active after timeout → run aborts, no posts touched, summary records error |
| T-918 | integration | Workspace dies mid-run (health check fails) → in-flight posts marked ERROR, workspace deletion attempted |
| T-919 | integration | Just-before-post race check catches duplicate (bot reply appeared between assess and post) → marked POSTED anyway (idempotent) but no duplicate comment created |
| T-920 | integration | Reddit 429 mid-run → backoff, retry up to max; on exhaustion, abort run |
| T-921 | integration | Reddit 5xx on get_new → retry once; on failure, abort run with no posts processed |
| T-922 | integration | Concurrency: 5 qualifying posts, `WORKSPACE_SESSION_CONCURRENCY=2` → 2 sessions in parallel, 3 queued; total wall-clock ≈ 3 × per-session time |
| T-923 | integration | State prune called at start of run, removes entries older than `STATE_PRUNE_HOURS` |
| T-924 | integration | Crashed-pod recovery: stale in-flight from prior run triggers workspace delete attempt |
| T-925 | integration | Workspace always deleted at end of run (success and failure paths) |
| T-926 | integration | Metrics emitted for every counter and histogram on happy path |
| T-927 | integration | Metrics emitted on error paths |
| T-928 | integration | `posts_filtered` breakdown sums to `posts_polled - posts_classified_total` |
| T-929 | integration | RunSummary.errors capped at 20 |
| T-930 | integration | Title truncation (300 char limit) works in shadow mode |

### main.py — adversarial / property

| ID | Level | Scenario |
|---|---|---|
| T-931 | property | Two consecutive `_run()` invocations on the same poll window produce no duplicate posts (idempotency) |
| T-932 | property | For any sequence of Reddit poll responses, no post is ever replied to twice across runs |
| T-933 | property | Workspace is always deleted if created (no leak path, including exception paths) |
| T-934 | property | State store contains no entries newer than `started_at - 1s` after a run (no future-dated writes) |
| T-935 | adversarial | Post with selftext containing 50 github URLs → still processed once, only first URL used for assessment |
| T-936 | adversarial | Post with selftext containing the bot's own username → not treated as bot reply |
| T-937 | adversarial | Post with title >300 chars (shouldn't happen from Reddit but test defensive code) → no crash |
| T-938 | adversarial | Classifier returns extra unknown JSON keys → ignored, not raise |
| T-939 | adversarial | Classifier returns `is_announcement: "true"` (string) → rejected as parse error |
| T-940 | adversarial | Assessment with score=0 → rejected; score=6 → rejected; score=3.5 → rejected (must be int) |
| T-941 | adversarial | Assessment with all-null metadata → accepted (clone failed), formatted with "—" placeholders |
| T-942 | adversarial | Workspace stuck in `Failed` phase → `wait_for_active` raises immediately, no timeout wait |
| T-943 | adversarial | Network outage mid-run → all in-flight posts marked ERROR, run returns (does not hang) |

## Live tests (opt-in)

| ID | Level | Scenario | Env gate |
|---|---|---|---|
| T-L1 | live | Postgres contract suite | `TEST_POSTGRES_URL` |
| T-L2 | live | Reddit OAuth + `get_new` against r/selfhosted (read-only) | `TEST_LIVE_REDDIT` (sets client creds) |
| T-L3 | live | LLMSafeSpaces workspace create→active→session→message→delete | `TEST_LIVE_LSS` (sets URL + key) |
| T-L4 | live | End-to-end against a synthetic post in a private test sub | `TEST_LIVE_FULL` (all of the above + target sub) |

Live tests run with `pytest -m live`. Default `pytest` skips them. Markers configured in `pytest.ini`.

## Execution model

- **CI (GitHub Actions via `task local-build-repo-assessor`)**: unit + contract (JSON + SQLite only) + integration + property. Must pass on every push.
- **Manual pre-release**: above + `pytest -m live` with real creds in env. Operator decision to merge.
- **Local dev**: same as CI; Postgres unit tests use mocked psycopg (fast).

## Test fixtures (conftest.py)

- `tmp_state_path` — fresh temp dir for JSON/SQLite state files, cleaned up after each test
- `mock_reddit` — `responses`-mocked `https://oauth.reddit.com/*` endpoints
- `mock_lss_client` — `unittest.mock.MagicMock` of the LLMSafeSpaces SDK client, with sensible defaults
- `sample_assessment` — full `Assessment` dataclass instance with realistic values
- `sample_classification_announcement` / `_not_announcement` — for parameterisation
- `sample_baseline` — Sonarr profile
- `sample_submission` — typical `RedditSubmission` (parameterised: link post, text post, missing flair, old, new)
- `fake_clock` — `freezegun` or manual now-injection, for time-based logic (stale in-flight, prune, age filter)
- `cap_error_log` — fixture capturing structured logs for assertion

## Definition of done (per module)

A module is "done" when:

1. Every row in its test section above passes.
2. Property tests for that module pass on 1000 generated inputs (Hypothesis or hand-rolled).
3. No `# TODO`, no `pass`, no `...`, no `raise NotImplementedError` in the module.
4. `mypy --strict` (or equivalent) passes on the module.
5. Adversarial self-review (README-LLM rule 11) documented in `ADVERSARIAL.md` with zero real findings.

## Definition of done (whole function)

1. All test sections green.
2. `task local-build-repo-assessor` produces a deployable zip.
3. `ASSUMPTIONS.md` records every assumption with validation status (rule 7).
4. `ADVERSARIAL.md` records the 3-phase review (rule 11).
5. README documents deployment steps (env vars, Fission env, secrets, PVC, metrics scraping).
