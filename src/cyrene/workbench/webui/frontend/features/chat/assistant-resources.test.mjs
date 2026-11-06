import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';
const settle = async () => {for (let i=0; i<15; i++) await Promise.resolve();};
function all(element, type) {
  if (!element) return [];
  if (Array.isArray(element)) return element.flatMap(e => all(e,type));
  return (element.type === type ? [element] : []).concat(all(element.props?.children, type));
}
test('task files deduplicate outputs and preserve owning workspace URLs', () => {
  const h = harness('../shell/assistant-task-resources.jsx');
  h.run(mod => mod.assistantTaskFiles({id:'task-a', files:[{path:'reports/a b.md',source:'agent'}],
    messages:[{role:'assistant',attachments:[{path:'reports/a b.md', name:'Report'}]},
      {role:'assistant',attachments:[{path:'reports/a b.md', name:'Updated report'}]},
      {role:'user',attachments:[{id:'input',url:'/uploads/input.pdf',name:'Input'}]}]}));
  assert.equal(h.value.length, 1);
  assert.equal(h.value[0].output, true);
  assert.equal(h.value[0].source, 'assistant-task');
  assert.equal(h.value[0].url, '/api/workbench/chats/task-a/files/reports/a%20b.md');
  assert.equal(h.value[0].name, 'Updated report');
});
test('task outputs exclude scratch files, uploads, tool results and pending questions', () => {
  const h = harness('../shell/assistant-task-resources.jsx');
  const chat = {id:'task-a', files:[{path:'dbg.png'}, {path:'grab.mjs'}, {path:'report.pdf'}],
    generatedFiles:[{path:'dump.mjs'}], messages:[
      {role:'user',attachments:[{url:'/uploads/input.pdf'}]},
      {role:'tool',attachments:[{path:'probe.png'}]},
      {role:'assistant',questionPrompt:true,attachments:[{path:'login.png'}]},
    ]};
  h.run(mod => mod.assistantTaskFiles(chat));
  assert.equal(h.value.length, 0);
  chat.messages.push({role:'assistant',attachments:[
    {path:'report.pdf'}, {url:'/media/final.png',name:'Final image'},
  ]});
  h.flush();
  assert.deepEqual(Array.from(h.value, file => file.url), [
    '/api/workbench/chats/task-a/files/report.pdf', '/media/final.png',
  ]);
  assert.ok(h.value.every(file => file.output));
});
test('resource rows open files, owned terminals and every browser tab in the originating task', async () => {
  const React = {createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const opened=[], requests=[];
  let query='';
  const h = harness('../shell/assistant-task-resources.jsx', {React,WBC_ICONS:{},wbcFormatTime:()=>'',wbcSetResourceDrag:(_e,p)=>opened.push(['drag',p]),wbcFileDragPayload:(file,ownerSessionId)=>({kind:'file',file,ownerSessionId}),
    workbenchServices:{chat:()=>({Model:{getChat:async()=>({id:'a',files:[{path:'dbg.png'},{path:'grab.mjs'}],
        messages:[{role:'assistant',attachments:[{path:'result.md'}]}]})}}),
      api:()=>({json:async url=>{requests.push(url);return {terminals:[{id:'t',ownerChatId:'a'},{id:'other',ownerChatId:'b'}]};}})}});
  h.context.setTimeout=h.context.window.setTimeout;h.context.clearTimeout=h.context.window.clearTimeout;
  h.context.window.cyrene={browser:{getState:async id=>({tabs:[{id:'tab1',title:'One'},{id:'tab2',title:'Two'}]})}};
  h.run((mod,hooks)=>{
    Object.assign(React,{useState:hooks.useWbcState,useEffect:hooks.useWbcEffect});
    return mod.AssistantTaskResources({task:{session_id:'a'},query,onOpen:(...args)=>opened.push(args)});
  });
  await settle();h.flush();
  const rows=all(h.value,'div').filter(row=>row.props.role==='button');
  assert.equal(rows.length,4);
  rows.forEach(row=>row.props.onClick());
  assert.deepEqual(opened.map(x=>x.slice(0,2)),[['a','file'],['a','terminal'],['a','browser'],['a','browser']]);
  assert.equal(opened[1][2],'t'); assert.equal(opened[3][2],'tab2');
  assert.equal(requests[0],'/api/terminals?ownerChatId=a');
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
