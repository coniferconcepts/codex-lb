## 1. Budget helpers

- [x] 1.1 Add `app.core.startup_budget` with listen-timeout parsing, remaining-budget clock, typed `fail_startup` reasons, and `StartupBudgetExceeded`.
- [x] 1.2 Wrap `init_db` so a blocking startup cannot overrun the remaining listen budget.

## 2. SQLite and leader lease

- [x] 2.1 Cap SQLite busy waits and the migration lock to the remaining pre-listen budget.
- [x] 2.2 Bound leader-lease acquire during pre-listen so a hung SQLite write cannot block past the budget.

## 3. Verification

- [x] 3.1 Keep existing `listen_timeout` CLI watcher tests green.
- [x] 3.2 Add unit coverage for typed `db_migrate` / `startup_budget` vs `listen_timeout`, and for contended SQLite not waiting out 30s.
