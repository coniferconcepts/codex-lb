from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.codex_sessions_retag import RetagResult, default_codex_home, retag_codex_sessions
from app.core.bind_policy import assert_bind_host_allowed, resolve_allow_nonlocal_bind, resolve_bind_host
from app.core.startup_budget import (
    listen_timeout_seconds as _listen_timeout_seconds,
)
from app.core.startup_budget import (
    mark_listen_watch_started,
    release_startup_budget,
)

if TYPE_CHECKING:
    from app.core.runtime_logging import LogConfig


class _CliHelpFormatter(argparse.HelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=36, width=120)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the codex-lb API server.",
        formatter_class=_CliHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    codex_sessions = subparsers.add_parser(
        "codex-sessions",
        help="Manage local Codex session metadata.",
        formatter_class=_CliHelpFormatter,
    )
    codex_sessions_subparsers = codex_sessions.add_subparsers(dest="codex_sessions_command")
    retag = codex_sessions_subparsers.add_parser(
        "retag",
        help="Re-tag Codex threads between the openai and codex-lb model providers.",
        formatter_class=_CliHelpFormatter,
    )
    retag.add_argument(
        "--from", dest="source_provider", metavar="PROVIDER", required=True, help="Provider tag to replace."
    )
    retag.add_argument("--to", dest="target_provider", metavar="PROVIDER", required=True, help="Provider tag to write.")
    retag.add_argument(
        "--codex-home",
        type=Path,
        metavar="PATH",
        default=None,
        help="Codex data directory. Defaults to CODEX_HOME, /codex-home in Docker, or ~/.codex.",
    )
    retag.add_argument("--dry-run", action="store_true", help="Show what would change without writing files.")
    retag.add_argument(
        "--yes",
        action="store_true",
        help="Confirm that Codex/Codex CLI is closed and allow a non-interactive write.",
    )

    parser.add_argument("--host", default=resolve_bind_host(os.environ))
    parser.add_argument("--port", default=os.getenv("PORT", "2455"))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))
    parser.add_argument(
        "--timeout-keep-alive",
        default=os.getenv("UVICORN_TIMEOUT_KEEP_ALIVE", "7200"),
        help=(
            "Seconds to keep idle HTTP connections open. Codex CLI reuses local "
            "connections for large compact POSTs; short keepalive windows can leave the "
            "client writing to a stale socket before the request reaches the app."
        ),
    )
    parser.add_argument(
        "--ws-max-size",
        default=os.getenv("UVICORN_WS_MAX_SIZE", str(128 * 1024 * 1024)),
        help=(
            "Maximum decompressed size in bytes of a single incoming websocket message. "
            "Codex clients resend the full conversation history (inline screenshots "
            "included) as one response.create message after a reconnect; messages above "
            "this budget are closed at the protocol layer with 1009 before the "
            "application-level slimming guard can run."
        ),
    )

    return parser.parse_args(argv)


DEFAULT_LIVE_HANG_SECONDS = 60.0
LIVE_PROBE_TIMEOUT_SECONDS = 1.0
LIVE_PROBE_BODY_LIMIT_BYTES = 4096


