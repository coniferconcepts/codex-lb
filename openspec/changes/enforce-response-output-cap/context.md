# Local response output caps

The Codex upstream rejects `max_output_tokens`, so the proxy strips the field and enforces the client limit locally. See [the requirements](specs/responses-api-compat/spec.md) and [the stable context](../../specs/responses-api-compat/output-cap-context.md).

The estimate is `ceil(visible_output_chars / 3)`, counting Unicode code points in public text and complete function-call arguments. Seven characters estimate three tokens. One allowance covers all output items and content parts. Repeated done events and full-output snapshots do not count the same output twice. Public normalization runs before enforcement, including conversion of `final_answer.text` to message text.

At the streaming cap, the proxy stops relaying output and drains the upstream stream through terminal usage and API-key settlement. It emits `response.incomplete` only after upstream terminal handling and settlement finish, preserving retained output and authoritative usage. The reason is `max_output_tokens`. Reaching the cap does not close the upstream stream early.

Text can be truncated to a prefix that fits. Function-call events stay buffered until the complete item is available. If its arguments exceed the remaining allowance, the entire call and all its events are dropped. Arguments are never cut mid-JSON. For example, a two-token cap allows six characters: `abcdefgh` becomes `abcdef`, while the eight-character arguments `{"x":12}` cause the whole call to be omitted. Collected responses also drop oversized calls and preserve final usage.

This estimate is a heuristic, not a tokenizer or a guaranteed upper bound for every language or encoding. Hidden reasoning tokens appear only in upstream usage. Final usage can mark the response incomplete even if the visible output fits. Estimated counts never replace upstream usage.

A client disconnect closes every stream wrapper through to the inner service generator, on both retry and HTTP-bridge paths, with or without a cap. Response cleanup shields closure from cancellation. A disconnect before terminal usage can preserve only usage already received.

No dependencies are added. See [verification](verification.md) for the failing regressions, passing checks, and outstanding environment checks.
