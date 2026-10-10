## Tasks
- [x] Add failing estimator, enforcement, validation, and fake-upstream route tests.
- [x] Implement local streaming and collected-response enforcement.
- [x] Update compatibility requirements and estimate context.
- [ ] Run requested tests, lint, type checking, and spec validation.

Tests, lint, and type checking pass. See [verification](verification.md) for the formatting baseline and blocked spec validation and review.

## Review corrections
- [x] Add failing regressions for whole function calls, terminal draining, wrapper closure, full-output consistency, and normalized final answers.
- [x] Buffer function calls and drop oversized calls without partial arguments.
- [x] Drain after the cap and preserve terminal usage and settlement.
- [x] Propagate disconnect closure through all wrappers.
- [x] Bound done events and full-output snapshots without double counting.
- [x] Apply the cap after public normalization for streamed and collected output.
- [x] Run the four requested test suites, Ruff lint, changed-file formatting, type checking, and diff whitespace verification; record exits.
- [ ] Complete repository formatting, OpenSpec CLI validation, and read-only Sol review; see environment blockers in verification.md.

## PR #11 P2 disconnect follow-up
- [ ] Reproduce upstream closure failure through the public service with real retry and attempt generators, stubbing only upstream transport.
- [ ] Close each attempt generator and its upstream iterator on downstream closure.
- [ ] Run output-cap and proxy unit tests, pinned Ruff checks, and repository type checking; record before and after exits.