def port_is_listening(host: str, port: int, timeout: float = 0.3) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def live_hang_seconds(environ: Mapping[str, str] | None = None) -> float:
    raw = ((environ or os.environ).get("CODEX_LB_LIVE_HANG_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_LIVE_HANG_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LIVE_HANG_SECONDS
    return value if value > 0 else DEFAULT_LIVE_HANG_SECONDS


def _loopback_live_url(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"http://[{host}]:{port}/health/live"
    return f"http://{host}:{port}/health/live"


def live_is_healthy(host: str, port: int, timeout: float = LIVE_PROBE_TIMEOUT_SECONDS) -> bool:
    """GET /health/live from a thread. Must not use the app event loop."""
    url = _loopback_live_url(host, port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode())
            if status < 200 or status >= 300:
                return False
            return bool(resp.read(LIVE_PROBE_BODY_LIMIT_BYTES))
    except (OSError, urllib.error.URLError, ValueError):
        return False


def watch_listen_or_timeout(
    *,
    host: str,
    port: int,
    timeout_seconds: float,
    is_listening: Callable[[str, int], bool] = port_is_listening,
    is_live_healthy: Callable[[str, int], bool] = live_is_healthy,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool],
    on_timeout: Callable[[], None],
    on_live_timeout: Callable[[], None] | None = None,
    on_live_hang: Callable[[], None] | None = None,
    keep_watching_after_live: bool = False,
    live_hang_seconds: float | None = None,
) -> str:
    """Wait for TCP listen AND /health/live 2xx. TCP-only is not success.

    After the first live 2xx, optionally keep probing so a later event-loop
    hang exits instead of remaining launchd-owned.
    """
    deadline = monotonic() + timeout_seconds
    saw_listen = False
    last_live_ok: float | None = None
    hang_budget = DEFAULT_LIVE_HANG_SECONDS if live_hang_seconds is None else live_hang_seconds
    live_fail = on_live_timeout or on_timeout
    hang_fail = on_live_hang or on_live_timeout or on_timeout
    while True:
        now = monotonic()
        if should_stop():
            return "stopped"
        listening = is_listening(host, port)
        live_ok = False
        if listening:
            saw_listen = True
            live_ok = is_live_healthy(host, port)
        if live_ok:
            last_live_ok = now
            if not keep_watching_after_live:
                return "listening"
        elif last_live_ok is None:
            if now >= deadline:
                if should_stop():
                    return "stopped"
                if saw_listen:
                    live_fail()
                    return "live_timeout"
                on_timeout()
                return "timeout"
        elif hang_budget > 0 and now - last_live_ok >= hang_budget:
            hang_fail()
            return "live_hung"
        sleep(min(0.2, max(0.05, timeout_seconds / 10)))


def _exit_listen_timeout() -> None:
    print("category=fail_startup reason=listen_timeout", file=sys.stderr)
    os._exit(1)


def _exit_live_timeout() -> None:
    from app.core.startup_budget import REASON_LIVE_TIMEOUT, format_fail_startup

    print(format_fail_startup(REASON_LIVE_TIMEOUT), file=sys.stderr, flush=True)
    os._exit(1)


def _exit_live_hung() -> None:
    from app.core.startup_budget import REASON_LIVE_HUNG, format_fail_startup

    print(format_fail_startup(REASON_LIVE_HUNG), file=sys.stderr, flush=True)
    os._exit(1)


def _exit_lifespan_budget_exceeded() -> None:
    from app.core.startup_budget import REASON_STARTUP_BUDGET_EXCEEDED, format_fail_startup

    print(format_fail_startup(REASON_STARTUP_BUDGET_EXCEEDED), file=sys.stderr, flush=True)
    os._exit(1)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.command == "codex-sessions":
        if args.codex_sessions_command == "retag":
            _run_codex_sessions_retag(args)
            return
        raise SystemExit("codex-sessions requires a subcommand")

    if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

    assert_bind_host_allowed(
        args.host,
        allow_nonlocal_bind=resolve_allow_nonlocal_bind(os.environ),
    )

    port = _parse_server_port(args.port)
    timeout_keep_alive = _parse_server_timeout_keep_alive(args.timeout_keep_alive)
    ws_max_size = _parse_server_ws_max_size(args.ws_max_size)
    os.environ["PORT"] = str(port)

    stop = threading.Event()
    timeout_seconds = _listen_timeout_seconds()
    mark_listen_watch_started()
    watcher = threading.Thread(
        target=watch_listen_or_timeout,
        kwargs={
            "host": args.host if args.host not in {"0.0.0.0", "::"} else "127.0.0.1",
            "port": port,
            "timeout_seconds": timeout_seconds,
            "should_stop": stop.is_set,
            "on_timeout": _exit_listen_timeout,
            "on_live_timeout": _exit_live_timeout,
            "on_live_hang": _exit_live_hung,
            "keep_watching_after_live": True,
            "live_hang_seconds": live_hang_seconds(),
        },
        name="codex-lb-listen-watch",
        daemon=True,
    )
    watcher.start()
    try:
        _run_server(
            "app.main:app",
            host=args.host,
            port=port,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
            timeout_keep_alive=timeout_keep_alive,
            ws_max_size=ws_max_size,
            proxy_headers=False,
            log_config=_build_log_config(),
        )
    finally:
        stop.set()
        release_startup_budget()


def _load_uvicorn():
    import uvicorn

    return uvicorn


def _load_graceful_drain_server():
    from app.core.server import GracefulDrainServer

    return GracefulDrainServer


def _load_http_protocol_class() -> Any:
    from app.core.http_protocol import load_http_protocol_class

    return load_http_protocol_class()


def _load_shutdown_drain_timeout_seconds() -> int:
    from app.core.config.settings import get_settings

    return get_settings().shutdown_drain_timeout_seconds


def _run_server(app: str, **kwargs: Any) -> None:
    uvicorn = _load_uvicorn()
    drain_timeout_seconds = _load_shutdown_drain_timeout_seconds()
    config = uvicorn.Config(
        app,
        # One process per instance is the binding topology contract. Passing
        # this explicitly prevents Uvicorn from treating ambient
        # WEB_CONCURRENCY as an unsupported multiprocess launch.
        workers=1,
        # Serve valid HTTP/1.1 requests that opportunistically offer an h2c
        # upgrade (JetBrains/Ktor clients) instead of rejecting them; the
        # stock httptools protocol drops the body or answers 400. See
        # app/core/http_protocol.py and issue #1757.
        http=_load_http_protocol_class(),
        timeout_graceful_shutdown=drain_timeout_seconds,
        **kwargs,
    )
    try:
        config.load_app()
        server = _load_graceful_drain_server()(
            config,
            drain_timeout_seconds=drain_timeout_seconds,
        )
        server.run()
    except KeyboardInterrupt:
        return
    if not server.started:
        from uvicorn.main import STARTUP_FAILURE

        raise SystemExit(STARTUP_FAILURE)


def _build_log_config() -> "LogConfig":
    from app.core.runtime_logging import build_log_config

    return build_log_config()


def _parse_server_port(raw_port: str) -> int:
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"--port/PORT must be an integer between 0 and 65535 inclusive, got {raw_port!r}.") from exc
    if not 0 <= port <= 65535:
        raise SystemExit(f"--port/PORT must be between 0 and 65535 inclusive, got {raw_port!r}.")
    return port


def _parse_server_timeout_keep_alive(raw_timeout: str) -> int:
    try:
        return int(raw_timeout)
    except ValueError as exc:
        message = f"--timeout-keep-alive/UVICORN_TIMEOUT_KEEP_ALIVE must be an integer, got {raw_timeout!r}."
        raise SystemExit(message) from exc


def _parse_server_ws_max_size(raw_ws_max_size: str) -> int:
    try:
        ws_max_size = int(raw_ws_max_size)
    except ValueError as exc:
        message = f"--ws-max-size/UVICORN_WS_MAX_SIZE must be an integer, got {raw_ws_max_size!r}."
        raise SystemExit(message) from exc
    if ws_max_size <= 0:
        raise SystemExit(f"--ws-max-size/UVICORN_WS_MAX_SIZE must be positive, got {raw_ws_max_size!r}.")
    return ws_max_size


def _run_codex_sessions_retag(args: argparse.Namespace) -> None:
    codex_home = args.codex_home or default_codex_home()
    if not args.dry_run:
        _confirm_retag_write(args.yes)

    try:
        result = retag_codex_sessions(
            codex_home=codex_home,
            source_provider=args.source_provider,
            target_provider=args.target_provider,
            dry_run=args.dry_run,
            progress_logger=lambda message: print(message, flush=True),
        )
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "locked" in message.casefold():
            message = (
                f"{message}\n"
                "Close Codex/Codex CLI and retry. The state_*.sqlite database can be locked while Codex is running."
            )
        raise SystemExit(message) from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    except OSError as exc:
        raise SystemExit(f"Unable to read or write Codex session files: {exc}") from exc

    _print_retag_summary(result)


def _confirm_retag_write(yes: bool) -> None:
    warning = (
        "This command rewrites Codex session metadata, including state_*.sqlite when present.\n"
        "Close Codex/Codex CLI before continuing to avoid SQLite locks or stale writes."
    )
    print(warning, file=sys.stderr)
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to write without --yes in a non-interactive shell.")
    answer = input("Continue? [y/N] ").strip().casefold()
    if answer not in {"y", "yes"}:
        raise SystemExit("Aborted.")


def _print_retag_summary(result: RetagResult) -> None:
    action = "Would update" if result.dry_run else "Updated"
    methods = ", ".join(result.methods_used) if result.methods_used else "none"
    print("")
    print("Codex session retag summary")
    print(f"- Codex home: {result.codex_home}")
    print(f"- Methods used: {methods}")
    print(f"- JSONL files scanned: {result.jsonl_files_scanned}")
    print(f"- JSONL files matched: {result.jsonl_files_matched}")
    print(f"- SQLite DBs scanned: {result.sqlite_dbs_scanned}")
    print(f"- SQLite DBs matched: {result.sqlite_dbs_matched}")
    print(f"- {action} JSONL files: {result.jsonl_files_matched if result.dry_run else result.jsonl_files_updated}")
    print(f"- {action} SQLite rows: {result.sqlite_rows_matched if result.dry_run else result.sqlite_rows_updated}")
    if result.backup_path is not None:
        print(f"- Backup: {result.backup_path}")


if __name__ == "__main__":
    main()
