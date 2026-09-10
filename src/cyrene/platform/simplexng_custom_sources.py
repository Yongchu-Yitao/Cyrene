"""Encode user queries safely in custom SimpleXNG POST bodies."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from urllib.parse import quote_plus


def wrap_request(engine) -> None:
    original = engine.request

    def request(query, params):
        result = original(query, params)
        if engine.method == "POST" and engine.request_body:
            content_type = engine.headers.get("Content-Type", "")
            encoded = json.dumps(query, ensure_ascii=False)[1:-1] if content_type == "application/json" else quote_plus(query)
            result["data"] = engine.request_body.format(
                query=encoded,
                pageno=params["pageno"],
                lang=params["language"][:2] if params["language"] != "all" else "en",
            )
        return result

    engine.request = request


def install() -> None:
    settings_path = os.environ.get("SEARXNG_SETTINGS_PATH", "")
    if not settings_path or not Path(settings_path).is_file():
        return
    import yaml
    settings = yaml.safe_load(Path(settings_path).read_text(encoding="utf-8")) or {}
    if not any(str(row.get("name", "")).startswith("custom-") for row in settings.get("engines", [])):
        return
    # Import the launcher to expose its vendored searx package without starting it.
    importlib.import_module("simplexng.simplexng")
    engines = importlib.import_module("searx.engines")
    if getattr(engines.load_engine, "_cyrene_custom_sources", False):
        return
    original = engines.load_engine

    def load_engine(config):
        engine = original(config)
        if engine is not None and str(config.get("name", "")).startswith("custom-") and config.get("engine") in ("json_engine", "xpath"):
            wrap_request(engine)
        return engine

    load_engine._cyrene_custom_sources = True
    engines.load_engine = load_engine
