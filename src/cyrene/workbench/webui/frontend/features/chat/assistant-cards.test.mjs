import assert from 'node:assert/strict';
import test from 'node:test';
import { cardReferences, projectInlineCards, initialCardValues, cardEvent, toggleCardSelection, customCardDocument } from './assistant-card-model.mjs';
import { presentConversationMessages } from './message-presentation.mjs';
const cardId = 'card_' + 'a'.repeat(32);
const entry = { status: 'completed', presentation: { kind: 'assistant_card', card_id: cardId, revision: 1, operation: 'card.create' } };
const update = { ...entry, presentation: { ...entry.presentation, operation: 'card.update', revision: 2 } };
test('completed creation is visible outside trace, in live and reloaded conversation', () => {
  const history = [{ id: 'a', role: 'assistant', activityCard: true, trace: [entry] }, { id: 'b', role: 'assistant', content: '完成' }];
  const result = presentConversationMessages(history);
  assert.equal(result[1].assistantCard.card_id, cardId);
  assert.equal(result[2].content, '完成');
  assert.equal(presentConversationMessages([{ id: 'live', runtimeActivity: { progress: [entry] } }])[1].id, result[1].id);
  assert.equal(history.length, 2);
});
test('failed, pending, unrelated and duplicate results do not move or create cards', () => {
  assert.equal(projectInlineCards([{ trace: [entry, entry] }]).length, 2);
  for (const changed of [{ ...update, failed: true }, { ...update, status: 'running' }, { ...update, presentation: { ...update.presentation, operation: 'card.pin' } }, { ...update, presentation: { ...update.presentation, card_id: '../bad' } }]) {
    assert.deepEqual(cardReferences({ trace: [changed] }), []);
  }
});
test('successful updates move the same card to the latest revision in live and saved conversations', () => {
  const otherId = 'card_' + 'b'.repeat(32);
  const other = { ...entry, presentation: { ...entry.presentation, card_id: otherId } };
  const created = { id: 'create', trace: [entry] };
  const initial = [{ ...created, trace: [entry, other] }, { id: 'request', role: 'user', content: '请更新卡片' }];
  const updated = { id: 'update', createdAt: '2026-11-18T02:00:00Z', runId: 'new-run' };
  const replay = { id: 'replay', trace: [entry, update] };
  const originalCard = projectInlineCards([created])[1];
  for (const activity of [{ trace: [update] }, { runtimeActivity: { progress: [update] } }]) {
    const messages = presentConversationMessages([...initial, { ...updated, ...activity }, replay]);
    assert.deepEqual(messages.map(message => message.id), ['create', originalCard.id + ':1', 'inline:' + otherId, 'request', 'update', originalCard.id, 'replay']);
    assert.equal(messages[1].assistantCard.snapshot, true);
    assert.deepEqual(messages[5].assistantCard, { card_id: cardId, revision: 2 });
    assert.equal(messages[5].runId, updated.runId);
    assert.equal(messages[5].createdAt, updated.createdAt);
  }
  const sameActivity = projectInlineCards([{ trace: [entry, other, update] }]);
  assert.deepEqual(sameActivity.slice(1).map(message => message.id), ['inline:' + otherId, originalCard.id]);
  assert.equal(projectInlineCards([{ trace: [update] }])[1].id, originalCard.id);
});

