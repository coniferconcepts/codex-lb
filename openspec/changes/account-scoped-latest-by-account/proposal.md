## Why

The accounts endpoint asks for the latest usage rows after it has already loaded the active account set, but the latest-by-account queries rank every historical row before selecting the latest row. This makes account listing scale with the full usage-history tables and can cause the endpoint to hang on larger installations.

## What Changes

- Scope primary and secondary usage latest-by-account queries to the known account IDs before latest-row selection.
- Scope account-list and dashboard latest usage reads to the accounts already loaded by those services.
- Scope additional usage latest-row reads in the account-list service to its known account IDs.
- Preserve the existing latest-row ordering, returned rows, and unfiltered refresh semantics.

## Impact

- Reduces the rows considered by account-list and dashboard usage queries on SQLite and PostgreSQL.
- Keeps global usage-refresh and proxy lanes unchanged.
- Adds regression coverage for SQL shape, endpoint behavior, and filtered/unfiltered result equivalence.
