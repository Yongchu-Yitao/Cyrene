import test from 'node:test';
import assert from 'node:assert/strict';
import {harness as baseHarness} from './hook-harness.test.mjs';
const shared = baseHarness('./resource-splits.jsx');
const sharedFiles = shared.run(mod => mod.wbcChatArtifactFiles);
function harness(file, extras = {}) {
  if (extras.workbenchServices) extras.workbenchServices = {
    events: () => ({ subscribe: () => () => {} }), ...extras.workbenchServices,
  };
  return baseHarness(file, {wbcChatArtifactFiles: sharedFiles, ...extras});
}
const settle = async () => {for (let i=0; i<15; i++) await Promise.resolve();};
function all(element, type) {
  if (!element) return [];
  if (Array.isArray(element)) return element.flatMap(e => all(e,type));
  return (element.type === type ? [element] : []).concat(all(element.props?.children, type));
}
test('task files reuse the ordinary panel index for uploads, changes and live deliverables', () => {
  const h = harness('../shell/assistant-task-resources.jsx');
  const chat = {id:'task-a', projectId:'__personal_assistant__', files:[
    {path:'reports/report.md',name:'report.md',source:'agent'},
    {path:'debug.js',name:'debug.js',source:'agent'},
  ],messages:[
    {role:'assistant',attachments:[{path:'reports/report.md',name:'report.md',url:'/exports/report.md'}]},
    {role:'user',attachments:[{id:'input',url:'/uploads/input.pdf',name:'Input'}]},
  ],liveAgentArtifacts:[{id:'live',name:'Live image',url:'/exports/live.png'}]};
  h.run(mod => mod.assistantTaskFiles(chat));
  assert.deepEqual(Array.from(h.value, file => file.name), ['report.md','Input','debug.js','Live image']);
  assert.equal(h.value[0].url, '/exports/report.md');
  assert.equal(h.value[1].output, false);
  assert.equal(h.value[2].url, '/api/workbench/chats/task-a/files/debug.js');
  assert.equal(h.value[2].source, 'assistant-task');
});

