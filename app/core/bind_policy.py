from __future__ import annotations

from collections.abc import Mapping

LOOPBACK_BIND_HOSTS = {"127.0.0.1", "::1", "[::1]", "localhost"}

_TRUTHY = {"1", "true", "yes", "on"}


def resolve_bind_host(environ: Mapping[str, str]) -> str:
    host = (environ.get("CODEX_LB_BIND_HOST") or environ.get("HOST") or "127.0.0.1").strip()
    return host or "127.0.0.1"


def resolve_allow_nonlocal_bind(environ: Mapping[str, str]) -> bool:
    raw = (environ.get("CODEX_LB_ALLOW_NONLOCAL_BIND") or "").strip().lower()
    return raw in _TRUTHY


def is_loopback_bind_host(host: str) -> bool:
    return host.strip().lower() in LOOPBACK_BIND_HOSTS


def assert_bind_host_allowed(host: str, *, allow_nonlocal_bind: bool) -> None:
    if allow_nonlocal_bind or is_loopback_bind_host(host):
        return
    raise SystemExit(
        "Refusing non-local bind host. Set CODEX_LB_ALLOW_NONLOCAL_BIND=true "
        "to allow binding codex-lb to a non-loopback interface."
    )
