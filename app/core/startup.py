"""Startup state management."""

from __future__ import annotations

import asyncio

_startup_complete: bool = False
_startup_complete_event: asyncio.Event | None = None
_bridge_registration_complete: bool = False
_bridge_durable_schema_ready: bool = False
_bridge_registration_event: asyncio.Event | None = None

# Bound before remaining SQLite warmup (issue #7). Only these HTTP paths may
# run while init_db / schedulers still own the database; /health/live is the
# LaunchAgent probe.
_HTTP_PATHS_ALLOWED_BEFORE_STARTUP = frozenset(
    {
        "/health/live",
        "/health/startup",
    }
)


def is_startup_complete() -> bool:
    return _startup_complete


def is_startup_warmup_pending() -> bool:
    """True only after real lifespan starts and before warmup marks complete."""
    return _startup_complete_event is not None and not _startup_complete


def http_path_allowed_before_startup(path: str) -> bool:
    return path in _HTTP_PATHS_ALLOWED_BEFORE_STARTUP


def reset_startup_complete() -> None:
    global _startup_complete, _startup_complete_event
    _startup_complete = False
    _startup_complete_event = asyncio.Event()


def mark_startup_complete() -> None:
    global _startup_complete
    _startup_complete = True
    if _startup_complete_event is not None:
        _startup_complete_event.set()


async def wait_for_startup_complete(timeout_seconds: float = 30.0) -> bool:
    if _startup_complete:
        return True
    if _startup_complete_event is None:
        return False
    try:
        await asyncio.wait_for(_startup_complete_event.wait(), timeout=timeout_seconds)
    except TimeoutError:
        return False
    return True


def reset_bridge_registration() -> None:
    global _bridge_registration_complete, _bridge_durable_schema_ready, _bridge_registration_event
    _bridge_registration_complete = False
    _bridge_durable_schema_ready = False
    _bridge_registration_event = asyncio.Event()


def mark_bridge_durable_schema_ready() -> None:
    global _bridge_durable_schema_ready
    _bridge_durable_schema_ready = True


def mark_bridge_registration_complete() -> None:
    global _bridge_registration_complete
    _bridge_registration_complete = True
    if _bridge_registration_event is not None:
        _bridge_registration_event.set()


async def wait_for_bridge_registration(timeout_seconds: float) -> bool:
    if _bridge_registration_complete:
        return True
    if _bridge_registration_event is None:
        return False
    try:
        await asyncio.wait_for(_bridge_registration_event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False
    return True
