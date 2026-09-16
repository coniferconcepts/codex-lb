https://github.com/coniferconcepts/codex-lb/issues/7

# 02 — after TCP listen, SQLite/leader-election loop starvation hangs /health/live while KeepAlive holds the process

**Status:** shipped (2026-09-16)

Shipped in `9aa26fe4` (skip post-listen PRAGMA / `CODEX_LB_SKIP_POST_LISTEN_SQLITE`) plus yield-before-purge. Router watchdog no longer one-shot-latches for 15 minutes; LaunchAgent `ExitTimeOut` is 60 in opencode-router (was 30). Do not re-implement launchd here.

**What to build:** A laptop sidecar that has already bound `127.0.0.1:2455` must still answer `GET /health/live` with HTTP 200, or exit with a typed reason, instead of sitting in launchd as `owned` while every live probe times out.

Issue 7 / ticket 01 and PR #8 (`fix/listen-timeout-startup-budget`, merged on `upgrade/v1.24.0`) cover the **pre-bind** window: defer large SQLite integrity/drift checks, emit `category=fail_startup` when pre-yield work exceeds `CODEX_LB_LISTEN_TIMEOUT_SECONDS`, and stop the opaque `listen_timeout` KeepAlive loop when the port never opens. That is not enough on this host.

Observed 2026-09-14 on LaunchAgent `com.opencode.codex-lb` running `/Users/benjaminerb/CODE/codex-lb` at `0343208f` (the PR #8 commits), listen timeout 300s, one operator `--load`, no second restart:

- stderr: Alembic already at head; "Deferring schema drift check until after listen"; bridge ring registered; then `sqlite_wedged_teardown phase=rollback` (issue #1682); `event_loop_lag` (up to ~86s in an earlier serving window; ~1s+ during this start); `Leader lease renewal timed out`.
- `watch_listen_or_timeout` only checks TCP `connect_ex`. Once the port is open it returns `listening` and the startup-budget thread stops (`mark_pre_listen_complete` after lifespan startup in `app/main.py`).
- curl `/health/live` then returns `curl (28)` with 0 bytes (TCP up, HTTP never completes). launchd still shows the job owned. Router watchdog counts live failures and latches after one `--restart`; it cannot distinguish this from a down listener.

A concurrent ChatGPT/Codex http_bridge window earlier the same day showed the same loop-lag → SQLite wedge → lease timeout → launchd SIGKILL (`ExitTimeOut` 30) path. Pinning `CODEX_LB_UPSTREAM_STREAM_TRANSPORT=http` on the LaunchAgent is a client/upstream mitigation, not a fix for a wedged loop after bind.

**Blocked by:** None — ticket 01 pre-bind budget shipped in PR #8; this slice shipped in `9aa26fe4`.

**Status:** shipped

- [x] After a successful TCP bind, `GET /health/live` returns a non-empty HTTP 2xx within a named budget (listen timeout or an explicit post-listen live budget) on this host's existing `store.db`, not only on an empty database.
- [x] If the event loop cannot serve that probe because SQLite teardown/leader-lease renewal is starved, the process exits with a typed stderr category (`fail_startup` or a new post-listen reason) instead of remaining launchd-owned with a hung live endpoint.
- [x] `watch_listen_or_timeout` (or a sibling watch) must not treat TCP listen as proof that live is healthy; TCP-up + live-timeout is a fail, not success.
- [x] A wedged writer (issue #1682 reclaim) cannot leave leader-lease heartbeats unable to renew until live hangs for many minutes.
- [x] Router `GET /health/live` from the liveness watchdog either sees 2xx or a dead/exited process — not an owned PID that never answers.
