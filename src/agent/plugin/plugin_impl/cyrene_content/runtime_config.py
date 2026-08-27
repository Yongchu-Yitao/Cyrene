"""Content-pack runtime configuration.

The encrypted store remains the generic persistence mechanism, while the
ownership, defaults, and interpretation of search-specific keys live here.
Existing installations keep their saved values because ``get_env`` accepts
arbitrary persisted keys even after they are removed from the core defaults.
"""

from __future__ import annotations

import os

from cyrene.runtime import config_store


SEARCH_PROXY = config_store.get_env("SEARCH_PROXY", "")
SEARXNG_URL = config_store.get_env("SEARXNG_URL", "")
SEARXNG_AUTO_START = (
    os.environ.get("SEARXNG_AUTO_START")
    or config_store.get_env("SEARXNG_AUTO_START", "1")
).strip().lower() not in {"0", "false", "no"}
SEARXNG_PORT = int(config_store.get_env("SEARXNG_PORT", "8888"))
SEARXNG_HOST = config_store.get_env("SEARXNG_HOST", "127.0.0.1")


__all__ = [
    "SEARCH_PROXY",
    "SEARXNG_AUTO_START",
    "SEARXNG_HOST",
    "SEARXNG_PORT",
    "SEARXNG_URL",
]
