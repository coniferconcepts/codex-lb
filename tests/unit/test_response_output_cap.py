from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from starlette.types import Message

import app.modules.proxy.api as api
import app.modules.proxy.service as proxy_service
from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.core.openai.output_cap import estimate_output_tokens, limit_response_stream
from app.core.openai.requests import ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import _proxy_repo_context


@pytest.mark.parametrize(("chars", "tokens"), [(0, 0), (1, 1), (3, 1), (4, 2), (7, 3)])
def test_estimate(chars: int, tokens: int) -> None:
    assert estimate_output_tokens(chars) == tokens


def block(kind: str, **values: JsonValue) -> str:
    payload: dict[str, JsonValue] = {"type": kind, **values}
    return format_sse_event(payload)


async def test_cap_drains_upstream_and_preserves_usage() -> None:
    closed = False
    advanced = False

    async def upstream() -> AsyncIterator[str]:
        nonlocal closed, advanced
        try:
            yield block("response.created", response={"id": "resp_cap", "usage": {"output_tokens": 1}})
            yield block("response.output_text.delta", delta="ab", output_index=0, item_id="item_cap")
            yield block("response.output_text.delta", delta="cdefghijkl", output_index=0, item_id="item_cap")
            advanced = True
            yield block("response.output_text.delta", delta="must not relay")
            yield block("response.completed", response={"status": "completed", "usage": {"output_tokens": 9}})
        finally:
            closed = True

    events = [parse_sse_data_json(value) async for value in limit_response_stream(upstream(), 2)]
    assert closed and advanced
    assert "".join(str(event["delta"]) for event in events if event and "delta" in event) == "abcdef"
    terminal = events[-1]
    assert terminal is not None
    assert terminal["type"] == "response.incomplete"
    response = cast(dict[str, JsonValue], terminal["response"])
    assert response["status"] == "incomplete"
    assert response["incomplete_details"] == {"reason": "max_output_tokens"}
    assert response["usage"] == {"output_tokens": 9}
    assert response["id"] == "resp_cap"


@pytest.mark.parametrize(("text", "usage", "expected"), [("abcdefgh", 1, "abcdef"), ("abc", 9, "abc")])
async def test_collected_cap_checks_estimate_and_final_usage(text: str, usage: int, expected: str) -> None:
    async def upstream() -> AsyncIterator[str]:
        yield block(
            "response.completed",
            response={
                "id": "resp_cap",
                "status": "completed",
                "usage": {"output_tokens": usage},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            },
        )

    result = await api._collect_responses_payload(limit_response_stream(upstream(), 2, stop_early=False))
    body = result.model_dump(mode="json", exclude_none=True)
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}
    assert body["output"][0]["content"][0]["text"] == expected
    assert body["usage"]["output_tokens"] == usage


@pytest.mark.parametrize("cap", [0, -1, True, 1.5, "2", None])
async def test_route_invalid_cap_is_openai_400(cap: JsonValue) -> None:
    payload = V1ResponsesRequest.model_validate({"model": "test-model", "input": "hi", "max_output_tokens": cap})
    response = await api.v1_responses(
        Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}),
        payload,
        cast(api.ProxyContext, None),
        None,
    )
    assert response.status_code == 400
    body = json.loads(cast(bytes, response.body))
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "max_output_tokens"


async def test_route_stream_caps_fake_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = False

    async def headers() -> dict[str, str]:
        return {}

    async def upstream(payload, *_args, **_kwargs) -> AsyncIterator[str]:
        nonlocal closed
        assert "max_output_tokens" not in payload.to_payload()
        try:
            yield block("response.output_text.delta", delta="abcdefghijkl", output_index=0)
            yield block("response.completed", response={"status": "completed", "usage": {"output_tokens": 7}})
        finally:
            closed = True

    monkeypatch.setattr(
        api.proxy_service_module, "get_settings", lambda: SimpleNamespace(http_responses_session_bridge_enabled=False)
    )
    context = cast(
        api.ProxyContext,
        SimpleNamespace(service=SimpleNamespace(rate_limit_headers=headers, stream_http_responses=upstream)),
    )
    payload = V1ResponsesRequest.model_validate(
        {"model": "test-model", "input": "hi", "stream": True, "max_output_tokens": 2}
    )
    response = await api.v1_responses(
        Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}), payload, context, None
    )
    assert isinstance(response, StreamingResponse)
    events = [parse_sse_data_json(str(value)) async for value in response.body_iterator]
    assert closed
    assert events[0] is not None and events[0]["delta"] == "abcdef"
    assert events[-1] is not None and events[-1]["type"] == "response.incomplete"


