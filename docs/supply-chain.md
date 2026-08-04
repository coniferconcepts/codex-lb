# Supply-chain install policy

- Lifecycle scripts disabled (`bunfig.toml` `ignoreScripts = true`).
- Bun holds new releases **5 days** (`minimumReleaseAge = 432000` seconds).
- CI must use `bun install --frozen-lockfile`.
- One-off scripts: `bun install --ignore-scripts=false` after review.
