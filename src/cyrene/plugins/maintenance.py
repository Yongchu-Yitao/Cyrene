"""Source identity and lifecycle checks shared by plugin maintenance clients."""
from pathlib import Path

from cyrene.plugins.management import pack_status


def source_pack_ids(host, target):
    root = Path(host.plugin_directory).resolve()
    source = (root / target).resolve()
    if source == root or not source.is_relative_to(root):
        raise ValueError('Invalid plugin source')
    registry = host.registry
    result = []
    for pack in registry.list_packs():
        location = Path(registry.pack_source(pack.id)).resolve()
        if location == source or (source.is_dir() and location.is_relative_to(source)):
            result.append(pack.id)
    return sorted(result)


def source_health(host, target):
    ids = source_pack_ids(host, target)
    errors = []
    source = (Path(host.plugin_directory) / target).resolve()
    for failure in host.load_failures:
        location = Path(failure.path).resolve()
        if location == source or location.is_relative_to(source):
            errors.append('load_failed')
    states = [pack_status(host.registry, pack, host)
              for pack in host.registry.list_packs() if pack.id in ids]
    restart = any(state["restart_required"] for state in states)
    for state in states:
        if state["setup_error"]:
            errors.append('setup_failed')
        if state["startup_error"]:
            errors.append('startup_failed')
        if (not restart and getattr(host, 'started', False)
                and state["enabled"] and state["application_running"] is False):
            errors.append('not_running')
    return {'pack_ids': ids, 'errors': sorted(set(errors)), 'restart_required': restart}
