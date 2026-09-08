"""Read-only investigation contribution for Doctor and other maintenance agents."""
from pathlib import Path
import hashlib

from cyrene.core.plugin import Plugin, application_plugin_scope
from cyrene.plugins.maintenance import source_health


async def inspect_plugins(arguments, context):
    host = context.services.get('plugin_maintenance_host') or application_plugin_scope()
    if host is None:
        raise ValueError('Plugin host unavailable')
    root = Path(host.plugin_directory).resolve()
    action = arguments['action']
    if action == 'catalog':
        items = []
        for entry in sorted(root.iterdir()):
            if entry.is_symlink() or entry.name.startswith('.') or not (entry.is_dir() or entry.suffix == '.py') or entry.name == '__pycache__':
                continue
            state = source_health(host, entry.name)
            names = [r.plugin.name for r in host.registry.list_plugins() if r.pack_id in state['pack_ids']]
            items.append({'target': entry.name, 'tools': names[:50], **state})
            if len(items) == 200:
                break
        return {'plugins': items}
    relative = Path(arguments.get('path', ''))
    if relative.is_absolute() or '..' in relative.parts or any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
        raise ValueError('Choose a regular source inside the plugin directory')
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root) or any((root / Path(*path.relative_to(root).parts[:i])).is_symlink() for i in range(1, len(path.relative_to(root).parts) + 1)):
        raise ValueError('Choose a source inside the plugin directory')
    if action == 'read':
        if not path.is_file() or path.suffix not in {'.py', '.json', '.toml', '.md', '.txt', '.js', '.jsx', '.html', '.css'} or path.stat().st_size > 64000:
            raise ValueError('Choose a text file of at most 64000 bytes')
        raw = path.read_bytes()
        return {'path': path.relative_to(root).as_posix(), 'target': path.relative_to(root).parts[0],
                'sha256': hashlib.sha256(raw).hexdigest(), 'source_text': raw.decode('utf-8')}
    # A literal search, bounded by bytes and files, never regex or arbitrary I/O.
    query = arguments.get('query', '')
    if not query or len(query) > 120:
        raise ValueError('Provide a short literal search')
    files = path.rglob('*') if path.is_dir() else [path]
    hits, scanned, total = [], 0, 0
    for file in files:
        if any(part.is_symlink() for part in [file, *list(file.parents)[:len(file.relative_to(root).parts) - 1]]) or not file.is_file() or file.suffix not in {'.py', '.json', '.toml', '.md'} or '__pycache__' in file.parts:
            continue
        if file.stat().st_size > 64000:
            continue
        raw = file.read_bytes()
        total += len(raw)
        scanned += 1
        if total > 1_000_000 or scanned > 100:
            break
        for number, line in enumerate(raw.decode('utf-8', errors='replace').splitlines(), 1):
            if query.lower() in line.lower():
                hits.append({'path': file.relative_to(root).as_posix(), 'line': number, 'text': line[:300]})
                if len(hits) >= 30:
                    return {'matches': hits, 'truncated': True}
    return {'matches': hits, 'files_scanned': scanned, 'bounded': True}


INSPECTION_PLUGIN = Plugin(
    name='PluginRepairInspect', description='Investigate installed built-in and custom plugins before choosing a repair. Catalog lists source targets and lifecycle state; search finds literal references inside one target; read inspects a source file.',
    input_schema={'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['catalog', 'search', 'read']},
        'path': {'type': 'string', 'maxLength': 240}, 'query': {'type': 'string', 'maxLength': 120}},
        'required': ['action'], 'additionalProperties': False}, handler=inspect_plugins,
    metadata={'read_only': True, 'doctor_inspection': True, 'i18n': {'zh': {'name': '调查插件问题', 'description': '查看相关功能、搜索实现并读取源码，为修复提供依据。'}}}, timeout_seconds=15,
)
