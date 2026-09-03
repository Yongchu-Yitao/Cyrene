"""Entrypoint for the x64-only SimpleXNG WoA sidecar."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path


def _prepare_settings() -> int:
    import yaml
    from simplexng.settings import get_bundled_template

    request = json.loads(sys.stdin.read())
    destination = Path(request["path"])
    settings = yaml.safe_load(Path(get_bundled_template()).read_text(encoding="utf-8"))
    settings["server"]["port"] = int(request["port"])
    settings["server"]["bind_address"] = str(request["host"])
    settings["server"]["secret_key"] = str(request["secret_key"])
    engines = settings.setdefault("engines", [])
    if not isinstance(engines, list):
        engines = []
        settings["engines"] = engines
    by_name = {
        str(item.get("name") or ""): item
        for item in engines
        if isinstance(item, dict)
    }
    for override in request.get("engine_overrides") or []:
        if not isinstance(override, dict) or not override.get("name"):
            continue
        name = str(override["name"])
        existing = by_name.get(name)
        if existing is None:
            existing = {}
            engines.append(existing)
            by_name[name] = existing
        existing.update(override)
    formats = settings.setdefault("search", {}).setdefault("formats", [])
    if "json" not in formats:
        formats.append("json")
    outgoing = settings.setdefault("outgoing", {})
    proxy_url = str(request.get("proxy_url") or "")
    if proxy_url:
        outgoing["proxies"] = {"all://": [proxy_url]}
        outgoing["extra_proxy_timeout"] = 10
        outgoing["request_timeout"] = max(float(outgoing.get("request_timeout") or 3.0), 15.0)
    else:
        outgoing.pop("proxies", None)
        outgoing.pop("extra_proxy_timeout", None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.dump(settings, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "path": str(destination)}))
    return 0


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        import brotli  # noqa: F401 - smoke test verifies the bundled dependency
        import fasttext  # noqa: F401 - smoke test verifies the bundled dependency
        import simplexng
        from cyrene.simplexng_child import main as _run_simplexng_child  # noqa: F401

        print(json.dumps({
            "ok": True,
            "marker": "CYRENE_SIMPLEXNG_SIDECAR_SMOKE=ok",
            "architecture": platform.machine(),
            "simplexng": getattr(simplexng, "__version__", "ok"),
        }))
        raise SystemExit(0)
    if "--cyrene-prepare-settings" in sys.argv:
        raise SystemExit(_prepare_settings())
    from cyrene.simplexng_child import main

    main()
