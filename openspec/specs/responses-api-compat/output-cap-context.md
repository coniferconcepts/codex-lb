# Local output caps

The Codex upstream rejects `max_output_tokens`, so the proxy strips the field and enforces the client limit locally. See [the requirements](spec.md).

Visible public text and complete function-call arguments use the estimate `ceil(visible_output_chars / 3)`. Characters are Unicode code points, not encoded bytes. Seven characters estimate three tokens. The allowance applies across items and content parts. Repeated done events and full-output snapshots describe the same output and do not spend the allowance again. Public normalization runs first, so a `final_answer.text` becomes message text before enforcement.

This heuristic is not a tokenizer or a guaranteed upper bound for every language or encoding. Hidden reasoning tokens are visible only in upstream usage. Final usage can mark a response incomplete after visible text has streamed. Estimated counts never replace upstream usage.

At the streaming cap, the proxy stops relaying output while it drains the upstream stream. The upstream service finishes terminal handling and API-key settlement before the proxy emits the incomplete terminal response. This preserves authoritative usage and avoids releasing a reservation before settlement. A client disconnect closes the response iterator and each wrapper through to the upstream service stream; response cleanup shields that closure from cancellation.

Text can use a prefix that fits the allowance. Function-call events stay buffered until the complete item is available. Calls that exceed the remaining allowance are dropped whole, so clients never receive truncated arguments. For example, a cap of two tokens permits six characters. An eight-character message becomes `abcdef`, but the nine-character arguments `{"x":123}` cause the entire call to be omitted. Collected responses also drop oversized calls and preserve final upstream usage.

The change adds no dependencies. See [verification](../../changes/enforce-response-output-cap/verification.md) for tests and outstanding environment checks.
