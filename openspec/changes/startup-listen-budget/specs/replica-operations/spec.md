## ADDED Requirements

### Requirement: Pre-listen leader-lease acquire is budgeted on SQLite

On a single-process SQLite start, acquiring the scheduler leader lease MUST NOT remain wedged in SQLite past the remaining listen budget. If the acquire cannot finish in time, the process MUST skip leader-gated work for that attempt (treat as non-leader) or exit with `category=fail_startup reason=leader_lease` rather than an opaque `listen_timeout`. Losing a few seconds of scheduler start after bind is acceptable. After the server may bind, ordinary lease acquire/renewal timeouts apply.

#### Scenario: Leader acquire hangs on SQLite before listen

- **GIVEN** the listen watcher is still counting down
- **AND** leader-lease acquire is blocked on SQLite
- **WHEN** the remaining pre-listen budget expires
- **THEN** the acquire is abandoned
- **AND** startup does not wait out the full SQLite busy timeout before bind
