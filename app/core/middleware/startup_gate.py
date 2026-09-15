from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import startup as startup_module

_STARTUP_GATE_WAIT_SECONDS = 30.0


class StartupGateMiddleware:
    """Hold non-live traffic until post-listen SQLite warmup finishes.

    Lifespan yields after fail-closed init_db so uvicorn can bind (issue #7).
    /health/live answers immediately. Other routes wait for warmup so TestClient
    and API callers do not race a still-migrating schema.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type not in {"http", "websocket"} or not startup_module.is_startup_warmup_pending():
            await self.app(scope, receive, send)
            return
        if scope_type == "http" and startup_module.http_path_allowed_before_startup(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        await startup_module.wait_for_startup_complete(timeout_seconds=_STARTUP_GATE_WAIT_SECONDS)
        if not startup_module.is_startup_warmup_pending():
            await self.app(scope, receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1013, "reason": "Service is starting"})
            return
        response = JSONResponse(
            status_code=503,
            content={
                "error": {
                    "type": "service_unavailable",
                    "message": "Service is starting",
                }
            },
        )
        await response(scope, receive, send)
