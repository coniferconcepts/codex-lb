"""Bound pre-listen startup work so KeepAlive sees a typed fail_startup reason.

The CLI listen watcher (`reason=listen_timeout`) is a last-resort: it fires when
the process never binds the port. Lifespan work that can block on SQLite or
leader-election MUST finish or fail inside the same ``CODEX_LB_LISTEN_TIMEOUT_SECONDS``
window, minus a short margin so this module can log and ``os._exit`` before the
watcher prints the opaque ``listen_timeout`` line.

``start_startup_budget_watch`` covers the whole pre-yield window, including
sync work that would otherwise freeze ``asyncio.wait``. Start it from lifespan
only so a uvicorn hang before lifespan still emits ``listen_timeout``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar

DEFAULT_LISTEN_TIMEOUT_SECONDS = 30.0
# Matches app.db.session._SQLITE_BUSY_TIMEOUT_SECONDS; kept local to avoid import cycles.
_DEFAULT_SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
LISTEN_WATCHER_EXIT_MARGIN_SECONDS = 5.0
_MIN_PHASE_SECONDS = 0.05

REASON_STARTUP_BUDGET_EXCEEDED = "startup_budget"
REASON_DB_MIGRATE = "db_migrate"
REASON_LEADER_LEASE = "leader_lease"
REASON_LISTEN_TIMEOUT = "listen_timeout"
REASON_LIVE_TIMEOUT = "live_timeout"
REASON_LIVE_HUNG = "live_hung"

_T = TypeVar("_T")

_watch_started_at: float | None = None
_pre_listen_complete: bool = False
_phase_reason: str = REASON_STARTUP_BUDGET_EXCEEDED
_budget_watch_started: bool = False
_budget_watch_thread: threading.Thread | None = None
_budget_watch_stop = threading.Event()


class StartupBudgetExceeded(TimeoutError):
    """Pre-listen work did not finish inside the listen-watcher's remaining budget."""

    def __init__(self, reason: str = REASON_STARTUP_BUDGET_EXCEEDED) -> None:
        self.reason = reason
        super().__init__(reason)


def format_fail_startup(reason: str) -> str:
    return f"category=fail_startup reason={reason}"