async def test_budget_aggregates_unicode_text_and_arguments() -> None:
    async def upstream() -> AsyncIterator[str]:
        yield block("response.output_text.delta", delta="猫猫", output_index=0)
        yield block("response.function_call_arguments.delta", delta='"猫猫"', output_index=1)
        yield block("response.output_item.done", output_index=1, item={"type": "function_call", "arguments": '"猫猫"'})
        yield block("response.completed", response={"status": "completed", "output": [], "usage": {"output_tokens": 2}})

    events = [parse_sse_data_json(value) async for value in limit_response_stream(upstream(), 2)]
    assert events[-1] is not None and events[-1]["type"] == "response.incomplete"
    response = cast(dict[str, JsonValue], events[-1]["response"])
    output = cast(list[dict[str, JsonValue]], response["output"])
    assert output[1]["arguments"] == '"猫猫"'


@pytest.mark.parametrize("cap", [None, 2])
async def test_undercap_or_absent_is_unchanged(cap: int | None) -> None:
    original = [
        block("response.output_text.delta", delta="abc"),
        block("response.completed", response={"status": "completed", "output": [], "usage": {"output_tokens": 1}}),
        "data: [DONE]\n\n",
    ]

    async def upstream() -> AsyncIterator[str]:
        for value in original:
            yield value

    assert [value async for value in limit_response_stream(upstream(), cap)] == original


@pytest.mark.parametrize("stop_early", [True, False])
async def test_final_usage_over_cap_preserves_seen_usage(stop_early: bool) -> None:
    async def upstream() -> AsyncIterator[str]:
        yield block("response.created", response={"id": "resp_cap", "usage": {"output_tokens": 9}})
        yield block("response.completed", response={"status": "completed", "output": [], "usage": None})

    events = [parse_sse_data_json(value) async for value in limit_response_stream(upstream(), 2, stop_early=stop_early)]
    assert events[-1] is not None
    response = cast(dict[str, JsonValue], events[-1]["response"])
    assert response["status"] == "incomplete"
    assert response["usage"] == {"output_tokens": 9}


async def test_collected_exact_cap_is_completed() -> None:
    async def upstream() -> AsyncIterator[str]:
        yield block(
            "response.completed",
            response={
                "status": "completed",
                "usage": {"output_tokens": 2},
                "output": [{"type": "function_call", "arguments": "abcdef"}],
            },
        )

    result = await api._collect_responses_payload(limit_response_stream(upstream(), 2, stop_early=False))
    assert result.model_dump()["status"] == "completed"


@pytest.mark.parametrize("stop_early", [True, False])
@pytest.mark.parametrize("terminal_only", [True, False])
async def test_review_oversized_function_call_is_never_emitted(stop_early: bool, terminal_only: bool) -> None:
    call: dict[str, JsonValue] = {
        "id": "fc_cap",
        "type": "function_call",
        "name": "lookup",
        "call_id": "call_cap",
        "arguments": '{"x":123}',
    }

    async def upstream() -> AsyncIterator[str]:
        if not terminal_only:
            yield block("response.output_item.added", output_index=0, item={**call, "arguments": ""})
            yield block("response.function_call_arguments.delta", output_index=0, delta='{"x":')
            yield block("response.function_call_arguments.delta", output_index=0, delta="123}")
            yield block("response.function_call_arguments.done", output_index=0, arguments=call["arguments"])
            yield block("response.output_item.done", output_index=0, item=call)
        yield block(
            "response.completed", response={"status": "completed", "output": [call], "usage": {"output_tokens": 3}}
        )

    events = [parse_sse_data_json(value) async for value in limit_response_stream(upstream(), 2, stop_early=stop_early)]
    assert all(event and event["type"] == "response.incomplete" for event in events)
    assert events[-1] is not None
    response = cast(dict[str, JsonValue], events[-1]["response"])
    assert response["output"] == []
    assert response["incomplete_details"] == {"reason": "max_output_tokens"}


