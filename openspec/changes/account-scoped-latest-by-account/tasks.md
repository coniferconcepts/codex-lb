## 1. Implementation

- [x] 1.1 Add optional account-ID filtering to primary usage latest-by-account queries before latest-row selection on SQLite and PostgreSQL.
- [x] 1.2 Pass known account IDs from the accounts and dashboard services to their latest usage reads.
- [x] 1.3 Preserve unfiltered usage-refresh and proxy behavior.

## 2. Tests

- [x] 2.1 Add a SQLite compiled-SQL regression test for pre-window account filtering and deterministic ordering.
- [x] 2.2 Add an end-to-end accounts endpoint test with interleaved usage data and an account without usage rows.
- [x] 2.3 Add filtered/unfiltered equivalence coverage for primary and additional usage repositories.

## 3. Validation

- [x] 3.1 Run OpenSpec validation, Ruff, ty, and the targeted pytest suite.
