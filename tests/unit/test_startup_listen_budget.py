"""Contract tests for listen-timeout vs lifespan startup budget (issue #7).

Intended implementer surface (adjust tests when names land):

- ``app.cli._exit_listen_timeout`` — port never binds (unchanged contract).
- ``app.cli`` or ``app.core.startup_budget`` — typed ``fail_startup`` reason when
  lifespan/SQLite/leader-election exceeds ``CODEX_LB_LISTEN_TIMEOUT_SECONDS``.
- ``app.core.startup_budget`` — budget helpers wrapping ``init_db`` and other
  startup phases so work cannot run unbounded relative to the listen timeout.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from typing import Any, Callable

import pytest

from app import cli
from app.core import startup_budget as startup_budget_module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_startup_budget() -> None:
    startup_budget_module.reset_startup_budget_for_tests()
    yield
    startup_budget_module.reset_startup_budget_for_tests()


_CANONICAL_LISTEN_TIMEOUT_LINE = "category=fail_startup reason=listen_timeout"

_LIFESPAN_EXIT_HELPER_NAMES = (
    "_exit_lifespan_budget_exceeded",
    "_exit_lifespan_startup_budget_exceeded",
    "_exit_startup_budget_exceeded",
    "_exit_fail_startup_lifespan_budget",
)

_LIFESPAN_REASON_ATTRS = (
    "REASON_LIFESPAN_BUDGET_EXCEEDED",
    "REASON_STARTUP_BUDGET_EXCEEDED",
    "LIFESPAN_BUDGET_EXCEEDED_REASON",
)

_INIT_DB_BUDGET_WRAPPER_NAMES = (
    "init_db_within_startup_budget",
    "init_db_with_startup_budget",
    "run_init_db_with_startup_budget",
)


def _try_import_startup_budget() -> Any:
    try:
        from app.core import startup_budget as module

        return module
    except ImportError:
        pytest.xfail("app.core.startup_budget is not implemented yet (issue #7)")


def _resolve_lifespan_exit_helper() -> tuple[Callable[[], None], str]:
    for name in _LIFESPAN_EXIT_HELPER_NAMES:
        candidate = getattr(cli, name, None)
        if callable(candidate):
            return candidate, name
    startup_budget = _try_import_startup_budget()
    for name in ("exit_lifespan_budget_exceeded", "exit_startup_budget_exceeded"):
        candidate = getattr(startup_budget, name, None)
        if callable(candidate):
            return candidate, f"startup_budget.{name}"
    pytest.xfail("No lifespan startup budget exit helper found on app.cli or app.core.startup_budget (issue #7)")


def _resolve_lifespan_budget_reason() -> str:
    startup_budget = _try_import_startup_budget()
    for attr in _LIFESPAN_REASON_ATTRS:
        value = getattr(startup_budget, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    formatter = getattr(startup_budget, "format_fail_startup", None)
    if callable(formatter):
        sample = formatter("lifespan_budget_exceeded")
        if "reason=lifespan_budget_exceeded" in sample:
            return "lifespan_budget_exceeded"
    pytest.xfail("No lifespan budget fail_startup reason constant found on app.core.startup_budget (issue #7)")


def _resolve_init_db_budget_wrapper() -> Callable[..., Any]:
    startup_budget = _try_import_startup_budget()
    for name in _INIT_DB_BUDGET_WRAPPER_NAMES:
        candidate = getattr(startup_budget, name, None)
        if callable(candidate):
            return candidate
    pytest.xfail("No init_db startup budget wrapper found on app.core.startup_budget (issue #7)")


def test_exit_live_timeout_emits_typed_fail_startup_not_listen_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, int] = {}

    def fake_exit(code: int) -> None:
        captured["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr(cli.os, "_exit", fake_exit)

    with pytest.raises(SystemExit):
        cli._exit_live_timeout()

    stderr = capsys.readouterr().err
    assert "category=fail_startup" in stderr
    assert "reason=live_timeout" in stderr
    assert "reason=listen_timeout" not in stderr
    assert captured["code"] == 1


def test_exit_listen_timeout_emits_canonical_fail_startup_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, int] = {}

    def fake_exit(code: int) -> None:
        captured["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr(cli.os, "_exit", fake_exit)

    with pytest.raises(SystemExit) as exc_info:
        cli._exit_listen_timeout()

    assert exc_info.value.code == 1
    assert captured["code"] == 1
    assert capsys.readouterr().err.strip() == _CANONICAL_LISTEN_TIMEOUT_LINE


def test_listen_timeout_fail_startup_reason_is_not_lifespan_budget_reason() -> None:
    lifespan_reason = _resolve_lifespan_budget_reason()
    assert "fail_startup" in _CANONICAL_LISTEN_TIMEOUT_LINE
    assert lifespan_reason != "listen_timeout"
    assert f"reason={lifespan_reason}" not in _CANONICAL_LISTEN_TIMEOUT_LINE


def test_lifespan_budget_exit_emits_typed_fail_startup_not_listen_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_helper, _helper_name = _resolve_lifespan_exit_helper()
    lifespan_reason = _resolve_lifespan_budget_reason()
    assert lifespan_reason != "listen_timeout"

    captured: dict[str, int] = {}

    def fake_exit(code: int) -> None:
        captured["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr(cli.os, "_exit", fake_exit)

    with pytest.raises(SystemExit):
        exit_helper()

    stderr = capsys.readouterr().err
    assert "category=fail_startup" in stderr
    assert f"reason={lifespan_reason}" in stderr
    assert "reason=listen_timeout" not in stderr
    assert captured["code"] == 1


@pytest.mark.asyncio
async def test_init_db_wrapper_fails_within_listen_budget_when_init_db_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _resolve_init_db_budget_wrapper()
    budget_seconds = 0.08
    started = time.monotonic()

    async def slow_init_db() -> None:
        await asyncio.sleep(5.0)

    from app.db import session as session_module

    monkeypatch.setattr(session_module, "init_db", slow_init_db)

    budget_exc_type = getattr(
        _try_import_startup_budget(),
        "StartupBudgetExceeded",
        None,
    )
    if budget_exc_type is None:
        pytest.xfail("StartupBudgetExceeded not defined on app.core.startup_budget (issue #7)")

    with pytest.raises(budget_exc_type) as exc_info:
        await wrapper(budget_seconds=budget_seconds)

    assert exc_info.value.reason == startup_budget_module.REASON_DB_MIGRATE
    assert exc_info.value.reason != "listen_timeout"

    elapsed = time.monotonic() - started
    assert elapsed < budget_seconds + 0.35, (
        "init_db must not be allowed to run unbounded relative to the listen startup budget"
    )


def test_subprocess_listen_never_happens_stderr_matches_fail_startup_contract(
    tmp_path: Any,
) -> None:
    """Characterization aligned with tests/unit/test_cli.py; do not weaken the contract."""
    hang = tmp_path / "hang_uvicorn.py"
    hang.write_text(
        "import time\nimport uvicorn\ndef run(*args, **kwargs):\n    time.sleep(8)\nuvicorn.run = run\n",
        encoding="utf-8",
    )
    import os
    import subprocess
    from pathlib import Path

    env = os.environ.copy()
    env["CODEX_LB_LISTEN_TIMEOUT_SECONDS"] = "0.8"
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    root = Path(__file__).resolve().parents[2]
    sock = __import__("socket").socket()
    sock.bind(("127.0.0.1", 0))
    free_port = int(sock.getsockname()[1])
    sock.close()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import hang_uvicorn, sys; sys.argv=['codex-lb','--port','{free_port}']; from app.cli import main; main()",
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert _CANONICAL_LISTEN_TIMEOUT_LINE in result.stderr


def test_default_listen_timeout_seconds_is_unchanged() -> None:
    assert startup_budget_module.DEFAULT_LISTEN_TIMEOUT_SECONDS == 30.0
    assert startup_budget_module.listen_timeout_seconds({}) == 30.0
    assert startup_budget_module.listen_timeout_seconds({"CODEX_LB_LISTEN_TIMEOUT_SECONDS": "90"}) == 90.0

    assert startup_budget_module.REASON_DB_MIGRATE != "listen_timeout"
    assert startup_budget_module.format_fail_startup(startup_budget_module.REASON_DB_MIGRATE) == (
        "category=fail_startup reason=db_migrate"
    )
    assert startup_budget_module.format_fail_startup(startup_budget_module.REASON_STARTUP_BUDGET_EXCEEDED) == (
        "category=fail_startup reason=startup_budget"
    )


@pytest.mark.asyncio
async def test_locked_sqlite_busy_wait_cannot_overrun_startup_budget(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    from app.db.sqlite_utils import check_sqlite_integrity

    db_path = tmp_path / "locked.db"
    bootstrap = sqlite3.connect(str(db_path))
    bootstrap.execute("CREATE TABLE t(x INTEGER)")
    bootstrap.commit()
    bootstrap.close()

    holder = sqlite3.connect(str(db_path), timeout=0, isolation_level=None)
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        monkeypatch.setenv("CODEX_LB_LISTEN_TIMEOUT_SECONDS", "1")
        startup_budget_module.mark_listen_watch_started()
        busy_timeout = startup_budget_module.sqlite_busy_timeout_seconds()
        assert busy_timeout is not None
        assert busy_timeout <= 1.0

        started = time.monotonic()
        result = await asyncio.to_thread(check_sqlite_integrity, db_path)
        elapsed = time.monotonic() - started
    finally:
        holder.close()

    assert elapsed < 2.5, "contended SQLite must not wait out the 30s app busy_timeout"
    assert result.ok is False
    details = (result.details or "").lower()
    assert "locked" in details or "busy" in details


@pytest.mark.asyncio
async def test_try_acquire_within_startup_budget_returns_false_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_acquire() -> bool:
        await asyncio.sleep(5.0)
        return True

    monkeypatch.setenv("CODEX_LB_LISTEN_TIMEOUT_SECONDS", "0.4")
    startup_budget_module.mark_listen_watch_started()
    started = time.monotonic()
    acquired = await startup_budget_module.try_acquire_within_startup_budget(slow_acquire)
    elapsed = time.monotonic() - started

    assert acquired is False
    assert elapsed < 1.5


def test_startup_budget_watch_beats_listen_timeout_when_caller_blocks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Post-init_db pre-yield work must not sit until the CLI listen watcher."""
    fired = threading.Event()
    codes: list[int] = []

    def fake_exit(code: int) -> None:
        codes.append(code)
        fired.set()

    monkeypatch.setenv("CODEX_LB_LISTEN_TIMEOUT_SECONDS", "0.4")
    monkeypatch.setattr(startup_budget_module.os, "_exit", fake_exit)
    startup_budget_module.mark_listen_watch_started()
    startup_budget_module.set_startup_phase(startup_budget_module.REASON_STARTUP_BUDGET_EXCEEDED)
    startup_budget_module.start_startup_budget_watch()
    time.sleep(2.0)

    assert fired.wait(0.1)
    assert codes == [1]
    stderr = capsys.readouterr().err
    assert "category=fail_startup reason=startup_budget" in stderr
    assert "reason=listen_timeout" not in stderr


