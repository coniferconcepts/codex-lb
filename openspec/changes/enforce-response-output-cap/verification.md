# Output-cap review verification

All commands ran from the repository root in the assigned worktree with the existing environment. No dependencies, commits, pushes, `.codex` changes, or `.omo` writes were made. Request-model changes from the original uncommitted change were preserved.

## Failing regressions before fixes

The initial regression command was `.venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py -k review_`. It exited 1 with all 20 new cases failing for behavioral reasons.

| Finding | Regression | Failure before the fix |
| --- | --- | --- |
| P1: whole function calls | `test_review_oversized_function_call_is_never_emitted`, `test_review_function_call_events_wait_for_complete_item` | Arguments were cut mid-call; call events escaped before item completion. |
| P1: terminal draining | `test_review_cap_drains_terminal_usage_and_settlement` | Upstream stopped before terminal usage and settlement. |
| P2: wrapper closure | `test_review_route_disconnect_closes_inner_stream` | Closing the response iterator left the inner stream open, with or without a cap. |
| P2: full output | `test_review_full_output_events_fit_cap` | Item, part, text-done, and full-response events exposed the uncapped text. |
| P2: normalization order | `test_review_route_caps_normalized_final_answer` | Streamed and collected `final_answer.text` bypassed the cap, whether supplied through an item event or terminal output. |

ASGI disconnect coverage then failed before response cleanup: `.venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py -k disconnect --tb=short` exited 1 with eight failures and four passing iterator cases. After the fix, the same command exited 0 with all 12 cases passing. The tests exercise ASGI 2.0 disconnect cancellation and ASGI 2.4 send failures.

The settlement test was strengthened to require upstream settlement before the incomplete terminal event is relayed. `.venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py -k drains_terminal --tb=short` exited 1 before deferred terminal emission. It passes in the final suite.

Final self-review added three failing cases for `final_answer` in `response.created` and `response.in_progress` output snapshots, and for an upstream `error` terminal after the cap. `.venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py -k 'full_snapshot or error_terminal' --tb=short` exited 1 with all three cases failing before the fixes. These cases pass in the final suite. Public normalization now covers every full-response output snapshot; error terminals after the cap also wait for settlement before becoming incomplete responses.

## P2/P3 follow-up

The earlier disconnect cases replaced `stream_http_responses` and bypassed the service wrapper. The test now also uses a real `ProxyService` and its public `stream_http_responses` entry point, replacing only the selected inner generator and unrelated settings and headers. The added cases cover retry and HTTP-bridge paths, absent and supplied caps, disconnects after the first or second event, direct iterator closure, ASGI 2.0 cancellation, and ASGI 2.4 send failures.

After correcting a missing dashboard setting in the test fixture, `.venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py -k 'disconnect and not stub' --tb=short` exited 1 with all 24 new cases failing at `assert closed`. The service wrapper then gained a shared `try/finally` that awaits the selected inner generator's `aclose()`. The same command exited 0 with all 24 cases passing. The final four-suite run below also passes with that cleanup in place.

The change context now describes draining through terminal usage and settlement, buffering complete function calls, and dropping oversized calls whole. The local absolute path was removed from this record. The requested privacy scan of every new or changed file found no matches; a separate scan found no local username, home path, or hostname.

## Passing checks after fixes

| Verifier | Exit | Result |
| --- | --- | --- |
| `.venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py tests/unit/test_openai_requests.py tests/unit/test_openai_errors.py tests/unit/test_proxy_api_responses_contract.py --tb=short` | 0 | 159 tests passed after the final code edit, including all 80 output-cap cases. |
| `.venv/bin/ruff check .` | 0 | Repository lint passed. |
| `.venv/bin/ty check` | 0 | Repository type checking passed. |
| `.venv/bin/ruff format --check app/core/openai/output_cap.py app/core/openai/requests.py app/core/openai/v1_requests.py app/modules/proxy/api.py app/modules/proxy/service.py tests/unit/test_response_output_cap.py` | 0 | All six files are formatted. |
| `git diff --check` | 0 | No whitespace errors. |

Additional tests verify that repeated done output counts once, complete calls use the allowance remaining after text, the DONE sentinel follows the incomplete terminal event, and multiple items and content parts share one allowance.