test('the main conversation renders inline cards without text or attachments', async () => {
  const { harness } = await import('./hook-harness.test.mjs');
  const React = await import('react');
  const { context } = harness('./conversation.jsx', {
    React, WbcAssistantMessage: 'assistant-message', wbcIsActivityMessage: () => false,
    wbcTraceDedupeKey: () => '',
  });
  const card = projectInlineCards([{ trace: [update] }])[1];
  const row = context.wbcRenderHistoryMessage(card, { retryClearingIds: new Set(), chat: { id: 'chat' } });
  assert.equal(row.key, card.id);
  assert.equal(row.props.children.type, 'assistant-message');
  assert.equal(row.props.children.props.msg, card);
});
test('display defaults never mutate saved user input and events carry exact revisions', () => {
  const card = { revision: 4, input_revision: 8, spec: { blocks: [{ type: 'form', fields: [{ field_id: 'budget', default: 2400 }] }, { type: 'map', selection: { mode: 'multiple', field_id: 'places' } }] }, input_state: { draft: { budget: 3000 } } };
  assert.deepEqual(initialCardValues(card), { budget: 3000, places: [] });
  assert.deepEqual(card.input_state.draft, { budget: 3000 });
  assert.deepEqual(cardEvent(card, 'submit', { places: ['p1'] }, { action_id: 'choose' }, 'e1'), { event_id: 'e1', base_revision: 4, base_input_revision: 8, kind: 'submit', action_id: 'choose', values: { places: ['p1'] } });
  assert.deepEqual(toggleCardSelection(['p1'], 'p2', 'multiple'), ['p1', 'p2']);
  assert.deepEqual(toggleCardSelection(['p1'], 'p1', 'multiple'), []);
});
test('custom document installs restrictive policy and explicit input bridge before generated HTML', () => {
  const doc = customCardDocument('<script>window.cyreneCard.change({count:2})</script>');
  assert.ok(doc.indexOf('Content-Security-Policy') < doc.indexOf('window.cyreneCard.change'));
  assert.ok(doc.includes("connect-src 'none'"));
  assert.ok(doc.includes("form-action 'none'"));
  assert.match(doc, /event.source\s*!==\s*parent/);
});

test('custom cards allow embedded media without permitting network media sources', () => {
  const doc = customCardDocument('<video controls></video><audio controls></audio>');
  const policy = doc.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
  const directives = Object.fromEntries(policy.split(';').map(value => value.trim().split(/\s+/)).map(([name, ...sources]) => [name, sources]));
  assert.deepEqual(directives['media-src'], ['data:', 'blob:']);
  assert.deepEqual(directives['default-src'], ["'none'"]);
  assert.deepEqual(directives['connect-src'], ["'none'"]);
});

test('conversation grouping keeps generated card outside collapsed tool groups', async () => {
  const { readFileSync } = await import('node:fs');
  const vm = await import('node:vm');
  const { transformSync } = await import('esbuild');
  const React = await import('react');
  const source = readFileSync(new URL('./messages.jsx', import.meta.url), 'utf8');
  const context = { React, module: { exports: {} }, require: () => ({ createDisclosureSubscriptions: () => new Map() }) };
  vm.runInNewContext(transformSync(source, { loader: 'jsx', format: 'cjs' }).code, context);
  const messages = [0,1,2].map(i => ({ id: 'activity-' + i, role: 'assistant', activityCard: true, trace: i === 2 ? [entry] : [{ status: 'completed', text: 'Read' }] }));
  const grouped = context.module.exports.wbcGroupConsecutiveActivityMessages(projectInlineCards(messages), null);
  assert.ok(grouped[0].activityGroup);
  assert.equal(grouped[1].assistantCard.card_id, cardId);
});

test('saved, queued and sent inputs do not claim task completion', async () => {
  const { cardDeliveryNotice } = await import('./assistant-card-model.mjs');
  assert.equal(cardDeliveryNotice(null), '');
  assert.match(cardDeliveryNotice({ status: 'pending' }), /正在发送/);
  assert.equal(cardDeliveryNotice({ status: 'sent' }), '');
  assert.match(cardDeliveryNotice({ status: 'failed' }), /已保存.*重试发送/);
});


test('serialized iframe bridge restores values without overwriting ongoing edits or submitting', async () => {
  const vm = await import('node:vm');
  const control = { value: '10', type: 'number', getAttribute: () => null };
  const parent = { postMessage: () => assert.fail('hydration must not submit') };
  const listeners = {};
  const context = { parent, window: {}, document: { querySelectorAll: () => [control], dispatchEvent: event => assert.equal(event.bubbles, true) },
    addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent: () => {},
    CustomEvent: class { constructor(name, options) { Object.assign(this, options); } } };
  const script = customCardDocument('', ['minutes']).match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(script, context);
  const restore = values => listeners.message({ source: parent, data: { channel: 'cyrene-card-state', values } });
  restore({minutes: 47}); assert.equal(control.value, '47');
  control.value = '58'; restore({minutes: 47}); assert.equal(control.value, '58');
  restore({minutes: 62}); assert.equal(control.value, '62');
  listeners.message({ source: {}, data: { channel: 'cyrene-card-state', values: {minutes: 99} } });
  assert.equal(control.value, '62');
});

