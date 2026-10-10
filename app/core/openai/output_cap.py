from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from typing import cast

from app.core.errors import response_failed_event
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_dict, is_json_list
from app.core.utils.sse import format_sse_event, parse_sse_data_json


def estimate_output_tokens(visible_output_chars: int) -> int:
    return (visible_output_chars + 2) // 3


def _incomplete(response: dict[str, JsonValue]) -> dict[str, JsonValue]:
    base = response_failed_event(
        "max_output_tokens", "Output limit reached", incomplete_details={"reason": "max_output_tokens"}
    )
    result = cast(dict[str, JsonValue], dict(base["response"]))
    result.update(response)
    result.pop("error", None)
    result["status"] = "incomplete"
    result["incomplete_details"] = {"reason": "max_output_tokens"}
    return result


def _output_chars(item: dict[str, JsonValue]) -> int:
    if item.get("type") == "function_call":
        arguments = item.get("arguments")
        return len(arguments) if isinstance(arguments, str) else 0
    content = item.get("content")
    if not isinstance(content, list):
        return 0
    return sum(
        len(part["text"])
        for part in content
        if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)
    )


def _truncate_output(response: dict[str, JsonValue], max_chars: int) -> bool:
    output = response.get("output")
    if not isinstance(output, list):
        return False
    remaining = max_chars
    retained: list[JsonValue] = []
    exceeded = False
    for item in output:
        if not is_json_dict(item):
            retained.append(item)
            continue
        size = _output_chars(item)
        if item.get("type") == "function_call":
            if size > remaining:
                exceeded = True
                break
            remaining -= size
        else:
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if not is_json_dict(part) or part.get("type") != "output_text":
                        continue
                    text = part.get("text")
                    if isinstance(text, str):
                        part["text"] = text[:remaining]
                        exceeded |= len(text) > remaining
                        remaining = max(0, remaining - len(text))
        retained.append(item)
    response["output"] = retained
    return exceeded