async def test_review_function_call_events_wait_for_complete_item() -> None:
    completed = False
    arguments = '{"x":1}'
    call: dict[str, JsonValue] = {
        "type": "function_call",
        "arguments": arguments,
        "call_id": "call_fit",
        "name": "lookup",
    }

    async def upstream() -> AsyncIterator[str]:
        nonlocal completed
        yield block("response.output_item.added", output_index=0, item={**call, "arguments": ""})
        yield block("response.function_call_arguments.delta", output_index=0, delta=arguments)
        completed = True
        yield block("response.output_item.done", output_index=0, item=call)
        yield block("response.completed", response={"status": "completed", "output": [call]})

    stream = limit_response_stream(upstream(), 4)
    first = parse_sse_data_json(await anext(stream))
    assert completed
    assert first is not None and first["type"] == "response.output_item.added"
    events = [first, *[parse_sse_data_json(value) async for value in stream]]
    assert events[-1] is not None and events[-1]["type"] == "response.completed"
    assert [event["delta"] for event in events if event and "delta" in event] == [arguments]


async def test_review_cap_drains_terminal_usage_and_settlement() -> None:
    settled = False

    async def upstream() -> AsyncIterator[str]:
        nonlocal settled
        yield block("response.created", response={"id": "resp_drain", "usage": {"output_tokens": 1}})
        yield block("response.output_text.delta", delta="abcdefgh", output_index=0)
        yield block("response.output_text.delta", delta="hidden tail", output_index=0)
        yield block(
            "response.completed",
            response={"status": "completed", "usage": {"input_tokens": 11, "output_tokens": 19}, "output": []},
        )
        settled = True

    events: list[dict[str, JsonValue] | None] = []
    async for value in limit_response_stream(upstream(), 2):
        event = parse_sse_data_json(value)
        if event and event["type"] == "response.incomplete":
            assert settled, "terminal output must wait for upstream settlement"
        events.append(event)
    assert settled
    assert [event["delta"] for event in events if event and "delta" in event] == ["abcdef"]
    assert events[-1] is not None and events[-1]["type"] == "response.incomplete"
    response = cast(dict[str, JsonValue], events[-1]["response"])
    assert response["usage"] == {"input_tokens": 11, "output_tokens": 19}
    assert response["output"] == [
        {"id": None, "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "abcdef"}]}
    ]


@pytest.mark.parametrize(
    "kind",
    [
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
        "response.content_part.done",
        "response.output_text.done",
        "response.in_progress",
    ],
)
async def test_review_full_output_events_fit_cap(kind: str) -> None:
    part: dict[str, JsonValue] = {"type": "output_text", "text": "abcdefgh"}
    item: dict[str, JsonValue] = {"type": "message", "role": "assistant", "content": [part]}
    fields: dict[str, JsonValue]
    if "output_item" in kind:
        fields = {"item": item}
    elif "content_part" in kind:
        fields = {"part": part, "content_index": 0}
    elif "output_text" in kind:
        fields = {"text": "abcdefgh", "content_index": 0}
    else:
        fields = {"response": {"status": "in_progress", "output": [item]}}

    async def upstream() -> AsyncIterator[str]:
        yield block("response.output_text.delta", delta="abc", output_index=0)
        yield block(kind, output_index=0, **fields)
        yield block(
            "response.completed", response={"status": "completed", "output": [item], "usage": {"output_tokens": 3}}
        )

    events = [parse_sse_data_json(value) async for value in limit_response_stream(upstream(), 2)]
    serialized = json.dumps(events)
    assert "abcdefgh" not in serialized
    assert events[-1] is not None and events[-1]["type"] == "response.incomplete"
    response = cast(dict[str, JsonValue], events[-1]["response"])
    output = cast(list[dict[str, JsonValue]], response["output"])
    assert len(output) == 1
    assert output[0]["content"] == [{"type": "output_text", "text": "abcdef"}]


@pytest.mark.parametrize("cap", [None, 2])
@pytest.mark.parametrize("disconnect_after", [1, 2])
@pytest.mark.parametrize("transport", ["iterator", "2.0", "2.4"])
@pytest.mark.parametrize("service_path", ["stub", "retry", "bridge"])
async def test_review_route_disconnect_closes_inner_stream(
    monkeypatch: pytest.MonkeyPatch, cap: int | None, disconnect_after: int, transport: str, service_path: str
) -> None:
    closed = False

    async def headers() -> dict[str, str]:
        return {}

    async def upstream(*_args, **_kwargs) -> AsyncIterator[str]:
        nonlocal closed
        try:
            yield block("response.created", response={"id": "resp_disconnect"})
            yield block("response.output_text.delta", delta="abc", output_index=0)
            yield block("response.completed", response={"status": "completed", "output": []})
        finally:
            closed = True

    settings = Settings(http_responses_session_bridge_enabled=service_path == "bridge")
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    if service_path == "stub":
        service = SimpleNamespace(rate_limit_headers=headers, stream_http_responses=upstream)
    else:
        service = proxy_service.ProxyService(cast(proxy_service.ProxyRepoFactory, None))
        monkeypatch.setattr(service, "rate_limit_headers", headers)
        inner_method = "_stream_via_http_bridge" if service_path == "bridge" else "_stream_with_retry"
        monkeypatch.setattr(service, inner_method, upstream)
        monkeypatch.setattr(
            proxy_service,
            "get_settings_cache",
            lambda: SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=60,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        )
    context = cast(
        api.ProxyContext,
        SimpleNamespace(service=service),
    )
    values: dict[str, JsonValue] = {"model": "test-model", "input": "hi", "stream": True}
    if cap is not None:
        values["max_output_tokens"] = cap
    response = await api.v1_responses(
        Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}),
        V1ResponsesRequest.model_validate(values),
        context,
        None,
    )
    assert isinstance(response, StreamingResponse)
    if transport == "iterator":
        iterator = cast(AsyncGenerator[str, None], response.body_iterator)
        for _ in range(disconnect_after):
            await anext(iterator)
        await iterator.aclose()
    else:
        disconnect = anyio.Event()
        sent = 0

        async def receive() -> Message:
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            nonlocal sent
            if message["type"] != "http.response.body":
                return
            sent += 1
            if sent == disconnect_after:
                if transport == "2.4":
                    raise OSError("client disconnected")
                disconnect.set()
                await anyio.sleep_forever()

        scope = {"type": "http", "asgi": {"spec_version": transport}}
        if transport == "2.4":
            with pytest.raises(ClientDisconnect):
                await response(scope, receive, send)
        else:
            await response(scope, receive, send)
    assert closed


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("item_event", [True, False])
async def test_review_route_caps_normalized_final_answer(
    monkeypatch: pytest.MonkeyPatch, streaming: bool, item_event: bool
) -> None:
    item: dict[str, JsonValue] = {"id": "fa_cap", "type": "final_answer", "text": "abcdefgh"}

    async def headers() -> dict[str, str]:
        return {}

    async def upstream(*_args, **_kwargs) -> AsyncIterator[str]:
        if item_event:
            yield block("response.output_item.done", output_index=0, item=item)
        yield block(
            "response.completed",
            response={
                "id": "resp_normalized",
                "status": "completed",
                "output": [] if item_event else [item],
                "usage": {"output_tokens": 1},
            },
        )

    monkeypatch.setattr(
        api.proxy_service_module, "get_settings", lambda: SimpleNamespace(http_responses_session_bridge_enabled=False)
    )
    context = cast(
        api.ProxyContext,
        SimpleNamespace(service=SimpleNamespace(rate_limit_headers=headers, stream_http_responses=upstream)),
    )
    values: dict[str, JsonValue] = {"model": "test-model", "input": "hi", "stream": streaming, "max_output_tokens": 2}
    response = await api.v1_responses(
        Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}),
        V1ResponsesRequest.model_validate(values),
        context,
        None,
    )
    if isinstance(response, StreamingResponse):
        events = [parse_sse_data_json(str(value)) async for value in response.body_iterator]
        assert "abcdefgh" not in json.dumps(events)
        assert events[-1] is not None
        body = cast(dict[str, JsonValue], events[-1]["response"])
    else:
        body = json.loads(cast(bytes, response.body))
    assert body["status"] == "incomplete"
    output = cast(list[dict[str, JsonValue]], body["output"])
    content = cast(list[dict[str, JsonValue]], output[0]["content"])
    assert content[0]["text"] == "abcdef"


