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

        print(json.dumps({
            "ok": True,
            "marker": "CYRENE_SIMPLEXNG_SIDECAR_SMOKE=ok",
            "architecture": platform.machine(),
            "simplexng": getattr(simplexng, "__version__", "ok"),
        }))
        raise SystemExit(0)
    if "--cyrene-prepare-settings" in sys.argv:
        raise SystemExit(_prepare_settings())
    from cyrene.tooling.backends.simplexng_child import main

    main()