test('watched fields redraw on each edit and restore, emitting only typed draft changes', async () => {
  const vm = await import('node:vm');
  const control = { type: 'number', value: '3', getAttribute: () => 'quantity', matches: () => true };
  const sent = [], listeners = {};
  const parent = { postMessage: message => sent.push(JSON.parse(JSON.stringify(message))) };
  const context = { parent, window: {}, document: { querySelectorAll: () => [control], dispatchEvent() {} },
    addEventListener: (name, fn) => { listeners[name] = fn; }, CustomEvent: class {} };
  const script = customCardDocument('', { quantity: { type: 'integer' } }).match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(script, context);
  let output;
  context.window.cyreneCard.watch(() => { output = Number(control.value) * 7; });
  assert.equal(output, 21);
  control.value = '5';
  listeners.input({ target: control, isTrusted: true });
  assert.equal(output, 35);
  assert.deepEqual(sent, [{ channel: 'cyrene-card', kind: 'change', values: { quantity: 5 } }]);
  listeners.message({ source: parent, data: { channel: 'cyrene-card-state', values: { quantity: 8 } } });
  assert.equal(output, 56);
  assert.equal(sent.length, 1);
});

test('card submissions show readable fields while retaining the immutable agent payload', () => {
  const data = { card_id: cardId, kind: 'submit', values: { genre: 'history', minutes: 47 },
    display_values: { genre: { label: '阅读偏好', value: '历史' }, minutes: { label: '每日分钟', value: 47 } } };
  const message = { role: 'user', clientRequestId: 'card_' + 'b'.repeat(64), content:
    '我在卡片「阅读计划」中点击了「提交计划」。请根据这次提交继续处理。\n固定快照\n' + JSON.stringify(data) };
  const [shown] = presentConversationMessages([message]);
  assert.equal(shown.content, '提交计划 · 阅读计划\n阅读偏好：历史\n每日分钟：47');
  assert.ok(message.content.includes('card_id'));
  assert.equal(presentConversationMessages([{...message, clientRequestId: undefined}])[0].content, message.content);
  assert.equal(presentConversationMessages([{...message, content: message.content + 'broken'}])[0].content, message.content + 'broken');
});

test('welcome cards use the live conversation slots without duplicate inline references', () => {
  const message = { role: 'assistant', trace: [entry, update].map(result => ({
    ...result, presentation: { ...result.presentation, home_kind: 'overview' },
  })) };
  assert.deepEqual(cardReferences(message), []);
});

test('automatic welcome requests are hidden without hiding human messages', () => {
  const messages = [{role: 'user', agentOriginated: true, agentMessageKind: 'welcome_refresh', content: 'maintenance'},
    {role: 'user', agentMessageKind: 'welcome_refresh', content: 'real human'}];
  assert.deepEqual(presentConversationMessages(messages), [messages[1]]);
});

test('action lists expand locally and row clicks submit the declared task action', async () => {
  const { readFileSync } = await import('node:fs');
  const vm = await import('node:vm');
  const { transformSync } = await import('esbuild');
  const React = await import('react');
  let expanded;
  const react = { ...React, useState: initial => { if (expanded === undefined) expanded = initial; return [expanded, value => { expanded = value; }]; } };
  const context = { module: { exports: {} }, require: name => name === 'react' ? react : name === './icons.jsx' ? { WBC_ICONS: { chevronDown: React.createElement('svg', { 'data-icon': 'chevron-down' }) } } : {} };
  vm.runInNewContext(transformSync(readFileSync(new URL('./assistant-cards.jsx', import.meta.url), 'utf8'), { loader: 'jsx', format: 'cjs' }).code, context);
  const action = {action_id: 'resume', kind: 'submit', label: '继续进行', fields: [], target_context_id: 'original', task_id: 'task-1'};
  const received = [];
  const element = context.module.exports.CardBlock({block: {type: 'action_list', title: '待你确认', collapsed: true, items: [{item_id: 'one', label: '调研', action_ids: ['resume']}]}, card: {spec: {actions: [action]}}, values: {}, disabled: false, onEvent: (...args) => received.push(args)});
  function nodes(node) { if (!node || typeof node !== 'object') return []; return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)]; }
  let tree = element.type(element.props);
  let buttons = nodes(tree).filter(node => node.type === 'button');
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].props['aria-expanded'], false);
  assert.equal(nodes(tree).filter(node => node.type === 'svg').length, 1);
  buttons[0].props.onClick();
  assert.equal(received.length, 0);
  tree = element.type(element.props);
  buttons = nodes(tree).filter(node => node.type === 'button');
  assert.equal(buttons.length, 2);
  assert.equal(buttons[0].props['aria-expanded'], true);
  buttons[1].props.onClick();
  assert.equal(received[0][0], 'submit');
  assert.equal(received[0][2], action);
  tree = element.type({...element.props, disabled: true});
  assert.equal(nodes(tree).filter(node => node.type === 'button')[1].props.disabled, true);
});

