---
title: Operator pointer + close shipped sidecar tickets
status: PLAN
date: 2026-09-16
owner: codex-lb
ssot_app: this repo (lifespan, /health/live, SQLite)
ssot_launchd: ~/CODE/opencode-router/docs/CODEX_LB_SIDECAR.md
---

# Operator pointer (do not copy launchd)

Runtime already matches 2026-09-16 learnings: import-before-listen, yield
before HTTP, `/health/live` no-DB, `CODEX_LB_SKIP_POST_LISTEN_SQLITE`.
This repo must **not** grow a second LaunchAgent/watchdog runbook.

`codex-lb-upgrade` is a linked worktree **behind** import/yield/skip-post-listen
commits. Do not treat it as current SoT.

## Changes

1. **`docs/troubleshooting.md`**: one short “LaunchAgent / laptop sidecar ops”
   pointer to `opencode-router` `docs/CODEX_LB_SIDECAR.md`. Explicit: do not
   WAL-checkpoint as first repair; env-parse hangs are the **router launcher**,
   not `app.cli`.
2. **`docs/reference/settings.md`** (or startup note): document
   `CODEX_LB_SKIP_POST_LISTEN_SQLITE` as implemented here, defaulted by the
   **router** sidecar launchers.
3. **Tickets**: close or retarget local `.omo` tickets `01-listen-timeout-startup-hang`
   and `02-post-listen-sqlite-loop-starves-live` against commits `1a035e80`,
   `f1daf21b`, `9aa26fe4`. Note `ExitTimeOut` 30 was a **router** plist issue,
   now 60 in the router template — do not re-implement ExitTimeOut in this repo.
4. Do not add `timeout_exec.pl`, watchdog grace, or plist `ExitTimeOut` here.

## Out of scope

- Copying `codex-lb-liveness-watchdog.sh` or LaunchAgent templates
- WAL-checkpoint as a documented restart remedy
- 15-minute / 900s latch language
- Treating `codex-lb-upgrade` as the live tree for this topic

## Acceptance

- [x] Public troubleshooting has a router pointer and no WAL-first repair
- [x] Skip-post-listen flag is discoverable in app docs
- [x] Tickets 01/02 are not still `ready-for-agent` with open checkboxes for shipped work
