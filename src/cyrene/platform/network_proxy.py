"""Explicit HTTP proxy policy shared by Cyrene network boundaries.

The persisted ``external_agent_proxy_enabled`` key predates the broader
network scopes.  It remains the compatibility-safe master switch while each
Cyrene-owned traffic class opts in independently.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

from cyrene.platform import config_store


MASTER_SETTING: Final = "external_agent_proxy_enabled"
ADDRESS_SETTING: Final = "external_agent_proxy_url"
PORT_SETTING: Final = "external_agent_proxy_port"
SCOPE_SETTINGS: Final[dict[str, str]] = {
    "search": "proxy_search_enabled",
    "browser": "proxy_browser_enabled",
    "extensions": "proxy_extensions_enabled",
}


def proxy_master_enabled() -> bool:
    return config_store.get_setting(MASTER_SETTING, False) is True


def configured_proxy_port() -> int | None:
    try:
        port = int(config_store.get_setting(PORT_SETTING, 7897))
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def normalize_proxy_url(value: object) -> str:
    """Normalize a credential-free HTTP proxy address.

    A missing scheme is interpreted as HTTP so ``proxy.example:8080`` is a
    convenient, valid input. Proxy paths, credentials, queries, and fragments
    are rejected because the same value is shared with subprocesses and
    Electron's fixed-server proxy configuration.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"http://{raw}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port == 0
    ):
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.rstrip('/')}"


def configured_proxy_url(*, opt_in: bool = True) -> str:
    """Return the configured HTTP proxy only when both gates are enabled."""
    if not opt_in or not proxy_master_enabled():
        return ""
    saved_address = config_store.get_setting(ADDRESS_SETTING, "")
    if str(saved_address or "").strip():
        return normalize_proxy_url(saved_address)
    port = configured_proxy_port()
    return f"http://127.0.0.1:{port}" if port is not None else ""


def scoped_proxy_url(scope: str) -> str:
    setting = SCOPE_SETTINGS.get(str(scope or "").strip().lower())
    if setting is None:
        raise ValueError(f"unknown proxy scope: {scope!r}")
    return configured_proxy_url(
        opt_in=config_store.get_setting(setting, False) is True
    )


def proxy_environment(*, opt_in: bool = True) -> dict[str, str]:
    """Return credential-free proxy variables for one opted-in subprocess."""
    proxy_url = configured_proxy_url(opt_in=opt_in)
    if not proxy_url:
        return {}
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }


__all__ = [
    "ADDRESS_SETTING",
    "MASTER_SETTING",
    "PORT_SETTING",
    "SCOPE_SETTINGS",
    "configured_proxy_port",
    "configured_proxy_url",
    "normalize_proxy_url",
    "proxy_environment",
    "proxy_master_enabled",
    "scoped_proxy_url",
]
