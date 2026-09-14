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
import time
from typing import Any, Callable

import pytest

from app import cli

pytestmark = pytest.mark.unit

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
    pytest.xfail(
        "No lifespan startup budget exit helper found on app.cli or app.core.startup_budget (issue #7)"
    )


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
    pytest.xfail(
        "No lifespan budget fail_startup reason constant found on app.core.startup_budget (issue #7)"
    )


def _resolve_init_db_budget_wrapper() -> Callable[..., Any]:
    startup_budget = _try_import_startup_budget()
    for name in _INIT_DB_BUDGET_WRAPPER_NAMES:
        candidate = getattr(startup_budget, name, None)
        if callable(candidate):
            return candidate
    pytest.xfail(
        "No init_db startup budget wrapper found on app.core.startup_budget (issue #7)"
    )


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

    with pytest.raises(budget_exc_type):
        await wrapper(budget_seconds=budget_seconds)

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
        "import time\n"
        "import uvicorn\n"
        "def run(*args, **kwargs):\n"
        "    time.sleep(8)\n"
        "uvicorn.run = run\n",
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
            "import hang_uvicorn, sys; "
            f"sys.argv=['codex-lb','--port','{free_port}']; "
            "from app.cli import main; main()",
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert _CANONICAL_LISTEN_TIMEOUT_LINE in result.stderr
