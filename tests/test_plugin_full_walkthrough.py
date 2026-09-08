import json
import httpx
import pytest
from fastapi import FastAPI, APIRouter
from cyrene.core import AgentSession
from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry, PluginRuntime
from cyrene.plugins.application import PluginApplicationHost
from cyrene.plugins.builtin.cyrene_plugin_development import tools, plugin_pack as development

@pytest.mark.asyncio
@pytest.mark.parametrize('kind', tools.SCAFFOLD_TYPES)
async def test_walkthrough(tmp_path,monkeypatch,kind):
    root=tmp_path/'installed'
    root.mkdir()
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    registry=PluginRegistry()
    registry.register_pack(development,source='development-test')
    hosts=[]
    def new_host(registry):
        host=PluginApplicationHost(app=FastAPI(),registry=registry,bot=None,db_path=str(tmp_path/'state.db'),data_directory=tmp_path/'data',plugin_directory=root)
        original=host.reload_user_plugins
        async def reload():return await original(seed=False)
        monkeypatch.setattr(host,'reload_user_plugins',reload)
        monkeypatch.setattr(tools,'application_plugin_scope',lambda:host)
        router=APIRouter()
        host.attach(router)
        host.app.include_router(router)
        hosts.append(host)
        return host
    from cyrene.platform import settings_store
    monkeypatch.setattr(settings_store,'save_enabled_plugins',lambda v:None)
    monkeypatch.setattr(settings_store,'save_enabled_plugin_packs',lambda v:None)
    host=new_host(registry)
    await host.startup()
    runtime=PluginRuntime(registry)
    ctx=PluginContext(workspace=workspace,data={'language':'en','run_id':'walkthrough'})
    async def author(name,args):
        described=await runtime.call('toolbox',{'operation':'describe','name':name},ctx)
        assert described.success,described
        result=await runtime.call('toolbox',{'operation':'invoke','name':name,'arguments':args},ctx)
        assert result.success,result
        assert 'error' not in result.value,result.value
        return json.loads(result.value['result'])
    assert (await author('PluginAuthoringGuide',{}))['ok']
    source='example.py' if kind=='standalone_tool' else 'example'
    result=await author('PluginScaffold',{'path':source,'plugin_type':kind,'pack_id':'example','name':'Example'})
    assert result['ok'],result
    assert (await author('PluginValidate',{'path':source}))['ok']
    installed=await author('PluginInstall',{'path':source})
    assert installed['ok'] and installed['loaded'] and installed['enabled'],installed
    app=kind in ('application_plugin','ui_plugin','full_pack')
    assert installed['restart_required']==app
    if app:
        assert not installed['application_running']
        await host.shutdown()
        restored=PluginRegistry(activation=registry.activation)
        assert not restored.load_directory(root)
        restored.register_pack(development,source='development-test')
        host=new_host(restored)
        registry=restored
        runtime=PluginRuntime(registry)
        await host.startup()
        assert host.pack_running('example') and not host.pack_restart_required('example')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host.app),base_url='http://test') as client:
            response=await client.get('/example/status')
            assert response.status_code==200,response.text
            assert response.json()['ok']
    if kind in ('tool_pack','standalone_tool','full_pack'):
        listed=await runtime.call('toolbox',{'operation':'list'},ctx)
        assert listed.success
        result=await runtime.call('toolbox',{'operation':'invoke','name':'ExampleTool','arguments':{'message':'walkthrough'}},ctx)
        assert result.value['result']['message']=='walkthrough',result
    if kind in ('context_plugin','full_pack'):
        registry.register_plugin(Plugin('WalkModel','test model',{'type':'object'},lambda a,c:{'content':''},kind='model'),source='test-model')
        session=AgentSession(tmp_path/'session',workspace,root,registry=registry,load_plugins=False,application_scope=host,model_plugin='WalkModel')
        try:
            assert 'Example' in await session.build_session_context()
            assert not session._plugins.setup_failures
        finally:
            session.close()
    if kind in ('ui_plugin','full_pack'):
        assert (root/'example'/'ui/index.html').is_file()
        assert (await host.call_frontend_method('example','ping',{'hello':True},project_id='project'))['echo']=={'hello':True}
    if kind in ('model_plugin','full_pack'):
        real_client=httpx.AsyncClient
        def respond(request):
            if request.url.path.endswith('/models'):
                return httpx.Response(200,json={'data':[{'id':'test-model'}]})
            return httpx.Response(200,json={'model':'test-model','choices':[{'message':{'content':'model works'},'finish_reason':'stop'}],'usage':{}})
        with monkeypatch.context() as patch:
            patch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(respond),**kw))
            discovery=await runtime.call('ExampleModel',{'operation':'list_models'},ctx)
            assert discovery.value['models'][0]['id']=='test-model',discovery
            completion=await runtime.call('ExampleModel',{'messages':[{'role':'user','content':'hello'}],'model':'test-model'},ctx)
            assert completion.value['content']=='model works',completion
    identity='ExampleTool' if kind=='standalone_tool' else 'example'
    target_kind='plugin' if kind=='standalone_tool' else 'pack'
    disabled=await author('PluginManager',{'action':'disable','kind':target_kind,'id':identity})
    assert disabled['ok'] and not disabled['enabled']
    if app:
        assert not host.pack_running('example')
    enabled=await author('PluginManager',{'action':'enable','kind':target_kind,'id':identity})
    assert enabled['ok'] and enabled['enabled']
    if app:
        assert host.pack_running('example')
    edit_path=source if kind=='standalone_tool' else 'example/__init__.py'
    read=await author('PluginSourceManager',{'action':'read','path':edit_path})
    original=read['content']
    bad=await author('PluginSourceManager',{'action':'write','path':edit_path,'content':original+'\nraise RuntimeError("walkthrough load error")\n','expected_sha256':read['sha256']})
    assert not bad['ok'] and bad['failures']
    broken=await author('PluginManager',{'action':'list'})
    assert any(item['id']==source for item in broken['failed_sources']),broken
    read=await author('PluginSourceManager',{'action':'read','path':edit_path})
    repaired=await author('PluginSourceManager',{'action':'write','path':edit_path,'content':original+'\n# repaired\n','expected_sha256':read['sha256']})
    assert repaired['ok'],repaired
    assert (await author('PluginReload',{}))['ok']
    deleted=await author('PluginManager',{'action':'delete','kind':target_kind,'id':identity})
    assert deleted['ok'] and not (root/source).exists(),deleted
    assert (await author('PluginInstall',{'path':source}))['ok']
    await host.shutdown()
