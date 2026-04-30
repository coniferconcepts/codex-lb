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
