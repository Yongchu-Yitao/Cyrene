import json
import zipfile

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from cyrene.core.plugin import PluginContext, PluginRegistry
from cyrene.plugins.application import PluginApplicationHost
from cyrene.plugins.builtin.cyrene_plugin_development import tools
from cyrene.workbench.http.plugins import register_plugin_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / 'installed'
    root.mkdir()
    app = FastAPI()
    host = PluginApplicationHost(app=app, registry=PluginRegistry(), bot=None,
        db_path=str(tmp_path / 'state.db'), data_directory=tmp_path / 'data', plugin_directory=root)
    original = host.reload_user_plugins
    async def reload():
        return await original(seed=False)
    monkeypatch.setattr(host, 'reload_user_plugins', reload)
    monkeypatch.setattr(tools, 'application_plugin_scope', lambda: host)
    router = APIRouter()
    register_plugin_routes(router, host)
    app.include_router(router)
    return TestClient(app), host


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['folder', 'zip', 'flat_zip', 'python'])
async def test_install_selected_source_and_reject_overwrite(tmp_path, client, kind):
    web, host = client
    source = tmp_path / ('sample.py' if kind == 'python' else 'sample')
    result = json.loads(await tools.scaffold({
        'path': str(source), 'pack_id': 'sample', 'name': 'sample',
        'plugin_type': 'standalone_tool' if kind == 'python' else 'tool_pack',
    }, PluginContext(workspace=tmp_path)))
    assert result['ok']
    if kind in ('zip', 'flat_zip'):
        archive = tmp_path / 'sample.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for file in source.rglob('*'):
                if file.is_file():
                    z.write(file, file.relative_to(source.parent if kind == 'zip' else source))
        source = archive
    response = web.post('/api/plugins/install-file', json={'path': str(source)})
    assert response.status_code == 200, response.text
    assert response.json()['loaded']
    assert host.registry.resolve('SampleTool')
    assert web.post('/api/plugins/install-file', json={'path': str(source)}).status_code == 400


def test_archive_traversal_and_invalid_payload_are_rejected(tmp_path, client):
    web, host = client
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../escaped.py', 'raise RuntimeError()')
    assert web.post('/api/plugins/install-file', json={'path': str(archive)}).status_code == 400
    assert list(host.plugin_directory.iterdir()) == []
    assert web.post('/api/plugins/install-file', json=[]).status_code == 400
    assert web.post('/api/plugins/install-file', json={'path': ''}).status_code == 400
