import asyncio
import json
import shutil
from pathlib import Path

import pytest

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.core.plugin.result_codec import question_options
from cyrene.plugins import native_tools


SOURCE = Path(__file__).parents[1] / 'src/cyrene/plugins/builtin/ask_user.py'


def direct_names(registry, agent_id='main'):
    return {tool['function']['name'] for tool in registry.direct_tool_definitions(agent_id=agent_id)}


@pytest.mark.parametrize(('options', 'expected_options'), [
    (['Short', 'Long'], [
        {'id': 'option_1', 'label': 'Short'},
        {'id': 'option_2', 'label': 'Long'},
    ]),
    ([
        {'key': 'A', 'label': '继续完善 WorldForge', 'description': '修边角、扩功能'},
        {'key': 'B', 'label': '用 SDK 起新插件', 'description': '做第二个独立插件包'},
        {'key': 'C', 'label': '实现统一安装器', 'description': '五类插件统一管理'},
    ], [
        {'id': 'option_1', 'label': '继续完善 WorldForge'},
        {'id': 'option_2', 'label': '用 SDK 起新插件'},
        {'id': 'option_3', 'label': '实现统一安装器'},
    ]),
    (['Short', {'id': 'long', 'label': 'Long'}], [
        {'id': 'option_1', 'label': 'Short'},
        {'id': 'long', 'label': 'Long'},
    ]),
])
def test_ask_user_loads_and_runs_without_control_pack(tmp_path, options, expected_options):
    shutil.copy2(SOURCE, tmp_path / 'ask_user.py')
    registry = PluginRegistry()
    assert registry.load_directory(tmp_path) == ()
    assert registry.registered('ask_user').pack_id is None
    assert not registry.plugin_locked('ask_user')
    assert 'ask_user' in direct_names(registry)
    assert 'ask_user' not in direct_names(registry, 'child')
    registry.set_plugin_enabled('ask_user', False)
    assert 'ask_user' not in direct_names(registry)
    registry.set_plugin_enabled('ask_user', True)
    result = asyncio.run(PluginRuntime(registry).call(
        'ask_user', {'text': 'Which format?', 'options': options},
        PluginContext(data={'run_context': {'agent_id': 'main', 'round_id': 'round-test',
                                          'permission_mode': 'full_access'}}),
    ))
    assert result.success, result.error
    value = json.loads(result.value)
    assert value['status'] == 'awaiting_user'
    assert value['text'] == 'Which format?'
    assert value['options'] == options
    assert value['option_count'] == len(options)
    assert question_options(value['options']) == expected_options
    assert value['round_id'] == 'round-test'


def test_seed_upgrade_moves_ask_user_out_of_control_pack(monkeypatch, tmp_path):
    canonical = {name: data for name, data in native_tools._collect_canonical_files().items()
                 if name.startswith('cyrene_control/') or name == 'ask_user.py'}
    legacy = dict(canonical)
    legacy['cyrene_control/ask_user.py'] = legacy.pop('ask_user.py')
    legacy['cyrene_control/__init__.py'] = legacy['cyrene_control/__init__.py'].replace(
        b'from . import deep_reflect,', b'from . import ask_user, deep_reflect,',
    ).replace(b'        enter_plan_mode,', b'        ask_user,\n        enter_plan_mode,')
    monkeypatch.setattr(native_tools, '_collect_canonical_files', lambda: legacy)
    native_tools.seed_builtin_plugin_directory(tmp_path)
    old_registry = PluginRegistry()
    assert old_registry.load_directory(tmp_path) == ()
    assert old_registry.registered('ask_user').pack_id == 'cyrene_control'

    monkeypatch.setattr(native_tools, '_collect_canonical_files', lambda: canonical)
    native_tools.seed_builtin_plugin_directory(tmp_path)
    assert not (tmp_path / 'cyrene_control/ask_user.py').exists()
    registry = PluginRegistry()
    assert registry.load_directory(tmp_path) == ()
    assert registry.registered('ask_user').pack_id is None
    assert sum(item.plugin.name == 'ask_user' for item in registry.list_plugins()) == 1
    registry.set_pack_enabled('cyrene_control', False)
    assert 'ask_user' in direct_names(registry)
    assert 'DeepReflect' not in direct_names(registry)
    registry.set_plugin_enabled('ask_user', False)
    registry.set_pack_enabled('cyrene_control', True)
    assert 'ask_user' not in direct_names(registry)