@pytest.mark.parametrize("stop_early", [True, False])
async def test_repeated_done_output_is_counted_once(stop_early: bool) -> None:
    item: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "abcde"}],
    }

    async def upstream() -> AsyncIterator[str]:
        yield block("response.output_text.delta", delta="abcde", output_index=0, content_index=0)
        yield block("response.output_text.done", text="abcde", output_index=0, content_index=0)
        yield block(
            "response.content_part.done", part={"type": "output_text", "text": "abcde"}, output_index=0, content_index=0
        )
        yield block("response.output_item.done", item=item, output_index=0)
        yield block(
            "response.completed", response={"status": "completed", "output": [item], "usage": {"output_tokens": 2}}
        )

    events = [parse_sse_data_json(value) async for value in limit_response_stream(upstream(), 3, stop_early=stop_early)]
    assert events[-1] is not None and events[-1]["type"] == "response.completed"
    response = cast(dict[str, JsonValue], events[-1]["response"])
    assert response["output"] == [item]


async def test_function_call_uses_remaining_budget_after_text() -> None:
    call: dict[str, JsonValue] = {
        "type": "function_call",
        "name": "lookup",
        "call_id": "call_remaining",
        "arguments": '{"x":1}',
    }

    async def upstream() -> AsyncIterator[str]:
        yield block("response.output_text.delta", delta="abc", output_index=0)
        yield block("response.output_item.added", item={**call, "arguments": ""}, output_index=1)
        yield block("response.function_call_arguments.delta", delta=call["arguments"], output_index=1)
        yield block("response.output_item.done", item=call, output_index=1)
        yield block(
            "response.completed",
            response={
                "status": "completed",
                "output": [
                    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "abc"}]},
                    call,
                ],
                "usage": {"output_tokens": 4},
            },
        )
        yield "data: [DONE]\n\n"

    events = [value async for value in limit_response_stream(upstream(), 3)]
    assert "function_call" not in "".join(events)
    assert events[-1] == "data: [DONE]\n\n"
    terminal = parse_sse_data_json(events[-2])
    assert terminal is not None and terminal["type"] == "response.incomplete"
    response = cast(dict[str, JsonValue], terminal["response"])
    output = cast(list[dict[str, JsonValue]], response["output"])
    assert len(output) == 1 and output[0]["content"] == [{"type": "output_text", "text": "abc"}]