def test_startup_budget_watch_emits_db_migrate_while_init_db_phase(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fired = threading.Event()

    def fake_exit(_code: int) -> None:
        fired.set()

    monkeypatch.setenv("CODEX_LB_LISTEN_TIMEOUT_SECONDS", "0.4")
    monkeypatch.setattr(startup_budget_module.os, "_exit", fake_exit)
    startup_budget_module.mark_listen_watch_started()
    startup_budget_module.set_startup_phase(startup_budget_module.REASON_DB_MIGRATE)
    startup_budget_module.start_startup_budget_watch()

    assert fired.wait(2.0)
    stderr = capsys.readouterr().err
    assert "category=fail_startup reason=db_migrate" in stderr
    assert "reason=listen_timeout" not in stderr


def test_startup_budget_watch_does_not_exit_after_pre_listen_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fired = threading.Event()

    def fake_exit(_code: int) -> None:
        fired.set()

    monkeypatch.setenv("CODEX_LB_LISTEN_TIMEOUT_SECONDS", "0.4")
    monkeypatch.setattr(startup_budget_module.os, "_exit", fake_exit)
    startup_budget_module.mark_listen_watch_started()
    startup_budget_module.start_startup_budget_watch()
    startup_budget_module.mark_pre_listen_complete()
    time.sleep(0.8)

    assert not fired.is_set()
