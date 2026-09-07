from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping

import uvicorn

from app.core.bind_policy import assert_bind_host_allowed, resolve_allow_nonlocal_bind, resolve_bind_host
from app.core.runtime_logging import build_log_config

DEFAULT_LISTEN_TIMEOUT_SECONDS = 30.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the codex-lb API server.")
    parser.add_argument("--host", default=resolve_bind_host(os.environ))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))

    return parser.parse_args()


def port_is_listening(host: str, port: int, timeout: float = 0.3) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def watch_listen_or_timeout(
    *,
    host: str,
    port: int,
    timeout_seconds: float,
    is_listening: Callable[[str, int], bool] = port_is_listening,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool],
    on_timeout: Callable[[], None],
) -> str:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if should_stop():
            return "stopped"
        if is_listening(host, port):
            return "listening"
        sleep(min(0.2, max(0.05, timeout_seconds / 10)))
    if should_stop():
        return "stopped"
    on_timeout()
    return "timeout"


def _listen_timeout_seconds(environ: Mapping[str, str] = os.environ) -> float:
    raw = (environ.get("CODEX_LB_LISTEN_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_LISTEN_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LISTEN_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_LISTEN_TIMEOUT_SECONDS


def _exit_listen_timeout() -> None:
    print("category=fail_startup reason=listen_timeout", file=sys.stderr)
    os._exit(1)


def main() -> None:
    args = _parse_args()

    if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

    assert_bind_host_allowed(
        args.host,
        allow_nonlocal_bind=resolve_allow_nonlocal_bind(os.environ),
    )

    os.environ["PORT"] = str(args.port)

    stop = threading.Event()
    timeout_seconds = _listen_timeout_seconds()
    watcher = threading.Thread(
        target=watch_listen_or_timeout,
        kwargs={
            "host": args.host if args.host not in {"0.0.0.0", "::"} else "127.0.0.1",
            "port": args.port,
            "timeout_seconds": timeout_seconds,
            "should_stop": stop.is_set,
            "on_timeout": _exit_listen_timeout,
        },
        name="codex-lb-listen-watch",
        daemon=True,
    )
    watcher.start()
    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
            log_config=build_log_config(),
        )
    finally:
        stop.set()


if __name__ == "__main__":
    main()
