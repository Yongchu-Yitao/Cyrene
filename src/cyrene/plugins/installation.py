"""Install validated source and report its actual registry/lifecycle state."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from cyrene.plugins.management import pack_status, plugin_status
from cyrene.plugins.validation import validate_plugin_source


async def install_source(host: Any, source: Path) -> dict[str, Any]:
    source = source.resolve()
    validation = validate_plugin_source(source)
    if not validation.get("ok"):
        return validation
    if host is None:
        return {"ok": False, "error": "The Plugin application host is unavailable."}
    source_type = str(validation.get("source_type") or "")
    identity = str(validation.get("pack_id") or validation.get("plugin_name") or source.stem)
    target = host.plugin_directory / (identity if source_type == "pack" else source.name)
    if target.exists():
        return {"ok": False, "error": "Plugin source already exists; edit or delete it instead.",
                "path": str(target)}
    staging_root = Path(tempfile.mkdtemp(prefix=f".{identity}.install-", dir=host.plugin_directory))
    staged = staging_root / target.name
    try:
        if source_type == "pack":
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        else:
            shutil.copy2(source, staged)
        if target.exists():
            raise FileExistsError(f"Plugin source already exists: {target}")
        staged.rename(target)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    seed, failures = await host.reload_user_plugins()
    target_failures = [item for item in failures if Path(item.path).resolve() == target.resolve()]
    other_failures = [item for item in failures if item not in target_failures]
    if source_type == "pack":
        pack = next((pack for pack in host.registry.list_packs()
                     if pack.id == identity and Path(host.registry.pack_source(pack.id)).resolve() == target.resolve()), None)
        loaded = pack is not None
        status = pack_status(host.registry, pack, host) if loaded else {}
    else:
        registered = next((item for item in host.registry.list_plugins()
                           if item.plugin.name == identity and Path(item.source).resolve() == target.resolve()), None)
        loaded = registered is not None
        status = plugin_status(host.registry, registered, host) if loaded else {}
    return {
        "ok": loaded and not target_failures,
        "loaded": loaded,
        "enabled": False,
        "restart_required": False,
        **status,
        "source_type": source_type,
        "identity": identity,
        "path": str(target),
        "failures": [{"path": str(item.path), "error": item.error} for item in target_failures],
        "other_failures": [{"path": str(item.path), "error": item.error} for item in other_failures],
        "recovery": ({"source_path": target.name,
                      "delete": {"action": "delete", "kind": "source", "id": target.name}}
                     if target_failures else None),
        "seeded": {"created": [str(path) for path in seed.created], "updated": [str(path) for path in seed.updated]},
    }
