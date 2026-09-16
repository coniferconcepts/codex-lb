https://github.com/coniferconcepts/codex-lb/issues/7

# 01 — startup: listen_timeout KeepAlive loop when lifespan/SQLite/leader-election exceeds 90s

**Status:** shipped (2026-09-16)

Shipped in `1a035e80` / `f1daf21b` (import-before-listen, yield-before-HTTP). LaunchAgent `ExitTimeOut` 30 was a **router** plist issue (now 60 in opencode-router). Do not re-implement ExitTimeOut here.

**What to build:** A single-process laptop sidecar start that cannot wedge in SQLite or leader-election past the listen timeout. Today a macOS LaunchAgent KeepAlive retries a process that sometimes never binds the listen port within `CODEX_LB_LISTEN_TIMEOUT_SECONDS` (default 90s), dying with `category=fail_startup reason=listen_timeout`. The hang is before listen. Suspected surface: CLI listen-timeout watcher (`app/cli.py`), app lifespan `init_db` (`app/main.py`), SQLite `busy_timeout`, and leader-election lease acquire/heartbeat (`app/core/scheduling/leader_election.py`). Drain-guard and grok-global-config preflight are not the cause.

If migration or the leader lease cannot finish in time, the process must fail with a typed startup reason instead of an opaque `listen_timeout` KeepAlive loop. After a successful bind, `/health/live` must return 200 within the listen budget on a clean SQLite database and on a contended SQLite file.

**Blocked by:** None.

- [x] A single-process laptop start cannot remain wedged in SQLite work or leader-election past the listen timeout (`CODEX_LB_LISTEN_TIMEOUT_SECONDS`, default 90s).
- [x] If migration or leader-lease work cannot complete within that budget, the process exits with a typed startup failure reason rather than `listen_timeout` that KeepAlive retries as an opaque loop.
- [x] After a successful bind on a clean SQLite database, `/health/live` returns 200 within the listen budget.
- [x] After a successful bind on a contended SQLite file, `/health/live` returns 200 within the listen budget.