async def test_full_items_and_parts_share_one_allowance() -> None:
    first: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "abc"}],
    }
    second: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "defg"}, {"type": "output_text", "text": "hijk"}],
    }

    async def upstream() -> AsyncIterator[str]:
        yield block("response.output_item.done", item=first, output_index=0)
        yield block("response.output_item.done", item=second, output_index=1)
        yield block("response.completed", response={"status": "completed", "output": [first, second]})

    events = [parse_sse_data_json(value) async for value in limit_response_stream(upstream(), 3)]
    assert events[-1] is not None and events[-1]["type"] == "response.incomplete"
    response = cast(dict[str, JsonValue], events[-1]["response"])
    output = cast(list[dict[str, JsonValue]], response["output"])
    assert output[0] == first
    assert output[1]["content"] == [{"type": "output_text", "text": "defg"}, {"type": "output_text", "text": "hi"}]
    assert "hijk" not in json.dumps(events)


@pytest.mark.parametrize("kind", ["response.created", "response.in_progress"])
async def test_review_route_caps_normalized_full_snapshot(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    item: dict[str, JsonValue] = {"type": "final_answer", "text": "abcdefgh"}

    async def headers() -> dict[str, str]:
        return {}

    async def upstream(*_args, **_kwargs) -> AsyncIterator[str]:
        yield block(kind, response={"status": "in_progress", "output": [item]})
        yield block(
            "response.completed", response={"status": "completed", "output": [item], "usage": {"output_tokens": 3}}
        )

    monkeypatch.setattr(
        api.proxy_service_module, "get_settings", lambda: SimpleNamespace(http_responses_session_bridge_enabled=False)
    )
    context = cast(
        api.ProxyContext,
        SimpleNamespace(service=SimpleNamespace(rate_limit_headers=headers, stream_http_responses=upstream)),
    )
    response = await api.v1_responses(
        Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}),
        V1ResponsesRequest.model_validate(
            {"model": "test-model", "input": "hi", "stream": True, "max_output_tokens": 2}
        ),
        context,
        None,
    )
    assert isinstance(response, StreamingResponse)
    events = [parse_sse_data_json(str(value)) async for value in response.body_iterator]
    assert "abcdefgh" not in json.dumps(events)
    assert events[-1] is not None and events[-1]["type"] == "response.incomplete"


