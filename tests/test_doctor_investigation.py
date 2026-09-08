from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest

from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry, PluginRuntime
from cyrene.platform.doctor.agent_analysis import analyze
from cyrene.platform.doctor.investigation import Investigation
from cyrene.plugins.builtin.cyrene_plugin_development.inspection import INSPECTION_PLUGIN, inspect_plugins
from cyrene.plugins.maintenance import source_health, source_pack_ids


@pytest.fixture
def host(tmp_path):
    root = tmp_path / 'plugins'
    root.mkdir()
    (root / 'folder').mkdir()
    (root / 'folder' / '__init__.py').write_text('def broken(:\n')
    registry = PluginRegistry(include_core=False)
    async def tool(args, context):
        return 'ok'
    registry.register_pack(PluginPack('different.pack.id', 'Example', (Plugin('Example', 'Example', {'type': 'object'}, tool),)), source=str(root / 'folder' / '__init__.py'))
    registry.register_pack(PluginPack('inspection', 'Inspect', (INSPECTION_PLUGIN,)), source='inspection')
    return SimpleNamespace(plugin_directory=root, registry=registry, runtime=PluginRuntime(registry), load_failures=[], startup_failures={}, setup_failures={}, restart_required_packs=(), pack_running=lambda _: False, pack_operational=lambda _: False)


@pytest.mark.asyncio
async def test_plugin_investigation_catalog_search_read_and_source_selection(host, tmp_path):
    report = {'id': 'doctor_one', 'language': 'zh', 'scope': {'chat_id': 'chat_one'}, 'plugin_targets': ['folder'],
              'findings': [{'id': 'e1', 'code': 'agent_transition_failed', 'status': 'failed'}]}
    calls = 0
    class Gateway:
        async def complete(self, messages, **options):
            nonlocal calls
            calls += 1
            assert 'PluginRepairInspect' in [t['function']['name'] for t in options['tools']]
            if calls < 4:
                args = [{'action': 'catalog'}, {'action': 'search', 'path': 'folder', 'query': 'broken'},
                        {'action': 'read', 'path': 'folder/__init__.py'}][calls - 1]
                name = 'PluginRepairInspect'
            else:
                name = 'submit_diagnosis'
                args = {'summary': 'Malformed implementation', 'user_summary': '已找到相关功能的问题。', 'user_next_steps': [],
                        'next_steps': [], 'evidence_ids': ['e1', 'i3'], 'repair_target': 'folder'}
            return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': f'call{calls}', 'name': name, 'arguments': args}]}
    result = await asyncio.wait_for(analyze(report, Gateway(), tmp_path / 'analysis', host=host), 15)
    assert result['repair_target'] == 'folder'
    assert result['investigation_findings'][-1]['code'] == 'repair_source_inspected'
    assert 'source_text' not in str(result)
    assert (host.plugin_directory / 'folder/__init__.py').read_text() == 'def broken(:\n'


@pytest.mark.asyncio
async def test_inspector_rejects_escape_and_symlink(host, tmp_path):
    context = PluginContext(services={'plugin_maintenance_host': host})
    private = tmp_path / 'private.py'
    private.write_text('private = True')
    (host.plugin_directory / 'link.py').symlink_to(private)
    for path in ('../private.py', 'link.py', str(private)):
        with pytest.raises(ValueError):
            await inspect_plugins({'action': 'read', 'path': path}, context)
    assert source_pack_ids(host, 'folder') == ['different.pack.id']
    host.restart_required_packs = ('different.pack.id',)
    assert source_health(host, 'folder')['restart_required'] is True
    host.setup_failures = {'different.pack.id': 'failed'}
    assert source_health(host, 'folder')['errors'] == ['setup_failed']


def test_inspection_contributions_respect_activation(host):
    host.registry.set_pack_enabled('inspection', False)
    investigation = Investigation(host, {'scope': {}}, {}, None)
    assert investigation.plugins() == ()


@pytest.mark.asyncio
async def test_frozen_mac_uses_environment_python_with_isolation(monkeypatch):
    import sys
    from cyrene.platform.doctor import repair_executor
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').exists():
        pytest.skip('macOS isolated executor')
    executable = str(Path(sys.executable).resolve())
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', '/Applications/Cyrene.app/Contents/MacOS/Cyrene')
    monkeypatch.setattr('cyrene.plugins.application.application_plugin_service', lambda _: SimpleNamespace(
        process_environment=lambda _: {'PATH': str(Path(executable).parent)}))
    assert repair_executor.capabilities()['mode'] == 'isolated_python'
    result = await repair_executor.run_probe({'example.py': b'value = 42\n'}, 'import example; assert example.value == 42')
    assert result['status'] == 'passed', result
