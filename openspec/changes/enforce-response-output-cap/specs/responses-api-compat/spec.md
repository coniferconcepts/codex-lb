## ADDED Requirements

### Requirement: Enforce client response output caps locally
For `/v1/responses`, the service MUST accept `max_output_tokens` only as a positive integer and MUST return an OpenAI-style 400 error for any other supplied value, including null and booleans. The field MUST never be forwarded to the Codex upstream. An absent field MUST preserve existing response behavior.

The service MUST enforce the budget after public output normalization, including text converted from `final_answer` items. It MUST estimate visible output tokens as `ceil(visible_output_chars / 3)`, counting Unicode characters cumulatively across output text and complete function-call arguments. Output text MUST fit a cumulative allowance of three characters per permitted token. All delta, done, item, content-part, and full-response output events MUST agree with that allowance and with the terminal output.

Function-call events MUST be buffered until the complete item is available. If its complete arguments exceed the remaining allowance, the service MUST drop the entire call, emit none of its events, and mark the response incomplete with reason `max_output_tokens`. Function-call arguments MUST NOT be truncated, including in collected output.

After the streaming budget is reached, the service MUST stop relaying further output and MUST continue draining upstream until the terminal event so authoritative usage and API-key settlement can complete. Upstream terminal handling and API-key settlement MUST finish before the service emits `response.incomplete` with response status `incomplete`, `incomplete_details.reason` equal to `max_output_tokens`, retained output, and authoritative upstream usage. Client disconnect MUST propagate `aclose` through every stream wrapper to the inner upstream stream, with or without a cap.

For collected responses, the service MUST mark the response incomplete when either final upstream `usage.output_tokens` or the visible estimate exceeds the cap. Upstream usage MUST be preserved. For streaming responses whose final upstream usage exceeds the cap, the terminal event MUST also be marked incomplete.

Hidden reasoning tokens MUST be checked through upstream usage when available. The visible estimate MUST NOT be reported as exact usage or as a count of hidden reasoning tokens.

#### Scenario: Invalid cap
- **WHEN** a client supplies zero, a negative integer, a boolean, a string, a non-integer number, or null as `max_output_tokens`
- **THEN** the service returns an OpenAI-style 400 error identifying `max_output_tokens`

#### Scenario: Stream crosses its visible budget
- **WHEN** streamed text reaches the cumulative estimated cap
- **THEN** the crossing delta fits the remaining allowance, further output is suppressed, and upstream is drained through terminal usage before the incomplete response is emitted

#### Scenario: Final usage exposes hidden reasoning
- **WHEN** visible output fits the cap but final upstream output-token usage exceeds it
- **THEN** the response is incomplete with reason `max_output_tokens` and preserves upstream usage

#### Scenario: Collected output exceeds the estimate
- **WHEN** a collected response has visible output estimating more tokens than the cap
- **THEN** its text fits the cumulative allowance, oversized function calls are dropped whole, and its status is incomplete

#### Scenario: No client cap
- **WHEN** the client omits `max_output_tokens`
- **THEN** the existing response behavior is preserved

#### Scenario: Oversized function call
- **WHEN** complete function-call arguments exceed the remaining allowance
- **THEN** none of that call's events or terminal item is emitted and the response is incomplete

#### Scenario: Repeated full output
- **WHEN** done or full-response events repeat text already emitted or contain additional text
- **THEN** repeated text is counted once and every emitted representation fits the remaining allowance

#### Scenario: Client disconnect
- **WHEN** a client disconnects while the response iterator is suspended
- **THEN** every wrapper closes its inner iterator through to the upstream stream

#### Scenario: Normalized final answer
- **WHEN** an upstream `final_answer.text` exceeds the cap
- **THEN** its public message text is truncated and the response is incomplete

#### Scenario: Error terminal after the cap
- **WHEN** upstream ends with an error event after visible output reaches the cap
- **THEN** upstream terminal handling finishes before the service emits an incomplete response with reason `max_output_tokens` and any usage already received