async def test_review_cap_drains_error_terminal_before_incomplete() -> None:
    settled = False

    async def upstream() -> AsyncIterator[str]:
        nonlocal settled
        yield block("response.output_text.delta", delta="abcdefgh", output_index=0)
        yield block("error", error={"code": "server_error", "message": "upstream error"})
        settled = True

    events: list[dict[str, JsonValue] | None] = []
    async for value in limit_response_stream(upstream(), 2):
        event = parse_sse_data_json(value)
        if event and event["type"] == "response.incomplete":
            assert settled
        events.append(event)
    assert settled
    assert events[-1] is not None and events[-1]["type"] == "response.incomplete"
    response = cast(dict[str, JsonValue], events[-1]["response"])
    assert response["incomplete_details"] == {"reason": "max_output_tokens"}


@pytest.mark.parametrize("service_path", ["direct", "http"])
@pytest.mark.parametrize("cap", [None, 2])
@pytest.mark.parametrize("disconnect_after", [1, 2])
async def test_service_disconnect_closes_real_retry_upstream(
    monkeypatch: pytest.MonkeyPatch, db_setup: bool, service_path: str, cap: int | None, disconnect_after: int
) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="acc_disconnect",
        chatgpt_account_id="workspace-disconnect",
        email="disconnect@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()

    closed = False

    async def upstream(*_args, **_kwargs) -> AsyncGenerator[str, None]:
        nonlocal closed
        try:
            yield block("response.created", response={"id": "resp_disconnect"})
            yield block("response.output_text.delta", delta="abc", output_index=0)
            yield block("response.completed", response={"status": "completed", "output": []})
        finally:
            closed = True

    inner_stream = upstream()
    monkeypatch.setattr(proxy_service, "core_stream_responses", lambda *_args, **_kwargs: inner_stream)
    service = proxy_service.ProxyService(_proxy_repo_context)
    payload = ResponsesRequest(model="gpt-5.1", input="hi", instructions="hi")
    public_stream = service.stream_http_responses if service_path == "http" else service.stream_responses
    stream = cast(AsyncGenerator[str, None], limit_response_stream(public_stream(payload, {}), cap))
    try:
        for _ in range(disconnect_after):
            event = parse_sse_data_json(await anext(stream))
            assert event is not None and event["type"] != "response.failed"
        await stream.aclose()
        assert closed
        assert inner_stream.ag_frame is None
    finally:
        await stream.aclose()
        await inner_stream.aclose()