@dataclass
class _OutputBudget:
    cap: int
    stop_at_cap: bool
    chars: int = 0
    stopped: bool = False
    response: dict[str, JsonValue] = field(default_factory=dict)
    items: dict[int, dict[str, JsonValue]] = field(default_factory=dict)
    calls: dict[int, dict[str, JsonValue]] = field(default_factory=dict)
    call_events: dict[int, list[dict[str, JsonValue]]] = field(default_factory=dict)

    def observe_response(self, response: dict[str, JsonValue]) -> None:
        self.response.update(
            deepcopy({key: value for key, value in response.items() if key not in {"output", "usage"}})
        )
        if isinstance(response.get("usage"), dict):
            self.response["usage"] = deepcopy(response["usage"])

    def retain_item(self, index: int, item: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
        previous = self.items.get(index, {})
        allowance = max(0, self.cap * 3 - self.chars + _output_chars(previous))
        bounded: dict[str, JsonValue] = {"output": [deepcopy(item)]}
        exceeded = _truncate_output(bounded, allowance)
        output = cast(list[dict[str, JsonValue]], bounded["output"])
        if not output:
            self.stopped = True
            return None
        retained = output[0]
        self.chars += _output_chars(retained) - _output_chars(previous)
        self.items[index] = retained
        self.stopped |= exceeded or (self.stop_at_cap and estimate_output_tokens(self.chars) >= self.cap)
        return retained

    def text_event(self, payload: dict[str, JsonValue], index: int) -> None:
        item = deepcopy(self.items.get(index, {"id": payload.get("item_id"), "type": "message", "role": "assistant"}))
        content = item.setdefault("content", [])
        content_index = payload.get("content_index", 0)
        if not is_json_list(content) or not isinstance(content_index, int) or content_index < 0:
            return
        while len(content) <= content_index:
            content.append({"type": "output_text", "text": ""})
        part = content[content_index]
        if not is_json_dict(part):
            return
        previous = part.get("text", "")
        previous = previous if isinstance(previous, str) else ""
        kind = payload.get("type")
        if kind == "response.output_text.delta":
            delta = payload.get("delta")
            if not isinstance(delta, str):
                return
            part["text"] = previous + delta
        elif kind == "response.output_text.done":
            part["text"] = payload.get("text")
        else:
            incoming = payload.get("part")
            if not is_json_dict(incoming):
                return
            content[content_index] = deepcopy(incoming)
        retained = self.retain_item(index, item)
        if retained is None:
            return
        retained_content = cast(list[dict[str, JsonValue]], retained["content"])
        retained_part = retained_content[content_index]
        text = retained_part.get("text", "")
        if kind == "response.output_text.delta" and isinstance(text, str):
            payload["delta"] = text[len(previous) :]
        elif kind == "response.output_text.done":
            payload["text"] = text
        else:
            payload["part"] = deepcopy(retained_part)

    def function_event(self, payload: dict[str, JsonValue], index: int) -> list[dict[str, JsonValue]]:
        events = self.call_events.setdefault(index, [])
        events.append(deepcopy(payload))
        item = payload.get("item")
        if is_json_dict(item):
            self.calls[index] = deepcopy(item)
        call = self.calls.setdefault(index, {"id": payload.get("item_id"), "type": "function_call", "arguments": ""})
        kind = payload.get("type")
        delta = payload.get("delta")
        if kind == "response.function_call_arguments.delta" and isinstance(delta, str):
            previous = call.get("arguments", "")
            call["arguments"] = (previous if isinstance(previous, str) else "") + delta
        if kind == "response.function_call_arguments.done":
            call["arguments"] = payload.get("arguments")
        if _output_chars(call) > self.cap * 3 - self.chars:
            self.stopped = True
            self.calls.pop(index, None)
            self.call_events.pop(index, None)
            return []
        if kind != "response.output_item.done":
            return []
        retained = self.retain_item(index, call)
        self.calls.pop(index, None)
        self.call_events.pop(index, None)
        return events if retained is not None else []

    def response_output(self, response: dict[str, JsonValue], *, terminal: bool) -> list[dict[str, JsonValue]]:
        self.observe_response(response)
        output = response.get("output")
        events: list[dict[str, JsonValue]] = []
        if not self.stopped and isinstance(output, list):
            for index, item in enumerate(output):
                if not is_json_dict(item):
                    continue
                if item.get("type") == "function_call" and not terminal:
                    continue
                retained = self.retain_item(index, item)
                if retained is not None and index in self.call_events:
                    events.extend(self.call_events.pop(index))
                    self.calls.pop(index, None)
                if self.stopped:
                    break
        response["output"] = [deepcopy(item) for _, item in sorted(self.items.items())]
        return events

    def final_response(self) -> tuple[dict[str, JsonValue], bool]:
        response = deepcopy(self.response)
        response["output"] = [deepcopy(item) for _, item in sorted(self.items.items())]
        usage = response.get("usage")
        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        exceeded = self.stopped or (isinstance(output_tokens, int) and output_tokens > self.cap)
        return response, exceeded


async def limit_response_stream(
    stream: AsyncIterator[str], cap: int | None, *, stop_early: bool = True
) -> AsyncIterator[str]:
    budget = _OutputBudget(cap, stop_early) if cap is not None else None
    pending_terminal: dict[str, JsonValue] | None = None
    done_block: str | None = None
    try:
        async for block in stream:
            if pending_terminal is not None:
                if block.strip() == "data: [DONE]":
                    done_block = block
                continue
            if budget is None:
                yield block
                continue
            payload = parse_sse_data_json(block)
            if payload is None:
                yield block
                continue
            kind = payload.get("type")
            terminal = kind in {"response.completed", "response.incomplete", "response.failed", "error"}
            response = payload.get("response")
            if is_json_dict(response):
                if budget.stopped and not terminal:
                    budget.observe_response(response)
                    continue
                original_response = deepcopy(response)
                events = budget.response_output(response, terminal=terminal)
                for event in events:
                    yield format_sse_event(event)
                if terminal:
                    final, exceeded = budget.final_response()
                    if exceeded:
                        payload["type"] = "response.incomplete"
                        payload["response"] = _incomplete(final)
                        pending_terminal = payload
                        continue
                    else:
                        payload["response"] = original_response
                elif "output" not in original_response:
                    payload["response"] = original_response
                yield format_sse_event(payload)
                continue
            if terminal:
                final, exceeded = budget.final_response()
                if exceeded:
                    pending_terminal = {"type": "response.incomplete", "response": _incomplete(final)}
                else:
                    yield block
                continue
            if budget.stopped:
                continue
            index = payload.get("output_index", 0)
            if not isinstance(index, int):
                continue
            item = payload.get("item")
            if (isinstance(kind, str) and kind.startswith("response.function_call_arguments.")) or (
                is_json_dict(item) and item.get("type") == "function_call"
            ):
                for event in budget.function_event(payload, index):
                    yield format_sse_event(event)
                continue
            if kind in {"response.output_item.added", "response.output_item.done"} and is_json_dict(item):
                payload["item"] = budget.retain_item(index, item)
            elif kind in {
                "response.output_text.delta",
                "response.output_text.done",
                "response.content_part.added",
                "response.content_part.done",
            }:
                budget.text_event(payload, index)
            yield format_sse_event(payload)
        if pending_terminal is not None:
            yield format_sse_event(pending_terminal)
            if done_block is not None:
                yield done_block
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
