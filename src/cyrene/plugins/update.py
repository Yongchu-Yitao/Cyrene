"""Replace a user-installed plugin as a unit, retaining its code until verified."""
from __future__ import annotations

from contextlib import ExitStack
from importlib.util import cache_from_source
import logging
import shutil
from pathlib import Path

from cyrene.platform.doctor.repair_workspace import repair_lock
from cyrene.plugins.installation import reload_installed_source, stage_source
from cyrene.plugins.maintenance import source_health
from cyrene.plugins.management import pack_status, plugin_status, update_activation
from cyrene.plugins.native_tools import is_bundled_plugin_entry
from cyrene.plugins.package_source import package_source
from cyrene.plugins.validation import validate_plugin_source

logger = logging.getLogger(__name__)
_UPDATE_DIRECTORY = ".plugin-updates"


def _update_lock(directory):
    return repair_lock(directory, busy_message="Another plugin update is in progress.")


def _discard(transaction):
    # Move out of the recovery namespace before deleting any backup bytes.
    garbage = transaction.with_name(".done-" + transaction.name)
    transaction.replace(garbage)
    shutil.rmtree(garbage)


def _replace_source(source, target):
    if target.suffix == ".py" and source.is_file():
        # Standalone files share the parent cache across versions of equal size/mtime.
        Path(cache_from_source(str(target))).unlink(missing_ok=True)
    source.replace(target)


def _restore(target, transaction):
    previous = transaction / "previous"
    if previous.exists():
        if target.exists():
            target.replace(transaction / "rejected")
        _replace_source(previous, target)
    _discard(transaction)


def prepare_plugin_updates(root: Path, boot: ExitStack) -> tuple[str, ...]:
    """Recover interrupted switches before discovery. Try deferred applications once."""
    directory = root / _UPDATE_DIRECTORY
    if not directory.exists():
        return ()
    verifying = []
    with _update_lock(directory):
        if not any(item.is_dir() and (not item.name.startswith(".") or item.name.startswith(".done-")) for item in directory.iterdir()):
            return ()
        # Other CLI or desktop hosts can compose against the same installation.
        # Only the host owning this OS lock may consume pending boot checks.
        try:
            boot.enter_context(repair_lock(directory / ".boot"))
        except ValueError:
            return ()
        for transaction in sorted(directory.iterdir()):
            if transaction.is_symlink():
                raise ValueError(f"Unexpected symbolic link in plugin update state: {transaction.name}")
            if transaction.name.startswith(".done-"):
                shutil.rmtree(transaction)
                continue
            if transaction.name.startswith(".") or not transaction.is_dir():
                continue
            target = root / transaction.name
            pending = transaction / "await-restart"
            if pending.exists() and target.exists():
                pending.replace(transaction / "previous")
                verifying.append(target.name)
            elif pending.exists():
                # An explicit uninstall after the update takes precedence.
                _discard(transaction)
            else:
                _restore(target, transaction)
    if not verifying:
        boot.close()
    return tuple(verifying)


async def verify_restarted_updates(host, names):
    if not names:
        return
    directory = host.plugin_directory / _UPDATE_DIRECTORY
    with _update_lock(directory):
        restored = False
        for name in names:
            transaction = directory / name
            errors = source_health(host, name)["errors"]
            if errors:
                _restore(host.plugin_directory / name, transaction)
                logger.error("Plugin %s failed after restart (%s). Restored previous source.", name, errors)
                restored = True
            else:
                _discard(transaction)
        if restored:
            await host.reload_user_plugins()


def _installed_target(host, identity, kind):
    if kind == "pack":
        source = next((host.registry.pack_source(pack.id) for pack in host.registry.list_packs()
                       if pack.id == identity), None)
    else:
        source = next((entry.source for entry in host.registry.list_plugins()
                       if entry.pack_id is None and entry.plugin.name == identity), None)
    if source is None:
        raise ValueError(f"No installed {kind} matches {identity}.")
    target = Path(source)
    if (target.parent != host.plugin_directory or target.is_symlink()
            or not target.exists() or target.name.startswith((".", "_"))
            or is_bundled_plugin_entry(target.name)):
        raise ValueError("PluginUpdate requires a user-installed plugin source; bundled source edits require review.")
    return target


async def update_source(host, path: Path, identity: str) -> dict:
    if host is None:
        return {"ok": False, "error": "The Plugin application host is unavailable."}
    directory = host.plugin_directory / _UPDATE_DIRECTORY
    with package_source(path) as source, _update_lock(directory):
        validation = validate_plugin_source(source)
        if not validation["ok"]:
            return validation
        if identity != (validation.get("pack_id") or validation.get("plugin_name")):
            raise ValueError("The new source does not match the requested plugin identity.")
        kind = validation["source_type"]
        target = _installed_target(host, identity, kind)
        transaction = directory / target.name
        if transaction.exists():
            raise ValueError("A previous update requires a restart before this plugin can be updated again.")
        if directory.is_relative_to(source):
            raise ValueError("The update source cannot contain the plugin installation directory.")
        registry = host.registry
        configured = (registry.pack_configured_enabled(identity) if kind == "pack"
                      else registry.plugin_configured_enabled(identity))
        transaction.mkdir()
        previous = transaction / "previous"
        staged = transaction / "new"
        try:
            stage_source(source, staged)
            target.replace(previous)
            _replace_source(staged, target)
            result = await reload_installed_source(host, target, identity, kind)
            result.pop("recovery", None)  # Failed fresh installs can be deleted; updates restore in place.
            if result["ok"] and result["configured_enabled"] != configured:
                # A new default must not silently flip the currently installed switch.
                await update_activation(registry, host, actor="agent",
                                        packs={identity: configured} if kind == "pack" else {},
                                        plugins={identity: configured} if kind != "pack" else {})
                if kind == "pack":
                    pack = next(pack for pack in registry.list_packs() if pack.id == identity)
                    result.update(pack_status(registry, pack, host))
                else:
                    entry = next(item for item in registry.list_plugins() if item.plugin.name == identity)
                    result.update(plugin_status(registry, entry, host))
            health = source_health(host, target.name)
            if result.get("configured_enabled") != configured and result["loaded"]:
                health["errors"].append("activation_changed")
            if result["ok"] and not health["errors"]:
                if result["restart_required"]:
                    previous.replace(transaction / "await-restart")
                else:
                    _discard(transaction)
                return result
        except BaseException:
            if transaction.exists():
                switched = previous.exists()
                _restore(target, transaction)
                if switched:
                    await host.reload_user_plugins()
            raise

        _restore(target, transaction)
        await host.reload_user_plugins()
        return {"ok": False, "identity": identity, "rolled_back": True,
                "error": "Update failed. Restored the previous plugin source.",
                "failures": result["failures"], "lifecycle_errors": health["errors"],
                "restart_required": source_health(host, target.name)["restart_required"]}
