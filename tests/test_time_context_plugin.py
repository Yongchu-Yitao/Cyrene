"""Time snapshots exercise real session hooks, persistence and projection."""
import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_user_language_plugin import _session
from cyrene.core.context.projection import selected_context_node_ids, project_context_message
from cyrene.plugins.builtin.cyrene_plugin_development.tools import validate_plugin_source


def test_time_lifecycle(tmp_path, monkeypatch):
    source = Path(__file__).parents[1] / 'src/cyrene/plugins/builtin/cyrene_time_context'
    assert validate_plugin_source(source)['ok']
    shutil.copytree(source, tmp_path / 'plugin_impl' / source.name)
    settings = {'timezone': 'Asia/Shanghai'}
    monkeypatch.setattr('cyrene.platform.settings_store.get', settings.get)
    now = datetime(2026, 11, 24, 15, 59, tzinfo=timezone.utc)
    s = _session(tmp_path)
    leaf = s.tree.root_id

    def tick(at):
        nonlocal leaf
        mounts = asyncio.run(s.hooks.model_start_mounts({'node_id': leaf}, time=at))
        for mount in mounts:
            node = s.store.mount(s.tree.id, leaf, {
                'role': 'context', 'content': mount['context'],
                'context_kind': mount['context_kind'],
                'context_source': mount['context_source'],
                'context_lifecycle': mount['context_lifecycle'],
                'run_id': 'test', 'trigger_model': False,
            })
            leaf = node.id
        return mounts

    try:
        assert s.registry.pack_enabled('cyrene_time_context')
        first = tick(now)
        assert '首次注入' in first[0]['context']
        assert '2026-11-24T23:59:00+08:00' in first[0]['context']
        assert tick(now) == ()  # Mount re-entry must terminate.
        assert tick(now + timedelta(seconds=30)) == ()
        assert '1 分 0 秒' in tick(now + timedelta(minutes=1))[0]['context']  # Midnight.
        assert tick(now + timedelta(minutes=5)) == ()
        assert '7 分 12 秒' in tick(now + timedelta(minutes=8, seconds=12))[0]['context']
    finally:
        s.close()
    s = _session(tmp_path)
    try:
        assert tick(now + timedelta(minutes=9)) == ()  # Durable path after rebind.
        settings['timezone'] = 'America/New_York'
        changed = tick(now + timedelta(minutes=9))
        assert '-05:00' in changed[0]['context']
        assert '0 分 48 秒' in changed[0]['context']
        assert tick(now + timedelta(minutes=9)) == ()
        assert '时钟回拨' in tick(now)[0]['context']
        path = s.store.get_path(s.tree.id, leaf)
        selected = selected_context_node_ids(path, 'test')
        snapshots = [n for n in path if n.id in selected and n.value.get('context_kind') == 'current_time']
        assert len(snapshots) == 1
        messages = []
        project_context_message(messages, snapshots[0].value)
        assert messages == [{'role': 'user', 'content': snapshots[0].value['content']}]
        assert len([h for h in s.hooks.list() if h.plugin_id == 'cyrene_time_context.mount']) == 1
    finally:
        s.close()