test('home layout displays Agent-authored copy and compact weather without timestamp', async () => {
  const { readFileSync } = await import('node:fs');
  const vm = await import('node:vm');
  const { transformSync } = await import('esbuild');
  const React = await import('react');
  const Weather = () => null;
  const context = { module: { exports: {} }, require: name => name === 'react' ? React : {CardWeather: Weather} };
  vm.runInNewContext(transformSync(readFileSync(new URL('./assistant-home-layout.jsx', import.meta.url), 'utf8'), {loader:'jsx',format:'cjs'}).code, context);
  const weather = {block_id:'weather',type:'weather'};
  const block = {block_id:'pending',type:'action_list',items:[{item_id:'one',label:'确认安排'}]};
  const headline = 'Agent 自己编写的问候\n第二行';
  const tree = context.module.exports.AssistantHomeLayout({card:{spec:{home_kind:'overview',title:headline,subtitle:'Agent 的动态说明',blocks:[weather,block]}},renderBlock:b=>React.createElement('section',{'data-block':b.block_id,key:b.block_id})});
  function nodes(node) { if (!node || typeof node !== 'object') return []; return [node,...React.Children.toArray(node.props?.children).flatMap(nodes)]; }
  const all = nodes(tree);
  assert.equal(all.find(n=>n.type==='h3').props.children,headline);
  assert.equal(all.find(n=>n.type==='p').props.children,'Agent 的动态说明');
  assert.equal(all.find(n=>n.type===Weather).props.showMetadata,false);
  assert.equal(all.filter(n=>n.props?.['data-block']==='weather').length,0);
  assert.equal(all.filter(n=>n.props?.['data-block']==='pending').length,1);
  assert.equal(all.filter(n=>n.type==='button').length,0);
  const title = context.module.exports.homeDisplayTitle;
  assert.equal(title({home_kind:'overview',title:'Cyrene · 今日概览'}),'今日概览');
  assert.equal(title({home_kind:'overview',title:'早上好，从这里继续。'}),'早上好，从这里继续。');
  assert.equal(title({home_kind:'resume',title:'Cyrene 开发进度'}),'Cyrene 开发进度');
});

test('overview only renders controls and panels for lists with items', async () => {
  const { harness } = await import('./hook-harness.test.mjs');
  const React = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { context } = harness('./assistant-home-layout.jsx', {
    createElement: React.createElement, Fragment: React.Fragment,
    HomeToggle: ({ label, count }) => React.createElement('button', null, label + ' ' + count),
    HomePanel: ({ children }) => React.createElement('section', null, children),
  });
  const { AssistantHomeLayout } = context.module.exports;
  for (const pending of [0, 1]) {
    for (const completed of [0, 1]) {
      const blocks = [['pending', pending], ['completed', completed]].map(([id, count]) => ({
        block_id: id, type: 'action_list', title: id, collapsed: true,
        items: count ? [{ item_id: 'one', label: id + ' item' }] : [],
      }));
      const card = { card_id: 'overview', spec: { home_kind: 'overview', title: '下午好', blocks } };
      const html = renderToStaticMarkup(React.createElement(AssistantHomeLayout, {
        card, renderBlock: block => block.title,
      }));
      assert.equal(html.includes('wbc-home-toggles'), Boolean(pending || completed));
      const visibleTitles = blocks.filter(block => block.items.length).map(block => block.title);
      assert.deepEqual(Array.from(html.matchAll(/<button>(.*?)<\/button>/g), m => m[1]), visibleTitles.map(title => title + ' 1'));
      assert.deepEqual(Array.from(html.matchAll(/<section>(.*?)<\/section>/g), m => m[1]), visibleTitles);
    }
  }
});

