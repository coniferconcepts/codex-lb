from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from app import cli
from app.core.runtime_logging import UtcDefaultFormatter

pytestmark = pytest.mark.unit


def test_main_passes_timestamped_log_config(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    log_config = kwargs["log_config"]
    assert isinstance(log_config, dict)
    formatters = log_config["formatters"]
    assert formatters["default"]["fmt"].startswith("%(asctime)s ")
    assert formatters["access"]["fmt"].startswith("%(asctime)s ")


def test_main_prefers_codex_lb_bind_host_env(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs

    monkeypatch.setenv("CODEX_LB_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert captured["kwargs"]["host"] == "127.0.0.1"


def test_main_rejects_nonlocal_bind_without_explicit_override(monkeypatch):
    monkeypatch.delenv("CODEX_LB_ALLOW_NONLOCAL_BIND", raising=False)
    monkeypatch.setattr(sys, "argv", ["codex-lb", "--host", "0.0.0.0"])

    with pytest.raises(SystemExit, match="Refusing non-local bind host"):
        cli.main()


def test_main_allows_nonlocal_bind_with_explicit_override(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs

    monkeypatch.setenv("CODEX_LB_ALLOW_NONLOCAL_BIND", "true")
    monkeypatch.setattr(sys, "argv", ["codex-lb", "--host", "0.0.0.0"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert captured["kwargs"]["host"] == "0.0.0.0"


def test_watch_listen_or_timeout_returns_listening() -> None:
    seen: list[str] = []

    def is_listening(host: str, port: int) -> bool:
        seen.append(f"{host}:{port}")
        return True

    result = cli.watch_listen_or_timeout(
        host="127.0.0.1",
        port=2455,
        timeout_seconds=1,
        is_listening=is_listening,
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
        should_stop=lambda: False,
        on_timeout=lambda: pytest.fail("timeout should not fire"),
    )
    assert result == "listening"
    assert seen == ["127.0.0.1:2455"]


def test_watch_listen_or_timeout_calls_on_timeout() -> None:
    fired = {"n": 0}
    ticks = {"n": 0}

    def monotonic() -> float:
        ticks["n"] += 1
        return 0.0 if ticks["n"] < 3 else 10.0

    result = cli.watch_listen_or_timeout(
        host="127.0.0.1",
        port=9,
        timeout_seconds=1,
        is_listening=lambda host, port: False,
        sleep=lambda _: None,
        monotonic=monotonic,
        should_stop=lambda: False,
        on_timeout=lambda: fired.__setitem__("n", fired["n"] + 1),
    )
    assert result == "timeout"
    assert fired["n"] == 1


def test_watch_listen_or_timeout_stops_before_timeout() -> None:
    result = cli.watch_listen_or_timeout(
        host="127.0.0.1",
        port=9,
        timeout_seconds=1,
        is_listening=lambda host, port: False,
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
        should_stop=lambda: True,
        on_timeout=lambda: pytest.fail("timeout should not fire"),
    )
    assert result == "stopped"


def test_main_starts_listen_watch_and_stops_it(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setenv("CODEX_LB_LISTEN_TIMEOUT_SECONDS", "30")

    cli.main()

    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 2455


def test_subprocess_exits_when_listen_never_happens(tmp_path: Any) -> None:
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
    # sitecustomize-style: import hang by starting python -c after inserting path
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
    assert "listen_timeout" in result.stderr


def test_utc_default_formatter_formats_without_converter_binding_error():
    formatter = UtcDefaultFormatter(
        fmt="%(asctime)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        use_colors=None,
    )
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.created = 0.0

    assert formatter.format(record) == "1970-01-01T00:00:00Z hello"
