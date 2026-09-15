from __future__ import annotations

import pytest

from app.core.middleware.startup_gate import StartupGateMiddleware
from app.core.startup import http_path_allowed_before_startup, is_startup_complete

pytestmark = pytest.mark.unit


class _RecordingApp:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        self.calls += 1
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})


async def _http_status(app, path: str) -> int:
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "GET", "path": path, "headers": []}
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    return int(start["status"])


def test_health_live_is_allowed_before_startup() -> None:
    assert http_path_allowed_before_startup("/health/live") is True
    assert http_path_allowed_before_startup("/health/startup") is True
    assert http_path_allowed_before_startup("/health/ready") is False
    assert http_path_allowed_before_startup("/v1/responses") is False


@pytest.mark.asyncio
async def test_startup_gate_allows_live_and_blocks_ready_until_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.startup as startup_module

    inner = _RecordingApp()
    gate = StartupGateMiddleware(inner)
    startup_module.reset_startup_complete()
    try:
        assert is_startup_complete() is False
        assert await _http_status(gate, "/health/live") == 200
        assert inner.calls == 1

        async def _timeout(*, timeout_seconds: float = 30.0) -> bool:
            return False

        monkeypatch.setattr(startup_module, "wait_for_startup_complete", _timeout)
        assert await _http_status(gate, "/health/ready") == 503
        assert inner.calls == 1
        startup_module.mark_startup_complete()
        assert await _http_status(gate, "/health/ready") == 200
        assert inner.calls == 2
    finally:
        startup_module._startup_complete = False
        startup_module._startup_complete_event = None
