import json

import pytest

from cyrene.core.plugin import PluginContext
from cyrene.plugins.builtin.cyrene_browser import browser_open_file, plugin_pack, runtime


@pytest.mark.asyncio
async def test_open_file_dispatches_canonical_workspace_path(tmp_path, monkeypatch):
    page = tmp_path / 'demo.html'
    page.write_text('<h1>Demo</h1>')
    calls = []

    async def fake_open(path, workspace):
        calls.append((path, workspace))
        return {'ok': True, 'title': 'Demo'}

    monkeypatch.setattr(runtime, 'open_local_file', fake_open)
    result = json.loads(await browser_open_file.handler({'path': 'demo.html'}, PluginContext(workspace=tmp_path)))
    assert result['title'] == 'Demo'
    assert calls == [(str(page.resolve()), str(tmp_path.resolve()))]
    assert 'browser_open_file' in {tool.name for tool in plugin_pack.plugins}


@pytest.mark.asyncio
async def test_open_file_rejects_invalid_and_escaping_paths(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside.html'
    outside.write_text('outside')
    (workspace / 'link.html').symlink_to(outside)
    (workspace / 'source.py').write_text('code')
    (workspace / '.hidden.html').write_text('hidden')

    async def forbidden(*args):
        pytest.fail('Invalid file must not reach browser RPC')

    monkeypatch.setattr(runtime, 'open_local_file', forbidden)
    for path in ['', '../outside.html', 'link.html', 'source.py', 'missing.html', '.hidden.html']:
        result = json.loads(await browser_open_file.handler({'path': path}, PluginContext(workspace=workspace)))
        assert result['ok'] is False


@pytest.mark.asyncio
async def test_open_file_rpc_and_frame(monkeypatch):
    calls = []
    frames = []
    monkeypatch.setattr(runtime, 'electron_browser_available', lambda: True)

    async def rpc(method, args):
        calls.append((method, args))
        return {'ok': True, 'title': 'Demo', 'url': 'http://127.0.0.1:123/token/demo.html'}

    async def emit(action, result):
        frames.append((action, result))

    monkeypatch.setattr(runtime, '_electron_browser_rpc', rpc)
    monkeypatch.setattr(runtime, '_emit_electron_frame', emit)
    result = await runtime.open_local_file('/workspace/demo.html', '/workspace')
    assert result['ok'] is True
    assert calls == [('openLocalFile', {'path': '/workspace/demo.html', 'workspace': '/workspace'})]
    assert frames == [('navigate', result)]
    monkeypatch.setattr(runtime, 'electron_browser_available', lambda: False)
    assert (await runtime.open_local_file('/workspace/demo.html', '/workspace'))['ok'] is False
