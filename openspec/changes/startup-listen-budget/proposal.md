## Why

A laptop LaunchAgent KeepAlive retry can loop on `category=fail_startup reason=listen_timeout` when lifespan work (SQLite integrity, Alembic, migration lock, leader-lease acquire) never binds `:2455` inside `CODEX_LB_LISTEN_TIMEOUT_SECONDS`. The hang is before listen. KeepAlive then has no distinct signal that startup work overran a budget.

## What Changes

- Bound pre-listen lifespan work—especially `init_db`—to the remaining listen timeout minus a short watcher-exit margin.
- If that budget fires, exit with a typed `category=fail_startup reason=` other than `listen_timeout` (`startup_budget`, `db_migrate`, or `leader_lease`).
- Cap SQLite busy waits and the startup migration lock to the remaining budget so contended SQLite cannot stack 30s `busy_timeout` (or 300s lock waits) until the opaque listen watcher fires.
- Leave the CLI listen watcher in place as a last-resort `listen_timeout` when the server never binds. Do not change the default listen timeout (30s in code; operators may set 90s via env).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: Startup migrations and SQLite integrity work must finish or fail inside the listen budget with a typed startup reason.
- `replica-operations`: Single-process SQLite startup must not remain wedged in leader-lease acquire past the listen budget.

## Impact

Limited to CLI listen-watch clock sharing, lifespan `init_db` wrapping, SQLite/Alembic connect timeouts during pre-listen, and leader-lease acquire bounding before bind. No default listen-timeout change, no KeepAlive/LaunchAgent changes, no multi-replica PostgreSQL topology change.
