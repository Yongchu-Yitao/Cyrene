import assert from 'node:assert/strict'
import test from 'node:test'
import {harness} from './hook-harness.test.mjs'

test('project projections retain identity and never overwrite another project cache', () => {
  const h=harness('./page-state.jsx'), cache={lists:{},details:{}}, notifications=[];
  let project='a';
  h.run(mod=>mod.useWbcChatProjections(project,cache,(...args)=>notifications.push(args)));
  const a=[{id:'a1',projectId:'a',messages:[]}];
  h.value.chatsProjectIdRef.current='a';h.value.setChats(a);h.value.setActiveChat(a[0]);h.flush();
  assert.equal(cache.lists.a,a);assert.equal(cache.details.a1,a[0]);assert.equal(h.value.chatsRef.current,a);
  assert.deepEqual(notifications.at(-1),['a',a]);
  const count=notifications.length;
  project='b';h.flush();
  assert.equal(notifications.length,count);assert.equal(cache.lists.b,undefined);
  // Late data still belongs to A even when the page has rendered B.
  const late=[{id:'a2',projectId:'a'}];
  h.value.setChats(late);h.value.setActiveChat(late[0]);h.flush();
  assert.equal(cache.lists.a,a);assert.equal(cache.lists.b,undefined);assert.equal(cache.details.a2,undefined);
  h.value.chatsProjectIdRef.current='b';
  h.value.setChats([{id:'b1',projectId:'b'},{id:'a1',projectId:'a'}]);h.flush();
  assert.equal(cache.lists.b,undefined);
  const b=[{id:'b1',projectId:'b'}];h.value.setChats(b);h.value.setActiveChat(b[0]);h.flush();
  assert.equal(cache.lists.b,b);assert.equal(cache.details.b1,b[0]);
  assert.deepEqual(notifications.at(-1),['b',b]);
  h.value.setChats([]);h.flush();assert.equal(cache.lists.b.length,0);
  assert.deepEqual(notifications.at(-1),['b',[]]);
  h.unmount();
});

test('chat projections tolerate a missing callback and do not notify without a project', () => {
  const notifications=[];
  for (const [project, callback] of [['a',undefined],['',(...args)=>notifications.push(args)]]) {
    const h=harness('./page-state.jsx');
    h.run(mod=>mod.useWbcChatProjections(project,{lists:{},details:{}},callback));
    h.value.setChats([{id:'a1',projectId:'a'}]);h.flush();
    h.unmount();
  }
  assert.deepEqual(notifications,[]);
});

test('draft Agent choice persists per project while consumption only clears active state', () => {
  const saved=new Map([['a',{id:'agent-a'}],['b',{id:'agent-b'}]]);
  const h=harness('./page-state.jsx',{
    wbcLoadDraftAgentBinding:id=>saved.get(id)||null,
    wbcSaveDraftAgentBinding:(id,value)=>saved.set(id,value),
  });
  let project='a', enabled=true;
  h.run((mod,hooks)=>{
    const state=mod.useWbcDraftAgentBinding(project,enabled);
    // Project hydration owns this reset, at its existing effect boundary.
    hooks.useWbcEffect(()=>state.setDraftAgentBinding(saved.get(project)||null),[project]);
    return state;
  });
  assert.equal(h.value.draftAgentBindingRef.current.id,'agent-a');
  const selected={id:'chosen'};h.value.handleDraftAgentChange(selected);h.flush();
  assert.equal(saved.get('a'),selected);assert.equal(h.value.draftAgentBindingRef.current,selected);
  h.value.setDraftAgentBinding(null);h.flush();
  assert.equal(h.value.draftAgentBindingRef.current,null);assert.equal(saved.get('a'),selected);
  project='b';h.flush();assert.equal(h.value.draftAgentBinding.id,'agent-b');
  enabled=false;h.flush();assert.equal(saved.get('b'),null);assert.equal(h.value.draftAgentBindingRef.current,null);
  assert.equal(saved.get('a'),selected);
  enabled=true;project='a';h.flush();assert.equal(h.value.draftAgentBinding,selected);
  h.value.handleDraftAgentChange(undefined);h.flush();assert.equal(saved.get('a'),null);
  h.unmount();
});

test('split side survives remount and drag assignment avoids redundant storage writes', () => {
  let saved='left',writes=0;
  const storage={getItem:()=>saved,setItem:(_key,value)=>{saved=value;writes++;}};
  const h=harness('./page-state.jsx',{localStorage:storage});
  h.run(mod=>mod.useWbcSplitSide());assert.equal(h.value.splitSide,'left');
  h.value.setSplitSideDirect('left');h.flush();assert.equal(writes,0);
  h.value.toggleSplitSide();h.flush();assert.equal(saved,'right');assert.equal(writes,1);
  h.value.setSplitSideDirect('left');h.flush();assert.equal(saved,'left');assert.equal(writes,2);h.unmount();
  const remount=harness('./page-state.jsx',{localStorage:storage});remount.run(mod=>mod.useWbcSplitSide());
  assert.equal(remount.value.splitSide,'left');remount.unmount();
});

test('unavailable local storage does not prevent split interaction', () => {
  const h=harness('./page-state.jsx',{localStorage:{getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}}});
  h.run(mod=>mod.useWbcSplitSide());assert.equal(h.value.splitSide,'right');
  h.value.toggleSplitSide();h.flush();assert.equal(h.value.splitSide,'left');
  h.value.setSplitSideDirect('right');h.flush();assert.equal(h.value.splitSide,'right');h.unmount();
});
