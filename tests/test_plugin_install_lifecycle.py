import json
import pytest

pytestmark = pytest.mark.usefixtures("isolated_plugin_settings")
from fastapi import FastAPI
from cyrene.core.plugin import PluginContext, PluginRegistry
from cyrene.plugins.application import PluginApplicationHost
from cyrene.plugins.builtin.cyrene_plugin_development import tools

@pytest.fixture
def host(tmp_path,monkeypatch):
    root=tmp_path/'installed'
    root.mkdir()
    registry=PluginRegistry()
    value=PluginApplicationHost(app=FastAPI(),registry=registry,bot=None,db_path=str(tmp_path/'state.db'),data_directory=tmp_path/'data',plugin_directory=root)
    original=value.reload_user_plugins
    async def reload(): return await original(seed=False)
    monkeypatch.setattr(value,'reload_user_plugins',reload)
    monkeypatch.setattr(tools,'application_plugin_scope',lambda:value)
    return value

async def draft(tmp_path,name='sample',kind='tool_pack'):
    ctx=PluginContext(workspace=tmp_path,data={"language":"en"})
    result=json.loads(await tools.scaffold({'path':name,'pack_id':name,'plugin_type':kind,'name':name},ctx))
    assert result['ok']
    return ctx

@pytest.mark.asyncio
async def test_unrelated_broken_plugin_does_not_fail_install(tmp_path,host):
    (host.plugin_directory/'broken.py').write_text('raise RuntimeError("unrelated failure")')
    ctx=await draft(tmp_path)
    result=json.loads(await tools.install({'path':'sample'},ctx))
    assert result['ok'] and result['loaded'] and result['enabled']
    assert result['failures'] == []
    assert host.registry.resolve('SampleTool')
    assert result['other_failures'][0]['path'].endswith('broken.py')

@pytest.mark.asyncio
async def test_failed_install_can_be_deleted_and_reinstalled(tmp_path,host):
    ctx=await draft(tmp_path)
    source=tmp_path/'sample'/'tool.py'
    source.write_text('raise RuntimeError("load failed")\n'+source.read_text())
    assert json.loads(await tools.validate({'path':'sample'},ctx))['ok']
    result=json.loads(await tools.install({'path':'sample'},ctx))
    assert not result['ok']
    listing=json.loads(await tools.manage_plugins({'action':'list'},ctx))
    assert 'sample' not in [p['id'] for p in listing['packs']]
    assert listing['failed_sources'][0]['id'] == 'sample'
    assert listing['failed_sources'][0]['loaded'] is False
    assert (host.plugin_directory/'sample').is_dir()
    retry=json.loads(await tools.install({'path':'sample'},ctx))
    assert 'already exists' in retry['error']
    deleted=json.loads(await tools.manage_plugins(result['recovery']['delete'],ctx))
    assert deleted['ok']
    assert not (host.plugin_directory/'sample').exists()
    source.write_text(source.read_text().split('\n',1)[1])
    installed=json.loads(await tools.install({'path':'sample'},ctx))
    assert installed['ok'] and installed['loaded']

@pytest.mark.asyncio
async def test_enable_application_reports_restart_requirement(tmp_path,host):
    ctx=await draft(tmp_path,kind='application_plugin')
    await tools.install({'path':'sample'},ctx)
    await tools.manage_plugins({'action':'disable','kind':'pack','id':'sample'},ctx)
    result=json.loads(await tools.manage_plugins({'action':'enable','kind':'pack','id':'sample'},ctx))
    assert result['ok']
    assert host.pack_restart_required('sample')
    assert result['restart_required']
    assert result['application_running'] is False
    listing=json.loads(await tools.manage_plugins({'action':'list'},ctx))
    entry=next(p for p in listing['packs'] if p['id']=='sample')
    assert entry['enabled']
    assert entry['restart_required']
    assert entry['application_running'] is False

@pytest.mark.asyncio
async def test_install_reports_preserved_disabled_override(tmp_path,host):
    ctx=await draft(tmp_path)
    host.registry.activation.replace(plugins={},packs={'sample':False})
    result=json.loads(await tools.install({'path':'sample'},ctx))
    assert result['ok']
    assert not host.registry.pack_enabled('sample')
    assert result['enabled'] is False

@pytest.mark.asyncio
async def test_failed_source_deletion_rejects_other_paths(tmp_path,host):
    ctx=await draft(tmp_path)
    outside=tmp_path/'keep.py'
    outside.write_text('important = True')
    (host.plugin_directory/'healthy.py').write_text('important = True')
    for identity in ('../keep.py',str(outside),'healthy.py','.upstream-hashes.json'):
        result=json.loads(await tools.manage_plugins({'action':'delete','kind':'source','id':identity},ctx))
        assert not result['ok']
    assert outside.exists()
    assert (host.plugin_directory/'healthy.py').exists()

@pytest.mark.asyncio
async def test_standalone_failed_source_recovery(tmp_path,host):
    ctx=await draft(tmp_path,name='solo.py',kind='standalone_tool')
    source=tmp_path/'solo.py'
    source.write_text(source.read_text()+'\nraise RuntimeError("broken")\n')
    result=json.loads(await tools.install({'path':'solo.py'},ctx))
    assert not result['loaded']
    listing=json.loads(await tools.manage_plugins({'action':'list'},ctx))
    assert listing['failed_sources'][0]['id']=='solo.py'
    assert json.loads(await tools.manage_plugins(result['recovery']['delete'],ctx))['ok']

