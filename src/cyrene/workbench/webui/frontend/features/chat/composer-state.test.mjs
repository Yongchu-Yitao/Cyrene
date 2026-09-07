import assert from 'node:assert/strict'
import test from 'node:test'
import {harness} from './hook-harness.test.mjs'

const settle = async () => { for(let i=0;i<8;i++) await Promise.resolve(); };

function draftHarness(namespace = '') {
  const saved=new Map([[':b','B draft']]);
  const h=harness('./composer-draft.jsx',{
    WBC_DRAFT_SAVE_DELAY_MS:150,
    wbcLoadDraft:(id,ns) => saved.get(`${ns}:${id}`)||'',
    wbcSaveDraft:(id,text,ns) => saved.set(`${ns}:${id}`,text),
  });
  let chatId='a';
  h.run((mod, hooks) => {
    const state=mod.useWbcComposerDraft(chatId,namespace);
    hooks.useWbcEffect(() => {
      if(state.prevChatIdRef.current!==chatId) {
        state.switchChat(chatId);
        state.prevChatIdRef.current=chatId;
      }
    },[chatId]);
    return state;
  });
  return {h,saved,switchChat(id) {chatId=id;h.flush();}};
}

test('draft is debounced, saved before switching, restored and flushed on page hide', () => {
  const {h,saved,switchChat}=draftHarness();
  h.value.setDraft('A typed');h.flush();
  assert.equal(saved.get(':a'),undefined);
  assert.equal(h.timers.size,1);
  switchChat('b');
  assert.equal(saved.get(':a'),'A typed');
  assert.equal(h.value.draft,'B draft');
  h.value.setDraft('B changed');h.flush();h.listeners.get('pagehide')();
  assert.equal(saved.get(':b'),'B changed');
  assert.equal(h.timers.size,0);
  switchChat('a');assert.equal(h.value.draft,'A typed');
  h.unmount();assert.equal(h.listeners.size,0);assert.equal(h.timers.size,0);
});

test('draft surfaces stay isolated and optimistic clear persists immediately', () => {
  const main=draftHarness(), quick=draftHarness('quick');
  main.h.value.setDraft('main');main.h.flush();
  quick.h.value.setDraft('quick');quick.h.flush();
  quick.h.context.document.visibilityState='hidden';quick.h.listeners.get('visibilitychange')();
  assert.equal(quick.saved.get('quick:a'),'quick');
  main.h.value.draftRef.current='';main.h.value.setDraft('');main.h.value.persistCurrentDraft();
  assert.equal(main.saved.get(':a'),'');
  main.h.flush();main.h.value.setDraft('restored after error');main.h.flush();main.h.unmount();
  assert.equal(main.saved.get(':a'),'restored after error');
  assert.equal(quick.saved.get('quick:a'),'quick');quick.h.unmount();
});

function resourceHarness() {
  const requests=[],errors=[];
  const h=harness('./composer-resources.jsx',{workbenchServices:{api:()=>({
    json(url,options) {return new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));},
    toastError(error) {errors.push(error);},
  })}});
  return {h,requests,errors};
}

test('context changes abort old requests and cannot apply stale results; unmount cleans up', async () => {
  const {h,requests}=resourceHarness();let project='a';
  h.run(mod=>mod.useWbcComposerContextResource(project,'/workspace',true));
  project='b';h.flush();assert.equal(requests[0].options.signal.aborted,true);
  requests[1].resolve({catalog:{},options:{},workspace_dir:'/b'});await settle();h.flush();
  requests[0].resolve({catalog:{},options:{},workspace_dir:'/a'});await settle();h.flush();
  assert.equal(h.value.contextState.workspace_dir,'/b');
  h.value.selectWorkspace('/chosen');h.flush();assert.equal(h.value.contextState.workspace_history[0],'/chosen');
  h.value.invalidate();h.flush();assert.equal(requests.length,3);assert.equal(h.value.contextState,null);
  h.unmount();assert.equal(requests[2].options.signal.aborted,true);
});

test('context failure settles loading and unavailable plugins make no request', async () => {
  const {h,requests,errors}=resourceHarness();let enabled=false;
  h.run(mod=>mod.useWbcComposerContextResource('a','/workspace',enabled));assert.equal(requests.length,0);
  enabled=true;h.flush();requests[0].reject(Error('offline'));await settle();h.flush();
  assert.equal(h.value.contextCatalogLoading,false);assert.equal(h.value.contextCatalogLoaded,true);
  assert.equal(h.value.contextState,null);assert.equal(errors.length,1);h.unmount();
});

test('command catalog loads on demand, refreshes after invalidation and respects external agents', async () => {
  const {h,requests}=resourceHarness();let draft='hello',builtin=true;
  h.run(mod=>mod.useWbcComposerCommandCatalog({builtinContextCapabilities:builtin,draft,toolsOpen:false,projectId:'a',command:'',setCommand(){}}));
  assert.equal(requests.length,0);draft='/';h.flush();assert.equal(requests.length,1);
  requests[0].resolve({commands:[{id:'summarize'}]});await settle();h.flush();
  assert.equal(h.value.slashCommandCatalog[0].id,'summarize');
  h.value.invalidate();h.flush();assert.equal(requests.length,2);
  builtin=false;h.flush();requests[1].resolve({commands:[{id:'stale'}]});await settle();h.flush();
  assert.equal(h.value.slashCommandCatalog.length,0);h.unmount();
});


test('project switch during command loading aborts the old owner and loads the new catalog',async()=>{
  const {h,requests}=resourceHarness();let projectId='a';
  h.run(mod=>mod.useWbcComposerCommandCatalog({builtinContextCapabilities:true,draft:'/',toolsOpen:false,projectId,command:'',setCommand(){}}));
  projectId='b';h.flush();
  assert.equal(requests.length,2);assert.equal(requests[0].options.signal.aborted,true);
  requests[1].resolve({commands:[{id:'b-command'}]});await settle();h.flush();
  requests[0].resolve({commands:[{id:'a-command'}]});await settle();h.flush();
  assert.equal(h.value.slashCommandCatalog[0].id,'b-command');
  assert.equal(h.value.slashCommandCatalogLoading,false);h.unmount();
});
