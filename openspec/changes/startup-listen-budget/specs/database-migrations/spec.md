## ADDED Requirements

### Requirement: Startup database work honors the listen budget

When the CLI listen watcher is counting down (`CODEX_LB_LISTEN_TIMEOUT_SECONDS`), startup database work (SQLite integrity checks, Alembic upgrade, and the cross-process migration lock) MUST finish or fail before that deadline, minus a short margin so the process can emit a typed `category=fail_startup` reason and exit. The default listen timeout MUST remain unchanged unless an operator sets the env var. A migration or integrity hang MUST NOT surface only as opaque `reason=listen_timeout`.

#### Scenario: Init DB exceeds remaining listen budget

- **GIVEN** the listen watcher is active with a remaining pre-listen budget
- **WHEN** `init_db` does not complete inside that budget
- **THEN** startup fails with `category=fail_startup reason=db_migrate`
- **AND** it does not wait for the listen watcher to print `reason=listen_timeout`

#### Scenario: Contended SQLite cannot stack the full busy timeout

- **GIVEN** another connection holds an exclusive write lock on the SQLite file
- **AND** the remaining listen budget is shorter than the ordinary 30s busy timeout
- **WHEN** startup integrity or migration work waits on that lock
- **THEN** the wait is capped to the remaining budget
- **AND** the process fails or proceeds inside that budget instead of stacking 30s waits until listen timeout