test('schedule receipts render once outside tool activity with the saved prompt and local time', async () => {
  const React = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { harness } = await import('./hook-harness.test.mjs');
  const { context } = harness('./assistant-cards.jsx', {
    ...React, WBC_ICONS: { calendar: null }, useWorkbenchI18n: () => ({ lang: 'zh', t: key => key }),
  });
  const presentation = { kind: 'schedule_created', task_id: '123', schedule_type: 'once',
    prompt: '交周报', next_run: '2026-11-20T07:00:00Z' };
  const entry = { status: 'completed', presentation };
  const rows = presentConversationMessages([{ runtimeActivity: { progress: [entry] } }, { trace: [entry] }]);
  const receipts = rows.filter(row => row.assistantCard);
  assert.equal(receipts.length, 1);
  const html = renderToStaticMarkup(React.createElement(context.module.exports.WbcInlineCard, {card: receipts[0].assistantCard}));
  assert.match(html, /交周报/);
  assert.match(html, /schedule.receipt.once/);
  assert.ok(html.includes(new Date(presentation.next_run).toLocaleTimeString('zh', {hour:'2-digit', minute:'2-digit', hourCycle:'h23'})));
  assert.doesNotMatch(html, /<button/);
  for (const change of [{failed:true}, {status:'running'}, {presentation:{...presentation, next_run:'invalid'}}]) {
    assert.deepEqual(cardReferences({trace:[{...entry, ...change}]}), []);
  }
});

test('operation cards survive replay and a source click targets its original attachment on the right', async () => {
  const React = await import('react');
  const { harness } = await import('./hook-harness.test.mjs');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { context } = harness('./tool-result-cards.jsx', { ...React, WBC_ICONS: {},
    useWorkbenchI18n: () => ({lang:'zh', t:key => key}) });
  const { ScheduleChange, ScheduleAgenda, KnowledgeSource } = context.module.exports;
  const source = {workspace:'project A', item_id:'paper1', attachment_id:'file2', document_name:'协作手册.md', excerpt:'每周五交周报'};
  const change = {kind:'schedule_change', prompt:'交周报', change:'rescheduled', previous:'2099-01-01T07:00:00Z', next_run:'2099-01-01T08:30:00Z'};
  const agenda = {kind:'schedule_agenda', rows:[{at:change.next_run,title:change.prompt}]};
  const trace = [change, agenda, {kind:'knowledge_sources', sources:[source]}].map((presentation, i) => ({
    presentation, toolCallId:'tool-'+i, status:'completed',
  }));
  const cards = presentConversationMessages([{runtimeActivity:{progress:trace}}, {trace}]).filter(m => m.assistantCard);
  assert.equal(cards.length,3);
  assert.match(renderToStaticMarkup(React.createElement(ScheduleChange,{change:cards[0].assistantCard.change})), /<del>/);
  assert.match(renderToStaticMarkup(React.createElement(ScheduleAgenda,{agenda:cards[1].assistantCard.agenda})), /交周报/);
  let opened;
  const button = KnowledgeSource({source:cards[2].assistantCard.source, onOpenFile:(file,side) => {opened={file,side};}});
  assert.equal(button.type,'button');
  button.props.onClick();
  assert.equal(opened.side,'right');
  assert.equal(opened.file.name,source.document_name);
  assert.equal(opened.file.url,'/api/workbench/library/items/paper1/attachments/file2/raw?workspace=project%20A');
});

test('Markdown places one current card between paragraphs across live activity and replay', () => {
  const marker = '::card[' + cardId + ']';
  const reply = { role: 'assistant', content: '之前\n' + marker + '\n之后\n' + marker };
  for (const activity of [{ trace: [entry, update] }, { runtimeActivity: { progress: [entry, update] } }]) {
    const result = presentConversationMessages([activity, reply, reply], 'assistant_global');
    assert.deepEqual(result[1].markdownParts, [{ text: '之前\n' }, { card_id: cardId, revision: 2 }, { text: '之后\n' }]);
    assert.equal(result.some(m => m.assistantCard), false);
    assert.equal(result[2].markdownParts.some(p => p.card_id), false);
  }
  for (const content of ['```md\n' + marker + '\n```', '> ' + marker, '    ' + marker, '示例 ' + marker]) {
    assert.equal(projectInlineCards([{ role: 'assistant', content }], 'assistant_global')[0].markdownParts, undefined);
  }
  assert.equal(projectInlineCards([{ role: 'user', content: marker }])[0].markdownParts, undefined);
});