A bounded inline self-review checked the implementation and API diff against all five findings. Output suppression retains emitted items while terminal metadata and authoritative usage continue to update. Oversized function calls never leave the event buffer. Stream closure runs through the response cleanup, prepend wrapper, limiter, and public normalizer. This was self-review, not an independent Sol review.

## Remaining checks

- `.venv/bin/ruff format --check .`: exit 1 on the untouched `tests/unit/test_bootstrap.py`; the same baseline failure existed before this assignment.
- `openspec validate --specs`: exit 127 because OpenSpec CLI is unavailable. No dependency was installed.
- Required read-only Sol-high contract review through `codex-lean-lane security`: exit 1 before review started, including the follow-up attempt scoped to P2/P3. The pinned lane uses `gpt-6.1-sol`, high effort, and a read-only sandbox. CLI initialization failed with `Read-only file system (os error 30)`. No reviewer verdict was produced.

The code corrections are implemented. The full local gate and independent review remain incomplete, so the change is not archived.

## PR #11 P2: real retry-path disconnect regression (blocked)

Added `test_service_disconnect_closes_real_retry_upstream` before changing production code. It creates a fresh encrypted test account in the real repository and calls `stream_responses` or `stream_http_responses` with the bridge disabled. Only `core_stream_responses` is replaced. The test covers absent and present caps and closure after the first or second event. It retains the upstream generator and checks both its cleanup flag and `ag_frame` immediately after outer `aclose()`, before test cleanup.

The regression has not reached its closure assertion in this environment. Database setup hangs in aiosqlite. A standalone in-memory aiosqlite connection also timed out (exit 124 after eight seconds). An existing proxy test hits the same database timeout. This is not evidence of a failing closure assertion. Production code is untouched because the assignment requires a failing regression before the fix, and the operating rules prohibit workarounds.

Run these commands from the assignment worktree. The table records results after adding the test and before any production fix. After-fix results are unavailable.

| Verifier | Exit | Result |
| --- | --- | --- |
| `.venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py -k 'not test_service_disconnect_closes_real_retry_upstream' --tb=short` | 0 | 80 passed, eight deselected. |
| `timeout 60 .venv/bin/python -m pytest -q tests/unit/test_response_output_cap.py --tb=short --timeout=10 --maxfail=1` | 1 | 80 passed, then regression database fixture timed out. |
| `timeout 60 .venv/bin/python -m pytest -q tests/unit/test_proxy_*.py --tb=short --timeout=10 --maxfail=1` | 1 | 40 passed, then an existing HTTP bridge test timed out in aiosqlite. |
| `uvx ruff@0.15.1 check app/modules/proxy/service.py tests/unit/test_response_output_cap.py` | 2 | Default tool cache is read-only. |
| `uvx ruff@0.15.1 format --check app/modules/proxy/service.py tests/unit/test_response_output_cap.py` | 2 | Default tool cache is read-only. |
| `UV_CACHE_DIR=/tmp/response-cap-uv-cache UV_TOOL_DIR=/tmp/response-cap-uv-tools uvx --offline ruff@0.15.1 check app/modules/proxy/service.py tests/unit/test_response_output_cap.py` | 1 | Ruff is absent from the writable cache. Online resolution also failed with blocked DNS (exit 2). |
| `UV_CACHE_DIR=/tmp/response-cap-uv-cache UV_TOOL_DIR=/tmp/response-cap-uv-tools uvx --offline ruff@0.15.1 format --check app/modules/proxy/service.py tests/unit/test_response_output_cap.py` | 1 | Ruff is absent from the writable cache. |
| `.venv/bin/python -m ruff check app/modules/proxy/service.py tests/unit/test_response_output_cap.py` | 0 | Installed Ruff is version 0.15.1. |
| `.venv/bin/python -m ruff format --check app/modules/proxy/service.py tests/unit/test_response_output_cap.py` | 0 | Both files are formatted. |
| `.venv/bin/python -m ty check` | 0 | Repository type check passed. |

Next verification must first reach and fail the upstream closure assertion with unchanged production code. Then close both `_stream_once` call sites in `_stream_with_retry`, including the forced-refresh path, and close the upstream iterator owned by `_stream_once`. Retry timeout overrides must remain active until attempt cleanup finishes. Rerun all requested checks and obtain the required read-only review. The change remains unverified and unarchived.
