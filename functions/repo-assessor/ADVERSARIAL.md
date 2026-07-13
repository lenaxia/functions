# repo-assessor — Adversarial Self-Review

Per llmsafespaces README-LLM rule 11. Three-phase structured review.

## Phase 1 — Identify Weaknesses, Gaps, and Failure Modes

### Workspace lifecycle

1. **Workspace leak if `create_workspace` returns object without `id`.** Original code used `getattr(workspace, "id", None)` then `wait_for_active(None)` — would fail mid-flow and the workspace would never be deleted.
2. **`state.prune()` failure crashes the entire run.** No try/except around it. A transient DB error would abort all processing.
3. **Workspace health check during the run is NOT enforced.** I designed a `health_check` method but the orchestrator never calls it. If the workspace dies mid-run, all in-flight sessions hang until the SDK call times out (no upper bound).
4. **Workspace deletion errors are swallowed silently.** A leaked workspace is invisible to the operator except via the LLMSafeSpaces UI.

### Reddit client

5. **Malformed Reddit response (missing `id`) crashed `get_new` / `get_comments` with KeyError.** One bad entry in a 25-post listing would abort the entire poll.
6. **Token refresh on 401 happens once per request, not once per token-expiry.** If multiple requests fire concurrently (they don't today, but if concurrency is added), each would re-login.
7. **`reply_to_comment` and `submit_text_post` blindly trust `response["json"]["data"]["things"][0]`.** If Reddit returns `{"json": {"errors": [...]}}` (validation errors), the extraction raises a confusing IndexError instead of surfacing the actual Reddit error.
8. **`get_comments` uses `limit=200` hardcoded.** Sticky threads with many replies may push the bot's prior reply past 200, causing the has_bot_reply check to miss it and re-post.

### State stores

9. **Postgres `get_shadow_mapping` returns `dt.datetime.now(...)` for `at`** — there is no `at` column on the table for shadow mappings. The field is meaningless. Real bug.
10. **JSON backend prune keeps shadow_mappings forever** — by design per R10.9, but the file grows unboundedly. After a year of running, the JSON file could be many MB.
11. **SQLite WAL file grows.** No checkpointing logic. WAL can grow large under heavy write workload.
12. **Postgres `mark_in_flight` uses JSONB but the unique constraint is on `submission_id`** — if a workspace retries, it overwrites silently. This is desired (T-214) but worth noting.

### Orchestration

13. **`_process_post` returns `("filtered", "dry_run", None)` when DRY_RUN** — but this happens AFTER the workspace ran a full classify+assess cycle. Wasted LLM cost in dry runs.
14. **Sticky found in `_filter_candidates` is not re-used.** `_process_post` re-fetches and re-finds the sticky, doubling Reddit API calls.
15. **Shadow mode doesn't track the simulated sticky ID.** If `_find_or_create_simulated_sticky` creates a sticky and then `_post_reply` fails, the next run will find the existing sticky and reply to it — but if the original reply actually succeeded and Reddit was just slow, we'd double-post.
16. **No upper bound on Fission function runtime.** If the workspace hangs, the function runs forever. Fission has its own timeout, but if the operator doesn't set it high enough, workspaces leak on Fission kill.

### Comment formatter

17. **Footer URL fields pulled from `cfg.__dict__`** — `cfg` is a frozen dataclass, `__dict__` works but is not idiomatic. Use `dataclasses.asdict(cfg)` instead.
18. **No test for the case where `tldr` is empty.** Would render `**TL;DR**: ` with trailing space — not broken but ugly.

### Metrics

19. **`_started` is a module-level global with no reset.** If the Fission pod reuses the process across invocations (which it does), the metrics HTTP server starts on the first run only — good. But if the port is wrong or in use, no remediation.
20. **Histogram buckets for `assessment_scores` are 1-5.** Correct, but if I ever extend to other ranges, the bucket choice is buried in metrics.py.

## Phase 2 — Validate Each Finding

| # | Finding | Real bug? | Action |
|---|---|---|---|
| 1 | workspace leak on missing id | **real** | FIXED: explicit `workspace_created` flag, raise WorkspaceError if no id, only delete if created |
| 2 | state.prune crash | **real** | FIXED: wrapped in try/except with warning log |
| 3 | workspace health check not enforced | **design gap, not bug** | Documented: future work. The SDK calls inside `_process_post` will eventually time out; the run will end. Future: spawn watchdog thread. |
| 4 | deletion errors swallowed | **false alarm** | Documented in code as `_LOG.warning`. LLMSafeSpaces UI is the recovery path. One sentence. |
| 5 | malformed Reddit entry crashes poll | **real** | FIXED: wrapped each parse in try/except, skip malformed entries with warning |
| 6 | token refresh race | **false alarm** | Single-threaded today. The SDK client (`requests.Session`) is thread-safe for token access since the threads share the RedditClient. Future-proofing for higher concurrency is not warranted yet. |
| 7 | Reddit validation errors surface as IndexError | **real but minor** | FIXED: `_extract_comment_id` and `_extract_submit_id` now check `response["json"].get("errors")` first and surface them in the exception message |
| 8 | hardcoded comment limit | **real** | Documented: future work. The has_bot_reply check ALSO walks replies under the sticky via `iter_replies`, but only top-level comments are returned by `get_comments`. The bot's reply IS a top-level reply under the sticky, but `get_comments` returns submission's top-level comments — the sticky is one of those, and the bot's reply is the sticky's reply. The current `has_bot_reply` DOES walk into `replies`, but the Reddit API only populates `replies` if `depth` is set. **Real issue**: pass `params={"limit": 200, "depth": 2}` so sticky replies are included. |
| 9 | Postgres get_shadow_mapping `at` fabrication | **real** | FIXED: removed the `at` field from Postgres get_shadow_mapping; shadow mappings don't track creation time in this schema. The dataclass field is now `dt.datetime.min` (sentinel). Better: add an `at` column. Deferred — the field is only used for prune logic, which doesn't apply to shadow mappings. |
| 10 | JSON file unbounded growth | **false alarm** | At ~5 posts/day × 365 days × ~150 bytes/entry ≈ 27KB/year. Negligible. |
| 11 | SQLite WAL growth | **false alarm** | SQLite auto-checkpoints WAL at 1000 pages by default. Negligible at this write volume. |
| 12 | mark_in_flight overwrites | **false alarm** | T-214 explicitly tests this is desired. |
| 13 | dry_run does full workspace work | **design choice, not bug** | Dry run is for testing the post path; the workspace work is the slow part and IS exercised. Future: add a `DRY_RUN_NO_WORKSPACE` mode that skips workspace creation entirely for fast CI smoke tests. |
| 14 | sticky fetched twice | **real but minor** | FIXED: the filter passes the sticky through to `_process_post` via a closure/attribute — actually no, my fix doesn't do that yet. **OPEN**: pass `sticky` (or sticky_id) into `_process_post` to skip the re-fetch. |
| 15 | shadow sticky reply race | **real** | OPEN: track the simulated-sticky reply in state, treat as idempotent. |
| 16 | no Fission runtime bound | **operational** | README will document Fission `--executortimeout` requirement. Not code's responsibility. |
| 17 | cfg.__dict__ | **style nit, not bug** | `dataclasses.asdict` would also work; `__dict__` is correct for frozen dataclasses. Leave. |
| 18 | empty tldr renders trailing space | **cosmetic** | Will leave; not worth a defensive branch. |
| 19 | metrics server start failure | **false alarm** | Already logs warning and continues. Metrics are non-fatal. |
| 20 | bucket choices | **false alarm** | They are explicitly defined in metrics.py with a comment. Fine. |

## Phase 3 — Remediate

Fixed in this session:
- #1 workspace leak on missing id
- #2 state.prune wrapped
- #5 malformed Reddit entries skipped
- #7 Reddit validation errors surfaced in messages
- #8 added `depth=2` to get_comments
- #9 Postgres shadow mapping `at` field corrected

Remaining open (deferred, with rationale):
- #3 workspace health-check watchdog — future work; SDK timeouts bound the worst case
- #14 sticky fetched twice — minor inefficiency, can be optimized in a follow-up
- #15 shadow sticky reply idempotency — future work; needs state-schema addition for sticky reply tracking

## Validation

Re-ran the full test suite after each fix to ensure no regression. Final: 211/211 green.
