import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';
const settle = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
function find(element, type) {
  if (!element) return null;
  if (Array.isArray(element)) return element.map(e=>find(e,type)).find(Boolean) || null;
  if (element.type===type) return element;
  return find(element.props?.children,type);
}
function mount(shared = {}) {
  const React = {createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const sends=[],answers=[],subscribers=new Set();
  const service={Composer:'composer',Transcript:'transcript',QuestionPrompt:'question',
    Model:{getChat:async()=>({id:'a',messages:[],pendingQuestion:{id:'q'}}), answerChat:async(...args)=>answers.push(args)},
    Runtimes:{get:()=>null,subscribe(fn) {subscribers.add(fn);return ()=>subscribers.delete(fn);},
      subscribeSaved:()=>()=>{},
      start:async(...args)=>sends.push(args),getFailure:()=>null}};
  const h=harness('../shell/assistant-chat.jsx',{...shared,React,useWbcComposerReserveHeight:()=>{},workbenchServices:{chat:()=>service}});
  h.context.setTimeout=h.context.window.setTimeout;
  h.context.clearTimeout=h.context.window.clearTimeout;
  h.run((mod,hooks)=>{
    Object.assign(React,{useState:hooks.useWbcState,useRef:hooks.useWbcRef,useEffect:hooks.useWbcEffect});
    return mod.AssistantChat({sessionId:'a',project:{id:'p'},title:'Cyrene'});
  });
  return {h,sends,answers,subscribers,service};
}
test('assistant surface reuses composer payload and user approval mode without global hooks',async()=>{
  const {h,sends,answers,subscribers}=mount();
  await settle();h.flush();
  const composer=find(h.value,'composer');
  assert.equal(composer.props.draftNamespace,'personal-assistant:');
  const payload={message:'read',attachments:[{id:'file1'}],model:'m'};
  await composer.props.onSend(payload);h.flush();
  assert.equal(sends[0][0],'a');
  assert.equal(sends[0][1].attachments,payload.attachments);
  assert.equal(sends[0][1].model,'m');
  assert.ok(sends[0][1].clientRequestId);
  await find(h.value,'question').props.onAnswer('q','approve','auto');
  assert.equal(sends[1][1].mode,'auto');
  assert.equal(sends[1][1].questionId,'q');
  h.flush();
  assert.equal(find(h.value,'question'),null);
  assert.equal(subscribers.size,1);
  h.unmount();
  assert.equal(subscribers.size,0);
  assert.equal(h.timers.size,0);
});
test('unmount during loading cannot schedule polling or reattach a stream',async()=>{
  const {h,subscribers}=mount();
  h.unmount();await settle();
  assert.equal(h.timers.size,0);
  assert.equal(subscribers.size,0);
});

test('saved reply replaces the live transcript before an older assistant poll can reconnect', async () => {
  const timeline = harness('./runtime-timeline.jsx').run(mod => mod);
  const pageHooks = harness('./runtime-page-hooks.jsx').run(mod => mod);
  const runtimeHarness = harness('./file-resources.jsx', {
    ...timeline, workbenchServices: {events: () => ({subscribe() {}})},
  });
  runtimeHarness.context.performance = performance;
  const engine = runtimeHarness.run(mod => mod.WorkbenchChatRuntimes);
  const streams = new Map(), requests = [], reconnects = [];
  const model = {
    getChat() { return new Promise((resolve, reject) => requests.push({resolve, reject})); },
    sendMessage(id, _input, handlers) { streams.set(id, handlers); return Promise.resolve(); },
    reconnectRun(id) { reconnects.push(id); return Promise.resolve(); },
  };
  const React = {createElement(type, props, ...children) { return {type, props: {...props, children}}; }};
  const h = harness('../shell/assistant-chat.jsx', {
    ...timeline, ...pageHooks, React, useWbcComposerReserveHeight() {},
    workbenchServices: {chat: () => ({Model: model, Runtimes: engine, Transcript: 'transcript', Composer: 'composer'})},
  });
  h.context.setTimeout = h.context.window.setTimeout;
  h.context.clearTimeout = h.context.window.clearTimeout;
  const cache = {details: {a: {id: 'a', status: 'running', messages: []}}};
  h.run((mod, hooks) => {
    Object.assign(React, {useState: hooks.useWbcState, useRef: hooks.useWbcRef, useEffect: hooks.useWbcEffect});
    return mod.AssistantChat({sessionId: 'a', cache});
  });
  const user = {id: 'user', role: 'user', content: 'Question', createdAt: new Date().toISOString()};
  const reply = {id: 'reply', role: 'assistant', content: 'Answer', createdAt: new Date(Date.now() + 1000).toISOString(),
    timelineVersion: 1, timelineOrder: 0, status: 'completed'};
  const saved = {...reply, attachments: [{id: 'file'}]};
  function projected() {
    const {messages, runtime} = find(h.flush(), 'transcript').props;
    return Array.from(timeline.wbcProjectTranscript(messages, runtime));
  }
  function firePoll() {
    for (const [id, callback] of [...h.timers]) { h.timers.delete(id); callback(); }
  }
  function save(id, messages) {
    streams.get(id).onSaved({type: 'saved', assistantMessages: messages, chatSummary: {id}});
  }
  const otherRun = engine.start('b', {message: 'Other session'}, model);
  save('b', [{...saved, id: 'other'}]);
  assert.deepEqual(projected(), []);
  await otherRun;

  const run = engine.start('a', {message: user.content}, model);
  streams.get('a').onAck({runId: 'run', userMessage: user});
  streams.get('a').onTimeline({version: 1, revision: 1, runId: 'run', status: 'completed', messages: [reply]});
  assert.deepEqual(projected().map(message => message.id), ['user', 'reply']);
  save('a', [saved]);
  assert.equal(engine.get('a'), null);
  assert.equal(find(h.flush(), 'composer').props.running, false);
  assert.deepEqual(projected().map(message => message.id), ['user', 'reply']);
  assert.equal(projected()[1].attachments[0].id, 'file');
  assert.equal(cache.details.a.status, 'idle');

  requests[0].resolve({id: 'a', status: 'running', messages: [user]});
  await run;
  await settle();
  assert.deepEqual(reconnects, []);
  assert.deepEqual(projected().map(message => message.id), ['user', 'reply']);
  firePoll();
  requests[1].reject(new Error('offline'));
  await settle();
  assert.deepEqual(projected().map(message => message.id), ['user', 'reply']);
  firePoll();
  requests[2].resolve({id: 'a', status: 'idle', messages: [user, saved]});
  await settle();
  assert.deepEqual(projected().map(message => message.id), ['user', 'reply']);
  h.unmount();
});

test('assistant shows the shared bottom button while reading history and resumes following after a click', async () => {
  const React = {createElement(type, props) { return {type, props}; }};
  const shared = harness('./conversation.jsx', {React, WBC_ICONS: {chevronsRight: 'chevrons'}});
  const {h, subscribers} = mount(shared.run(mod => mod));
  const stage = h.value.props.children.find(child => child?.props?.className === 'wbc-thread-stage');
  const transcript = stage.props.children[0].props;
  const scrolls = [];
  let scrollTop = 0;
  const thread = {
    scrollHeight: 1000, clientHeight: 100,
    get scrollTop() { return scrollTop; },
    set scrollTop(value) { scrollTop = Math.min(value, this.scrollHeight - this.clientHeight); },
    scrollTo(options) { scrolls.push([options.top, options.behavior]); this.scrollTop = options.top; },
  };
  transcript.ref.current = thread;
  const overlay = () => find(h.value, 'composer').props.topOverlay;
  const scroll = top => {
    thread.scrollTop = top;
    transcript.onScroll({currentTarget: thread});
    h.flush();
  };
  const receiveUpdate = height => {
    thread.scrollHeight = height;
    for (const subscriber of subscribers) subscriber({a: {}});
    h.flush();
  };

  await settle(); h.flush();
  assert.equal(thread.scrollTop, 900);
  assert.equal(overlay(), null);
  scroll(890);
  assert.equal(overlay().type, shared.value.WbcScrollToBottomButton);
  scroll(900);
  assert.equal(overlay(), null);
  scroll(890);
  receiveUpdate(1200);
  assert.equal(thread.scrollTop, 890);

  const button = overlay().type(overlay().props);
  assert.equal(button.props['aria-label'], 'Back to latest message');
  button.props.onClick();
  h.flush();
  assert.deepEqual(scrolls, [[1200, 'smooth']]);
  assert.equal(thread.scrollTop, 1100);
  assert.equal(overlay(), null);
  receiveUpdate(1300);
  assert.equal(thread.scrollTop, 1200);
  h.unmount();
});

test('composer reserve follows standalone and workspace composers as attachments grow', () => {
  for (const inWorkspace of [false, true]) {
    const h = harness('./conversation.jsx');
    const styles = new Map();
    const owner = {style: {setProperty: (key, value) => styles.set(key, value), removeProperty: key => styles.delete(key)}};
    let height = 164, resize, disconnected = false;
    const composer = {getBoundingClientRect: () => ({height})};
    const main = {closest: () => inWorkspace ? owner : null, querySelector: () => composer,
      style: inWorkspace ? {setProperty: () => assert.fail('Workspace measurements belong to the page')} : owner.style};
    h.context.ResizeObserver = class {
      constructor(callback) {resize = callback;}
      observe(element) {assert.equal(element, composer);}
      disconnect() {disconnected = true;}
    };
    h.context.requestAnimationFrame = h.context.window.setTimeout;
    h.context.cancelAnimationFrame = h.context.window.clearTimeout;
    h.run(mod => mod.useWbcComposerReserveHeight({current: main}, 'assistant'));
    assert.equal(styles.get('--wbc-composer-reserve-height'), '164px');
    height = 286.5;
    resize();
    for (const [id, callback] of h.timers) {h.timers.delete(id); callback();}
    assert.equal(styles.get('--wbc-composer-reserve-height'), '287px');
    h.unmount();
    assert.equal(styles.size, 0);
    assert.equal(h.listeners.size, 0);
    assert.equal(disconnected, true);
  }
});


test('custom conversation workspace does not replace or clear ordinary runtime hooks', () => {
  const calls = [];
  const h = harness('./runtime-page-hooks.jsx');
  h.run(mod => mod.useWbcRuntimePageHooks({enabled: false, runtimeEngine: {setHooks: value => calls.push(value)}}));
  h.flush();
  h.unmount();
  assert.deepEqual(calls, []);
});


test('split assistant composers reserve space on their own surface', () => {
  const h = harness('./conversation.jsx');
  const styles = new Map();
  const main = {closest: () => ({style: {setProperty: () => assert.fail('Must not change siblings')}}),
    querySelector: () => ({getBoundingClientRect: () => ({height: 240})}),
    style: {setProperty: (k,v) => styles.set(k,v), removeProperty: k => styles.delete(k)}};
  h.run(mod => mod.useWbcComposerReserveHeight({current:main}, 'assistant', true));
  assert.equal(styles.get('--wbc-composer-reserve-height'), '240px');
  h.unmount();
  assert.equal(styles.size, 0);
});

for (const viaComposer of [false, true]) test(`assistant ${viaComposer ? 'composer' : 'card'} answers hide immediately and stream work`, async () => {
  const {h, sends, service} = mount();
  await settle(); h.flush();
  let finish;
  service.Runtimes.start = (...args) => { sends.push(args); return new Promise(resolve => {finish = resolve;}); };
  const card = find(h.value, 'question');
  const composer = find(h.value, 'composer');
  const request = viaComposer
    ? composer.props.onSend({message: '全部要'})
    : card.props.onAnswer('q', '全部要');
  h.flush();
  assert.equal(find(h.value, 'question'), null);
  assert.equal(find(h.value, 'composer').props.running, true);
  assert.equal(sends[0][1].questionId, 'q');
  await card.props.onAnswer('q', 'duplicate');
  assert.equal(sends.length, 1);
  // A stale polling response cannot remount the consumed request.
  for (const [id, callback] of [...h.timers]) { h.timers.delete(id); callback(); }
  await settle(); h.flush();
  assert.equal(find(h.value, 'question'), null);
  finish(); await request; await settle(); h.flush();
  assert.equal(find(h.value, 'question'), null);
  h.unmount();
});

test('assistant restores an unaccepted answer after transport failure', async () => {
  const {h, service} = mount();
  await settle(); h.flush();
  service.Runtimes.start = async () => {throw Error('offline');};
  await find(h.value, 'composer').props.onAnswer('q', 'answer');
  await settle(); h.flush();
  assert.equal(find(h.value, 'question').props.pending.id, 'q');
  h.unmount();
});

function permissionSurface() {
  const React = {createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const sends = [];
  const service = {QuestionPrompt: 'question', Model: {}, Runtimes: {
    start: async (...args) => sends.push(args), getFailure: () => null,
  }};
  const task = {session_id: 'managed_task', title: 'Publish result', pending_question: {
    id: 'permission_1', kind: 'permission', options: [{value: 'approve'}, {value: 'deny'}],
  }};
  const h = harness('../shell/assistant-chat.jsx', {React, workbenchServices: {chat: () => service}});
  h.run((mod, hooks) => {
    Object.assign(React, {useState: hooks.useWbcState, useRef: hooks.useWbcRef, useEffect: hooks.useWbcEffect});
    return mod.AssistantTaskPermission({task});
  });
  return {h, sends, service, task};
}

for (const decision of ['approve', 'deny']) test(`main assistant card sends human ${decision} to the original task`, async () => {
  const {h, sends, task} = permissionSurface();
  const card = find(h.value, 'question');
  assert.equal(card.props.pending, task.pending_question);
  await card.props.onAnswer('wrong-question', decision);
  assert.equal(sends.length, 0);
  await card.props.onAnswer('permission_1', decision, 'default');
  h.flush();
  assert.equal(sends[0][0], task.session_id);
  assert.equal(sends[0][1].questionId, 'permission_1');
  assert.equal(sends[0][1].message, decision);
  assert.equal(sends[0][1].mode, 'default');
  assert.equal(sends[0][1].agentOriginated, undefined);
  assert.equal(find(h.value, 'question'), null);
  await card.props.onAnswer('permission_1', decision);
  assert.equal(sends.length, 1);
  h.unmount();
});

test('task approval errors remain retryable without blocking the assistant', async () => {
  const {h, service, sends} = permissionSurface();
  service.Runtimes.start = async () => {throw Error('offline');};
  await find(h.value, 'question').props.onAnswer('permission_1', 'approve');
  h.flush();
  assert.equal(find(h.value, 'question').props.busy, false);
  service.Runtimes.start = async (...args) => sends.push(args);
  await find(h.value, 'question').props.onAnswer('permission_1', 'deny');
  h.flush();
  assert.equal(sends.length, 1);
  assert.equal(h.value, null);
  h.unmount();
});

test('task approval suppresses double clicks while the child resumes', async () => {
  const {h, service, sends} = permissionSurface();
  let finish;
  service.Runtimes.start = (...args) => {sends.push(args); return new Promise(resolve => {finish = resolve;});};
  const card = find(h.value, 'question');
  const request = card.props.onAnswer('permission_1', 'approve');
  h.flush();
  assert.equal(find(h.value, 'question'), null);
  await card.props.onAnswer('permission_1', 'deny');
  assert.equal(sends.length, 1);
  h.unmount();
  finish(); await request;
});

test('assistant page uses one global scope across project changes and forwards task permission cards', async () => {
  const React = {createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const calls = [], identities = [];
  let active = true;
  const permission = {session_id: 'managed_one', title: 'Task', pending_question: {id: 'q', kind: 'permission'}};
  const snapshot = {assistant_id: 'assistant_global', scope: {id: '__personal_assistant__'},
    sessions: [permission], chats: [{id: 'assistant_global'}]};
  const context = {store: {activeProject: null}, assistant: {setSessionId: id => identities.push(id)},
    navigation: {renderCollapseControl: () => null, renderDockSlot: () => null}};
  const service = {Model: {}, Page: 'page'};
  const h = harness('../shell/assistant-page.jsx', {usePinnedAssistantCards: () => ({cards: [], error: ''}), React, workbenchServices: {chat: () => service},
    WbcRailToolbar: 'rail-toolbar', AssistantChat: 'assistant-chat', assistantFileOwners: () => ({}), wbcChatRailVisualState: () => ({}), wbcFormatTime: () => '', WBC_ICONS: {}});
  h.context.fetch = async (url, options) => {calls.push([url, options.method]); return {ok:true,json:async () => snapshot};};
  h.context.setTimeout = h.context.window.setTimeout;
  h.context.clearTimeout = h.context.window.clearTimeout;
  h.run((mod, hooks) => {
    Object.assign(React, {useState: hooks.useWbcState, useRef: hooks.useWbcRef, useMemo: hooks.useWbcMemo, useEffect: hooks.useWbcEffect});
    return mod.PersonalAssistantPage({context, active});
  });
  await settle(); h.flush();
  assert.equal(h.value.type, 'page');
  assert.equal(h.value.props.project.id, '__personal_assistant__');
  assert.equal(h.value.props.requestedChatId, 'assistant_global');
  const rendered = h.value.props.renderConversation({sessionId: 'assistant_global'});
  assert.equal(rendered.props.permissionRequests[0], permission);
  assert.equal(h.value.props.renderConversation({sessionId: 'managed_one'}).props.permissionRequests.length, 0);
  const count = calls.length;
  await h.value.props.modelOverride.listChats();
  assert.equal(calls.length, count, 'workspace list reuses the loaded assistant snapshot');
  assert.ok(calls.every(([,method]) => method === 'GET'), 'existing assistant does not repeat initialization');
  context.store.activeProject = {id: 'different-project'};
  h.flush(); await settle(); h.flush();
  assert.equal(calls.length, count);
  assert.equal(h.value.props.project.id, '__personal_assistant__');
  assert.ok(calls.every(([url]) => url === '/api/workbench/assistant'));
  assert.deepEqual(identities, ['assistant_global']);
  h.value.props.onActiveChatIdChange('managed_one'); h.flush();
  assert.equal(h.value.props.requestedChatId, 'managed_one');
  h.listeners.get('cyrene:assistant-home')(); h.flush();
  assert.equal(h.value.props.requestedChatId, 'assistant_global');
  assert.equal(h.value.props.requestedChatRevision, 1);
  h.listeners.get('cyrene:assistant-home')(); h.flush();
  assert.equal(h.value.props.requestedChatRevision, 2);
  const rail = h.value.props.railOverride({openResource: () => {}});
  const railText = JSON.stringify(rail);
  assert.equal(railText.includes('助理上下文'), false);
  assert.equal(railText.includes('Cyrene · 个人助理'), false);
  assert.ok(rail.props.className.split(' ').includes('wbc-rail'));
  assert.equal(railText.includes('assistant-task-group'), false);
  assert.equal(railText.includes('assistant_global'), false);
  const toolbar = find(rail, 'rail-toolbar');
  toolbar.props.onQueryChange('not-found'); h.flush();
  assert.equal(JSON.stringify(h.value.props.railOverride({})).includes('aria-label\":\"Task'), false);
  toolbar.props.onAction(); h.flush();
  assert.equal(h.value.props.requestedChatId, 'assistant_global');
  assert.equal(h.value.props.requestedChatRevision, 3);
  assert.equal(find(h.value.props.railOverride({}), 'rail-toolbar').props.query, '');
  active=false; h.flush();
  assert.equal(h.value.type, 'page');
  assert.equal(h.value.props.active, false);
  assert.equal(h.timers.size, 0);
  active=true; h.flush();
  assert.equal(h.value.type, 'page', 'cached workspace stays visible during refresh');
  await settle(); h.flush();
  assert.ok(calls.every(([,method]) => method === 'GET'));
  h.unmount();
});


test('assistant API errors are reported without trying an old initialization endpoint', async () => {
  const React = {createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const calls = [];
  const context = {assistant: {setSessionId: () => {}},
    navigation: {renderCollapseControl: () => null, renderDockSlot: () => null}};
  const h = harness('../shell/assistant-page.jsx', {usePinnedAssistantCards: () => ({cards: [], error: ''}), React, workbenchServices: {chat: () => ({Model: {}})},
    WbcRailToolbar: 'rail-toolbar', assistantFileOwners: () => ({}), WBC_ICONS: {}});
  h.context.fetch = async (url, options) => {
    calls.push([url, options.method]);
    return {ok: false, status: 404, text: async () => '{"detail":"Assistant service unavailable"}'};
  };
  h.context.setTimeout = h.context.window.setTimeout;
  h.context.clearTimeout = h.context.window.clearTimeout;
  h.run((mod, hooks) => {
    Object.assign(React, {useState: hooks.useWbcState, useRef: hooks.useWbcRef, useMemo: hooks.useWbcMemo, useEffect: hooks.useWbcEffect});
    return mod.PersonalAssistantPage({context});
  });
  await settle(); h.flush();
  assert.deepEqual(calls, [['/api/workbench/assistant', 'GET']]);
  assert.ok(JSON.stringify(h.value).includes('Assistant service unavailable'));
  h.unmount();
});

test('assistant transcript paints the workspace cache before the background request completes', async () => {
  const React = {createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const cached={id:'a',messages:[{role:'assistant',content:'already loaded'}]};
  const cache={details:{a:cached}};
  let active=true, finish;
  const service={Composer:'composer',Transcript:'transcript', Model:{getChat:()=>new Promise(resolve=>{finish=resolve;})},
    Runtimes:{get:()=>null,subscribe:()=>()=>{},subscribeSaved:()=>()=>{},getFailure:()=>null}};
  const h=harness('../shell/assistant-chat.jsx',{React,useWbcComposerReserveHeight:()=>{},workbenchServices:{chat:()=>service}});
  h.context.setTimeout=h.context.window.setTimeout;h.context.clearTimeout=h.context.window.clearTimeout;
  h.run((mod,hooks)=>{
    Object.assign(React,{useState:hooks.useWbcState,useRef:hooks.useWbcRef,useEffect:hooks.useWbcEffect});
    return mod.AssistantChat({sessionId:'a',cache,active});
  });
  assert.equal(find(h.value,'transcript').props.messages,cached.messages);
  assert.ok(find(h.value,'composer'));
  finish({...cached,messages:[{role:'assistant',content:'fresh'}]});await settle();h.flush();
  assert.equal(cache.details.a.messages[0].content,'fresh');
  active=false;h.flush();
  assert.equal(h.timers.size,0);
  assert.equal(find(h.value,'transcript').props.messages[0].content,'fresh');
  h.unmount();
});

test('polling disconnect is not a message failure and clears after reconnection', async () => {
  const {h, service} = mount();
  await settle(); h.flush();
  const original = service.Model.getChat;
  service.Model.getChat = async () => { throw new Error('Failed to fetch'); };
  for (const [id, callback] of [...h.timers]) { h.timers.delete(id); callback(); }
  await settle(); h.flush();
  assert.equal(find(h.value, 'composer').props.error, '');
  assert.match(find(h.value, 'p').props.children.join(''), /连接中断/);
  service.Model.getChat = original;
  for (const [id, callback] of [...h.timers]) { h.timers.delete(id); callback(); }
  await settle(); h.flush();
  assert.equal(find(h.value, 'composer').props.error, '');
  assert.doesNotMatch(JSON.stringify(h.value), /连接中断/);
  h.unmount();
});
