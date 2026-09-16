# Troubleshooting

## LaunchAgent / laptop sidecar

On this operator stack, macOS LaunchAgent supervision (watchdog grace, env-parse
timeout, `ExitTimeOut`) lives in sibling **opencode-router**:
[`docs/CODEX_LB_SIDECAR.md`](https://github.com/coniferconcepts/opencode-router/blob/development/docs/CODEX_LB_SIDECAR.md)
(local: `~/CODE/opencode-router/docs/CODEX_LB_SIDECAR.md`).

- Do not WAL-checkpoint as the first repair for `database is locked` during a
  restart; that was overlapping writer versus teardown.
- Env-parse hangs are the **router launcher** (`timeout_exec.pl`), not `app.cli`.
- `CODEX_LB_SKIP_POST_LISTEN_SQLITE=1` skips post-listen integrity/drift so
  `/health/live` stays on-loop. Router sidecar launchers default this on; the
  app reads the env in `app/core/startup_budget.py`.

## Usage and quota

**Why does codex-lb still say `rate_limited` when Codex Desktop says the window reset?**
codex-lb refreshes usage on its own schedule and treats upstream samples conservatively. The full policy — refresh cadence, expiry, and why displays can briefly disagree with upstream — is documented in the
[usage refresh policy context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/usage-refresh-policy/context.md).

## Streaming

**Codex CLI falls back to POST instead of WebSockets.**
Run the [WebSocket verification steps](client-setup.md#verify-websocket-transport). If codex-lb sits behind a reverse proxy, make sure it forwards WebSocket upgrades — see [Remote Access](deployment/remote.md).

## Fast Mode, Ultrafast, and service tiers

Fast Mode, Ultrafast, and service-tier behavior is documented in the
[Responses API compatibility context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/responses-api-compat/context.md#fast-mode-and-service-tiers).

## Old Codex sessions missing after migrating

`codex resume` filters by `model_provider` — re-tag old sessions with the built-in retag command. See
[session retagging](client-setup.md#migrating-from-direct-openai-session-retagging).

---

*Spec: [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy)*
