# Harden frontend Bun install policy ordering

Frontend container installs must receive the frontend Bun and npm supply-chain policy files before dependency installation. This keeps `ignoreScripts`, release-age, and npm audit policy effective in every supported Docker build path.

## Scope

- Copy `frontend/bunfig.toml` and `frontend/.npmrc` before `bun install` in both root-context Dockerfiles.
- Copy `bunfig.toml` and `.npmrc` before `bun install` in the Compose inline Dockerfile, whose context is `./frontend`.
- Keep the existing Python stages, ports, and unrelated build behavior unchanged.