test('resource rows open files, owned terminals and every browser tab in the originating task', async () => {
  const React = {createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const opened=[], requests=[];
  let query='';
  const h = harness('../shell/assistant-task-resources.jsx', {React,WBC_ICONS:{},wbcFormatTime:()=>'',wbcSetResourceDrag:(_e,p)=>opened.push(['drag',p]),wbcFileDragPayload:(file,ownerSessionId)=>({kind:'file',file,ownerSessionId}),
    workbenchServices:{chat:()=>({Model:{getChat:async()=>({id:'a',files:[],
        messages:[{role:'assistant',attachments:[{path:'result.md'}]}]})}}),
      api:()=>({json:async url=>{requests.push(url);
        if (url.endsWith('/resources')) return {chat:{id:'a',messages:[{role:'assistant',attachments:[{path:'result.md'}]}]}};
        return {terminals:[{id:'t',ownerChatId:'a'},{id:'other',ownerChatId:'b'}]};}})}});
  h.context.setTimeout=h.context.window.setTimeout;h.context.clearTimeout=h.context.window.clearTimeout;
  h.context.window.cyrene={browser:{getState:async id=>({tabs:[{id:'tab1',title:'One',favicon:'https://example.com/favicon.ico'},{id:'tab2',title:'Two'}]})}};
  h.run((mod,hooks)=>{
    Object.assign(React,{useState:hooks.useWbcState,useEffect:hooks.useWbcEffect});
    return mod.AssistantTaskResources({task:{session_id:'a'},query,onOpen:(...args)=>opened.push(args)});
  });
  await settle();h.flush();
  const rows=all(h.value,'div').filter(row=>row.props.role==='button');
  assert.equal(rows.length,4);
  const favicon=all(h.value,'img')[0];
  assert.equal(favicon.props.src,'https://example.com/favicon.ico');
  const failed={hidden:false};favicon.props.onError({currentTarget:failed});assert.equal(failed.hidden,true);
  rows.forEach(row=>row.props.onClick());
  assert.deepEqual(opened.map(x=>x.slice(0,2)),[['a','file'],['a','terminal'],['a','browser'],['a','browser']]);
  assert.equal(opened[1][2],'t'); assert.equal(opened[3][2],'tab2');
  assert.deepEqual(requests, ['/api/workbench/chats/a/resources', '/api/terminals?ownerChatId=a']);
  rows[3].props.onDragStart({});
  assert.equal(opened[4][1].kind,'browser');
  assert.equal(opened[4][1].ownerSessionId,'a');
  assert.equal(opened[4][1].tabId,'tab2');
  assert.equal(all(h.value,'button').length,0); // No resource groups or disclosure controls.
  query='two'; h.flush();
  const matches=all(h.value,'div').filter(row=>row.props.role==='button');
  assert.deepEqual(matches.map(row=>row.props['aria-label']), ['Two']);
  query='result.md'; h.flush();
  assert.deepEqual(all(h.value,'div').filter(row=>row.props.role==='button').map(row=>row.props['aria-label']), ['result.md']);
  h.unmount();
  assert.equal(h.timers.size,0);
});


test('browser resource drops retain source session across assistant splits', () => {
  const resource={kind:'browser',tabId:'tab-b',ownerSessionId:'task-b'};
  const h=harness('./pane-drop-controller.jsx', {
    wbcReadSplitDrag:()=>null,wbcHasChatDrag:()=>false,wbcHasPluginViewDrag:()=>false,
    wbcHasResourceDrag:()=>true,wbcReadResourceDrag:()=>resource,
  });
  h.run(mod=>mod.wbcDroppedPaneCard({activeChatIdRef:{current:'assistant_global'},
    paneContentCard:(kind,payload,ownerChatId)=>({kind,payload,ownerChatId})}, {}, {}, {}, 'chat:assistant_global','right'));
  assert.equal(h.value.card.kind,'browser');
  assert.equal(h.value.card.payload,'tab-b');
  assert.equal(h.value.card.ownerChatId,'task-b');
});

test('shared workspace files prefer the delivering task and different workspaces stay separate', () => {
  const h=harness('../shell/assistant-task-resources.jsx');
  h.run(mod=>mod.assistantFileOwners([
    {session_id:'browser',workspace_path:'/shared'}, {session_id:'writer',workspace_path:'/shared'},
    {session_id:'other',workspace_path:'/elsewhere'},
  ],{browser:[{path:'report.md'}],writer:[{path:'report.md',output:true}],other:[{path:'report.md'}]}));
  assert.equal(h.value['/shared/report.md'],'writer');
  assert.equal(h.value['/elsewhere/report.md'],'other');
});

test('clicking another task resource keeps the current split workspace and source ownership', () => {
  const h=harness('./pane-layout-controller.jsx',{
    wbcNormalizePaneLayout:value=>value||{left:[],right:[]},
    wbcPaneCard:(kind,payload,options)=>({id:kind+':'+payload,kind,payload,...options}),
    wbcPaneCardLocation:()=>null,
    openPaneLayout:(layout,card)=>({...layout,right:[...layout.right,card]}),
  });
  let layouts={assistant_global:{left:[{id:'main',kind:'chat'}],right:[]}};
  h.run(mod=>mod.wbcOpenPaneContent({activeChatIdRef:{current:'assistant_global'},projectId:'global',
    paneLayoutsByChat:layouts,setPaneLayoutsByChat:update=>{layouts=update(layouts);}},
    'browser','child-tab',{resourceOwnerChatId:'task-browser',side:'right'}));
  assert.equal(layouts.assistant_global.left[0].id,'main');
  assert.equal(layouts.assistant_global.right[0].ownerChatId,'task-browser');
  assert.equal(layouts.assistant_global.right[0].payload,'child-tab');
  assert.equal(layouts['task-browser'],undefined);
});


test('dragging an already visible browser moves its pane instead of duplicating the native tab', () => {
  const card={id:'browser-existing',kind:'browser',payload:'tab-b',ownerChatId:'task-b'};
  const h=harness('./pane-drop-controller.jsx', {
    wbcReadSplitDrag:()=>null,wbcHasChatDrag:()=>false,wbcHasPluginViewDrag:()=>false,
    wbcHasResourceDrag:()=>true,wbcReadResourceDrag:()=>({kind:'browser',tabId:'tab-b',ownerSessionId:'task-b'}),
  });
  h.run(mod=>mod.wbcDroppedPaneCard({paneContentCard:()=>assert.fail('Must reuse native tab')},
    {}, {left:[],right:[card]}, {}, 'main','bottom'));
  assert.equal(h.value.sourceCardId,'browser-existing');
  assert.equal(h.value.card,card);
});

test('main assistant deliveries keep their attachment URL and hidden resources stay hidden', async()=>{
  const React={createElement(type,props,...children){return {type,props:{...props,children}};}};
  const opened=[];
  const h=harness('../shell/assistant-task-resources.jsx',{React,WBC_ICONS:{},wbcFormatTime:()=>'',
    wbcFileDragPayload:()=>({}),workbenchServices:{chat:()=>({Model:{getChat:async()=>({messages:[]})}}),
      api:()=>({json:async()=>({terminals:[]})})}});
  h.context.setTimeout=h.context.window.setTimeout;h.context.clearTimeout=h.context.window.clearTimeout;
  h.context.window.cyrene={browser:{getState:async()=>({tabs:[{id:'tab1',title:'Hidden'}]})}};
  h.run((mod,hooks)=>{
    React.useState=hooks.useWbcState;React.useEffect=hooks.useWbcEffect;
    return mod.AssistantTaskResources({task:{session_id:'task',presentation:{hidden_resources:['browser:tab1'],
      files:[{id:'final',name:'Report',url:'/api/attachments/final'}]}},onOpen:(...args)=>opened.push(args)});
  });
  await settle();h.flush();
  const rows=all(h.value,'div').filter(row=>row.props.role==='button');
  assert.equal(rows.length,1);rows[0].props.onClick();
  assert.equal(opened[0][2].url,'/api/attachments/final');
  h.unmount();
});

function resourceObserverFixture({ hidden = false, request } = {}) {
  const requests = [], delays = new Map();
  let listener, browserListener, browserUnsubscribed = false, eventUnsubscribed = false;
  let resources = { files: [], terminals: [], tabs: [] }, error = '', updates = 0;
  const h = harness('../shell/assistant-task-resources.jsx', {
    workbenchServices: {
      chat: () => ({ Model: { getChat: () => assert.fail('Must not fetch full conversations') } }),
      api: () => ({ json: (url, options) => {
        requests.push({ url, options });
        return request ? request(url, options) : Promise.resolve(url.endsWith('/resources')
          ? { chat: { id: 'task', files: [{ path: 'report.md', source: 'agent' }] } }
          : { terminals: [{ id: 'term', ownerChatId: 'task', title: 'Shell', cwd: '/work', outputBytes: requests.length }] });
      } }),
      events: () => ({ subscribe: fn => { listener = fn; return () => { eventUnsubscribed = true; }; } }),
    },
  });
  const setTimer = h.context.window.setTimeout;
  h.context.window.setTimeout = (fn, delay) => { const id = setTimer(fn); delays.set(id, delay); return id; };
  h.context.document.hidden = hidden;
  h.context.document.visibilityState = hidden ? 'hidden' : 'visible';
  h.context.window.cyrene = { browser: {
    getState: async () => ({ tabs: [] }),
    onManagerState: fn => { browserListener = fn; return () => { browserUnsubscribed = true; }; },
  } };
  let dispose;
  h.run(mod => { dispose = mod.observeAssistantResources({ session_id: 'task', project_id: 'project' }, update => {
    const next = update(resources); if (next !== resources) updates++; resources = next;
  }, value => { error = value; }); });
  return {
    h, requests, event: event => listener(event), browser: state => browserListener(state),
    get resources() { return resources; }, get error() { return error; }, get updates() { return updates; },
    hide(value) { h.context.document.hidden = value; h.context.document.visibilityState = value ? 'hidden' : 'visible'; h.listeners.get('visibilitychange')(); },
    fireTimer() { assert.equal(h.timers.size, 1); const [id, fn] = [...h.timers][0]; h.timers.delete(id); fn(); },
    get delay() { return delays.get([...h.timers.keys()][0]); },
    dispose() { dispose(); assert.equal(h.timers.size, 0); assert.equal(eventUnsubscribed, true); assert.equal(browserUnsubscribed, true); assert.equal(h.listeners.size, 0); },
  };
}

test('hidden resources do no work; visibility restores immediately and reconciles once per minute', async () => {
  const f = resourceObserverFixture({ hidden: true });
  assert.equal(f.requests.length, 0);
  f.event({ type: 'workbench_chat_changed', chat_id: 'task' });
  assert.equal(f.h.timers.size, 0);
  f.hide(false); await settle();
  assert.equal(f.requests.length, 2);
  assert.equal(f.delay, 60000);
  const first = f.resources;
  f.fireTimer(); await settle();
  assert.equal(f.requests.length, 4);
  assert.equal(f.resources, first, 'unchanged data including volatile terminal statistics must not rerender');
  assert.equal(f.updates, 1);
  f.hide(true);
  assert.equal(f.h.timers.size, 0);
  assert.equal(f.requests.at(-1).options.signal.aborted, true);
  f.hide(false); await settle();
  assert.equal(f.requests.length, 6);
  f.dispose();
});

test('resource events are scoped and bursts coalesce; browser pushes never refetch conversation files', async () => {
  const f = resourceObserverFixture(); await settle();
  f.event({ type: 'workbench_chat_changed', chat_id: 'other' });
  f.event({ type: 'terminal_list_changed', project_id: 'other-project' });
  f.event({ type: 'tool_call', chat_id: 'task' });
  assert.equal(f.delay, 60000);
  f.event({ type: 'workspace_changes', chatId: 'task' });
  f.event({ type: 'workbench_chat_changed', chat_id: 'task' });
  assert.equal(f.h.timers.size, 1); assert.equal(f.delay, 200);
  f.fireTimer(); await settle(); assert.equal(f.requests.length, 4);
  const files = f.resources.files;
  f.browser({ pages: [{ id: 'tab', sessionId: 'task', title: 'New page', url: '/page' }, { id: 'other', sessionId: 'other' }] });
  assert.equal(f.resources.tabs.length, 1);
  assert.equal(f.resources.tabs[0].title, 'New page');
  assert.equal(f.resources.files, files);
  assert.equal(f.requests.length, 4);
  f.browser({ pages: [] }); assert.equal(f.resources.tabs.length, 0, 'closed tabs disappear');
  f.event({ type: 'terminal_list_changed', project_id: 'project' });
  assert.equal(f.delay, 200);
  f.dispose();
});

test('an in-flight refresh coalesces events and cannot overwrite newer browser state', async () => {
  let resolveFiles;
  const f = resourceObserverFixture({ request: url => url.endsWith('/resources')
    ? new Promise(resolve => { resolveFiles = resolve; }) : Promise.resolve({ terminals: [] }) });
  f.event({ type: 'workspace_changes', chatId: 'task' });
  f.event({ type: 'workbench_chat_changed', chat_id: 'task' });
  assert.equal(f.requests.length, 2); assert.equal(f.h.timers.size, 0);
  f.browser({ pages: [{ id: 'new', sessionId: 'task', title: 'New' }] });
  resolveFiles({ chat: { files: [{ path: 'new.md' }] } }); await settle();
  assert.equal(f.resources.files[0].path, 'new.md');
  assert.equal(f.resources.tabs[0].id, 'new');
  assert.equal(f.delay, 200);
  f.fireTimer(); assert.equal(f.requests.length, 4);
  f.dispose();
  resolveFiles({ chat: { files: [{ path: 'stale.md' }] } }); await settle();
  assert.equal(f.resources.files[0].path, 'new.md');
  assert.equal(f.h.timers.size, 0);
});

test('hide and restore during a request rejects the stale result and schedules one fresh snapshot', async () => {
  let resolveFiles;
  const f = resourceObserverFixture({ request: url => url.endsWith('/resources')
    ? new Promise(resolve => { resolveFiles = resolve; }) : Promise.resolve({ terminals: [] }) });
  f.hide(true); assert.equal(f.requests[0].options.signal.aborted, true);
  f.hide(false); assert.equal(f.requests.length, 2);
  resolveFiles({ chat: { files: [{ path: 'stale.md' }] } }); await settle();
  assert.equal(f.resources.files.length, 0);
  assert.equal(f.delay, 200);
  f.fireTimer(); resolveFiles({ chat: { files: [{ path: 'fresh.md' }] } }); await settle();
  assert.equal(f.resources.files[0].path, 'fresh.md');
  assert.equal(f.delay, 60000);
  f.dispose();
});

test('a failed resource source retains its last good data and later reconciliation clears the error', async () => {
  let fail = false;
  const f = resourceObserverFixture({ request: url => {
    if (url.endsWith('/resources')) return fail ? Promise.reject(Error('offline')) : Promise.resolve({ chat: { files: [{ path: 'saved.md' }] } });
    return Promise.resolve({ terminals: [{ id: fail ? 'new-terminal' : 'old-terminal', ownerChatId: 'task' }] });
  } });
  await settle(); const files = f.resources.files;
  fail = true; f.fireTimer(); await settle();
  assert.equal(f.resources.files, files);
  assert.equal(f.resources.terminals[0].id, 'new-terminal');
  assert.notEqual(f.error, '');
  fail = false; f.fireTimer(); await settle();
  assert.equal(f.error, ''); f.dispose();
});
