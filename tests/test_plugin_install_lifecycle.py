import json
import pytest
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
    from cyrene.platform import settings_store
    monkeypatch.setattr(settings_store,'save_enabled_plugins',lambda v:None)
    monkeypatch.setattr(settings_store,'save_enabled_plugin_packs',lambda v:None)
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