test('native boolean fields submit unchecked values and only enforce explicitly true-only inputs', async () => {
  const React = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { harness } = await import('./hook-harness.test.mjs');
  const { context } = harness('./assistant-cards.jsx', { ...React, React });
  const fields = [{ field_id: 'first', type: 'boolean', label: 'First' },
    { field_id: 'second', type: 'boolean', label: 'Second' }];
  const block = { type: 'form', fields };
  const properties = { first: { type: 'boolean' }, second: { type: 'boolean' } };
  const card = { spec: { blocks: [block], input_schema: { properties, required: ['first', 'second'] } },
    input_state: { draft: { first: true } } };
  const values = initialCardValues(card);
  assert.deepEqual(values, { first: true, second: false });
  const render = () => renderToStaticMarkup(React.createElement(context.module.exports.CardBlock, { block, card, values }));
  assert.doesNotMatch(render(), / required=/);
  properties.second.enum = [true];
  assert.match(render(), / required=/);
});

test('each turn retains its version across Markdown, tool fallback and history reload', () => {
  const marker = '::card[' + cardId + ']';
  for (const first of [marker, '创建完成']) for (const second of [marker, '更新完成']) {
    for (const activity of [{ trace: [update] }, { runtimeActivity: { progress: [update] } }]) {
      const rows = presentConversationMessages([
        { trace: [entry] }, { role: 'assistant', content: first },
        { role: 'user', content: '更新清单' },
        activity, { role: 'assistant', content: second }, { trace: [entry, update] },
      ], 'work');
      const split = rows.findIndex(row => row.role === 'user');
      const cards = items => items.flatMap(row => row.assistantCard || row.markdownParts?.filter(p => p.card_id) || []);
      assert.deepEqual(cards(rows.slice(0, split)), [{ card_id: cardId, revision: 1, snapshot: true }]);
      assert.deepEqual(cards(rows.slice(split)), [{ card_id: cardId, revision: 2 }]);
    }
  }
});

test('groups gather ordinary cards across turns, reserving a single placement per revision', () => {
  const second = 'card_'+'b'.repeat(32);
  const group = {title:'旅程',subtitle:'沿海三天',pages:[cardId,second,cardId]};
  const directive = '::card[book] '+JSON.stringify(group);
  const other = {...entry,presentation:{...entry.presentation,card_id:second}};
  const messages = [{trace:[entry,other]}, {role:'assistant',content:'::card['+cardId+']'},
    {role:'user',content:'合订'}, {role:'assistant',content:directive}];
  const projected = presentConversationMessages(messages, 'work');
  assert.equal(projected.some(m=>m.assistantCard),false);
  assert.deepEqual(projected.at(-1).markdownParts[0].pages,[{card_id:cardId,revision:1},{card_id:second,revision:1}]);
  const afterUpdate = presentConversationMessages([...messages,{role:'user',content:'修改'},{trace:[update]}],'work');
  assert.equal(afterUpdate.find(m=>m.content===directive).markdownParts[0].pages[0].snapshot,true);
  assert.equal(afterUpdate.at(-1).assistantCard.revision,2);
  for (const content of ['```md\n'+directive+'\n```', '::card[book] '+JSON.stringify({...group,subtitle:''}),
    '::card[stack] '+JSON.stringify({...group,pages:[cardId,'schedule:1']})]){
    assert.equal(presentConversationMessages([{role:'assistant',content}],'work')[0].markdownParts,undefined);
  }
});


test('sandbox Escape forwards only real keyboard input to the fullscreen host', async () => {
  const vm = await import('node:vm');
  const messages = [], listeners = {};
  const script = customCardDocument('').match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(script, { window: {}, parent: { postMessage: data => messages.push(data) },
    addEventListener: (name, fn) => { listeners[name] = fn; } });
  listeners.keydown({ key: 'Escape', isTrusted: false });
  listeners.keydown({ key: 'Enter', isTrusted: true });
  assert.equal(messages.length, 0);
  listeners.keydown({ key: 'Escape', isTrusted: true });
  assert.equal(messages.length, 1);
  assert.equal(messages[0].channel, 'cyrene-card-fullscreen-exit');
});