def listen_timeout_seconds(environ: Mapping[str, str] | None = None) -> float:
    raw = ((environ or os.environ).get("CODEX_LB_LISTEN_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_LISTEN_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LISTEN_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_LISTEN_TIMEOUT_SECONDS


def listen_watcher_margin_seconds(timeout_seconds: float | None = None) -> float:
    """Leave enough time to log and _exit before the listen watcher fires.

    The default margin is 5s. When the operator shortens the listen timeout
    below that (tests use sub-second values), keep a proportional slice so the
    lifespan budget is still positive.
    """
    timeout = DEFAULT_LISTEN_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    if timeout <= 0:
        return 0.0
    return min(LISTEN_WATCHER_EXIT_MARGIN_SECONDS, max(_MIN_PHASE_SECONDS, timeout * 0.15))


def mark_listen_watch_started(*, monotonic: Callable[[], float] = time.monotonic) -> None:
    """Anchor the budget clock when the CLI listen watcher starts.

    Idempotent: the first call wins so lifespan startup cannot reset the clock
    after the watcher thread is already counting down.
    """
    global _watch_started_at
    if _watch_started_at is None:
        _watch_started_at = monotonic()


def mark_pre_listen_complete() -> None:
    """Restore default SQLite busy_timeout after the server may bind."""
    global _pre_listen_complete
    _pre_listen_complete = True
    _budget_watch_stop.set()


def set_startup_phase(reason: str) -> None:
    """Record which typed fail_startup reason the budget watch should emit."""
    global _phase_reason
    _phase_reason = reason


def start_startup_budget_watch() -> None:
    """Fail closed if pre-listen work blocks the event loop until listen_timeout.

    The asyncio ``init_db`` wrapper cannot fire while a sync SQLite integrity
    check (or later lifespan work) owns the loop. This daemon thread still
    ``os._exit``s with the current phase at ``timeout - margin``. Start it from
    lifespan only: a uvicorn hang before lifespan must keep ``listen_timeout``.
    """
    global _budget_watch_started, _budget_watch_thread
    mark_listen_watch_started()
    if _budget_watch_started:
        return
    _budget_watch_started = True
    _budget_watch_stop.clear()
    thread = threading.Thread(
        target=_startup_budget_watch_loop,
        name="codex-lb-startup-budget",
        daemon=True,
    )
    _budget_watch_thread = thread
    thread.start()


def _startup_budget_watch_loop() -> None:
    timeout = listen_timeout_seconds()
    margin = listen_watcher_margin_seconds(timeout)
    started = _watch_started_at
    if started is None:  # pragma: no cover - start_startup_budget_watch always marks
        started = time.monotonic()
    deadline = started + timeout - margin
    while True:
        if _pre_listen_complete or _budget_watch_stop.is_set():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        _budget_watch_stop.wait(timeout=min(_MIN_PHASE_SECONDS, remaining))
    if _pre_listen_complete or _budget_watch_stop.is_set():
        return
    exit_startup_budget_exceeded(_phase_reason)


def release_startup_budget() -> None:
    """Forget the listen-watch clock after the process stops serving (and in tests)."""
    global _watch_started_at, _pre_listen_complete, _budget_watch_started
    global _budget_watch_thread, _phase_reason
    _budget_watch_stop.set()
    _pre_listen_complete = True
    thread = _budget_watch_thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=0.5)
    _budget_watch_thread = None
    _watch_started_at = None
    _budget_watch_started = False
    _phase_reason = REASON_STARTUP_BUDGET_EXCEEDED
    _pre_listen_complete = False
    _budget_watch_stop.clear()


def reset_startup_budget_for_tests() -> None:
    release_startup_budget()


def remaining_lifespan_budget_seconds(*, monotonic: Callable[[], float] = time.monotonic) -> float:
    """Seconds left for pre-listen work after reserving the watcher-exit margin."""
    mark_listen_watch_started(monotonic=monotonic)
    started = _watch_started_at
    if started is None:  # pragma: no cover - mark_listen_watch_started always sets it
        started = monotonic()
    timeout = listen_timeout_seconds()
    remaining = timeout - (monotonic() - started) - listen_watcher_margin_seconds(timeout)
    return remaining


def is_pre_listen_startup() -> bool:
    return _watch_started_at is not None and not _pre_listen_complete


def sqlite_busy_timeout_seconds() -> float | None:
    """Cap SQLite busy waits to the remaining pre-listen budget, if any.

    ``None`` means callers keep their ordinary default (30s on app engines).
    """
    if not is_pre_listen_startup():
        return None
    remaining = remaining_lifespan_budget_seconds()
    if remaining <= 0:
        return _MIN_PHASE_SECONDS
    return max(_MIN_PHASE_SECONDS, min(_DEFAULT_SQLITE_BUSY_TIMEOUT_SECONDS, remaining))


def migration_lock_timeout_seconds() -> float | None:
    """Cap the cross-process migration lock wait to the remaining budget.

    ``None`` leaves ``database_migration_lock_timeout_seconds`` (default 300s)
    in place when the listen budget is not active.
    """
    if not is_pre_listen_startup():
        return None
    remaining = remaining_lifespan_budget_seconds()
    return max(_MIN_PHASE_SECONDS, remaining)


def sqlalchemy_connect_args_for_url(url: str) -> dict[str, object]:
    if not url.startswith("sqlite"):
        return {}
    seconds = sqlite_busy_timeout_seconds()
    if seconds is None:
        return {}
    return {"timeout": seconds}


def exit_startup_budget_exceeded(reason: str = REASON_STARTUP_BUDGET_EXCEEDED) -> None:
    print(format_fail_startup(reason), file=sys.stderr, flush=True)
    os._exit(1)


async def run_within_startup_budget(
    awaitable: Awaitable[_T],
    *,
    timeout_seconds: float,
    reason: str = REASON_STARTUP_BUDGET_EXCEEDED,
) -> _T:
    """Run ``awaitable`` but do not wait out a wedged SQLite thread on timeout.

    ``asyncio.wait_for`` awaits cancellation unwind, which can sit behind
    ``busy_timeout``. ``asyncio.wait`` returns on the deadline; the task is
    cancelled best-effort and abandoned so the caller can raise or ``_exit``.
    """
    if timeout_seconds <= 0:
        raise StartupBudgetExceeded(reason)
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    if task in done:
        return task.result()
    task.cancel()
    await asyncio.wait({task}, timeout=_MIN_PHASE_SECONDS)
    raise StartupBudgetExceeded(reason)


async def init_db_within_startup_budget(
    *,
    budget_seconds: float | None = None,
    init_db: Callable[[], Awaitable[None]] | None = None,
) -> None:
    runner = init_db
    if runner is None:
        from app.db.session import init_db as runner
    timeout = remaining_lifespan_budget_seconds() if budget_seconds is None else budget_seconds
    await run_within_startup_budget(runner(), timeout_seconds=timeout, reason=REASON_DB_MIGRATE)


async def try_acquire_within_startup_budget(
    try_acquire: Callable[[], Awaitable[bool]],
) -> bool:
    """Bound a leader-lease acquire that can block on SQLite during pre-listen.

    After bind, returns the inner result unchanged. On a pre-listen timeout the
    acquire is abandoned so lifespan can continue toward listen rather than
    stacking ``busy_timeout`` waits.
    """
    if not is_pre_listen_startup():
        return await try_acquire()
    remaining = remaining_lifespan_budget_seconds()
    try:
        return await run_within_startup_budget(
            try_acquire(),
            timeout_seconds=remaining,
            reason=REASON_LEADER_LEASE,
        )
    except StartupBudgetExceeded:
        return False