@pytest.mark.asyncio
async def test_manager_surfaces_application_startup_errors(tmp_path,host):
    ctx=await draft(tmp_path,kind='application_plugin')
    await tools.install({'path':'sample'},ctx)
    host._startup_failures['sample']='startup failed'
    listing=json.loads(await tools.manage_plugins({'action':'list'},ctx))
    entry=next(p for p in listing['packs'] if p['id']=='sample')
    assert entry['startup_error']=='startup failed'


@pytest.mark.asyncio
async def test_update_zip_preserves_data_and_activation(tmp_path, host):
    from shutil import make_archive
    from cyrene.plugins.builtin.cyrene_plugin_development import plugin_pack

    ctx = await draft(tmp_path)
    installed = host.plugin_directory / "sample"
    assert json.loads(await tools.install({"path": "sample"}, ctx))["ok"]
    await tools.manage_plugins({"action": "disable", "kind": "pack", "id": "sample"}, ctx)
    (installed / "obsolete.txt").write_text("old")
    data = host.data_directory / "plugin_data" / "sample" / "saved.txt"
    data.parent.mkdir(parents=True)
    data.write_text("keep")
    (tmp_path / "sample" / "view.html").write_text("new")
    make_archive(str(tmp_path / "update"), "zip", tmp_path, "sample")
    handler = next(plugin.handler for plugin in plugin_pack.plugins if plugin.name == "PluginUpdate")
    result = json.loads(await handler({"path": "update.zip", "id": "sample"}, ctx))
    assert result["ok"] and result["loaded"] and not result["enabled"]
    assert not (installed / "obsolete.txt").exists()
    assert (installed / "view.html").read_text() == "new"
    assert data.read_text() == "keep"


@pytest.mark.asyncio
async def test_update_rejects_other_identity_and_restores_on_load_failure(tmp_path, host):
    ctx = await draft(tmp_path)
    assert json.loads(await tools.install({"path": "sample"}, ctx))["ok"]
    file = tmp_path / "sample" / "tool.py"
    old = file.read_bytes()
    file.write_bytes(b'raise RuntimeError("bad update")\n' + old)
    wrong = json.loads(await tools.update_plugin({"path": "sample", "id": "other"}, ctx))
    assert not wrong["ok"] and "identity" in wrong["error"]
    result = json.loads(await tools.update_plugin({"path": "sample", "id": "sample"}, ctx))
    assert not result["ok"] and result["rolled_back"] and result["failures"]
    assert (host.plugin_directory / "sample" / "tool.py").read_bytes() == old
    assert host.registry.resolve("SampleTool")


@pytest.mark.asyncio
async def test_standalone_update_uses_registered_filename(tmp_path, host):
    ctx = await draft(tmp_path, name="solo.py", kind="standalone_tool")
    install = json.loads(await tools.install({"path": "solo.py"}, ctx))
    assert install["ok"]
    new = tmp_path / "renamed.py"
    new.write_bytes((tmp_path / "solo.py").read_bytes() + b"\n# upgraded\n")
    result = json.loads(await tools.update_plugin({"id": install["identity"], "path": new.name}, ctx))
    assert result["ok"] and (host.plugin_directory / "solo.py").read_bytes() == new.read_bytes()
    assert not (host.plugin_directory / new.name).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_update_requires_restart_and_verifies_new_host(tmp_path, host, failed, monkeypatch):
    from fastapi import APIRouter

    monkeypatch.setattr("cyrene.plugins.native_tools._collect_canonical_files", lambda: {})

    ctx = await draft(tmp_path, kind="application_plugin")
    assert json.loads(await tools.install({"path": "sample"}, ctx))["ok"]
    source = tmp_path / "sample" / "application.py"
    old = source.read_bytes()
    if failed:
        source.write_bytes(old.replace(b"async def startup() -> None:\n    pass",
                                       b'async def startup() -> None:\n    raise RuntimeError("startup failed")'))
    else:
        source.write_bytes(old + b"\n# updated\n")
    result = json.loads(await tools.update_plugin({"id": "sample", "path": "sample"}, ctx))
    assert result["ok"] and result["restart_required"]
    restarted = PluginApplicationHost.load_user_plugins(
        app=FastAPI(), bot=None, db_path=host.db_path, data_directory=host.data_directory,
        plugin_directory=host.plugin_directory,
    )
    restarted.attach(APIRouter())
    await restarted.startup()
    assert (host.plugin_directory / "sample" / "application.py").read_bytes() == (old if failed else source.read_bytes())
    assert not (host.plugin_directory / ".plugin-updates" / "sample").exists()
    await restarted.shutdown()


@pytest.mark.parametrize("new_in_place", [False, True])
def test_interrupted_update_restores_complete_previous_source(tmp_path, new_in_place):
    from contextlib import ExitStack
    from cyrene.plugins.update import prepare_plugin_updates

    previous = tmp_path / ".plugin-updates" / "sample" / "previous"
    previous.mkdir(parents=True)
    (previous / "old").write_text("keep")
    installed = tmp_path / "sample"
    if new_in_place:
        installed.mkdir()
        (installed / "new").touch()
    with ExitStack() as boot:
        assert prepare_plugin_updates(tmp_path, boot) == ()
    assert [p.name for p in installed.iterdir()] == ["old"]
    with ExitStack() as boot:
        assert prepare_plugin_updates(tmp_path, boot) == ()
