import assert from 'node:assert/strict';
import test from 'node:test';
import { cardReferences, projectInlineCards, initialCardValues, cardEvent, toggleCardSelection, customCardDocument } from './assistant-card-model.mjs';
import { presentConversationMessages } from './message-presentation.mjs';
const cardId = 'card_' + 'a'.repeat(32);
const entry = { status: 'completed', presentation: { kind: 'assistant_card', card_id: cardId, revision: 1, operation: 'card.create' } };
test('completed creation is visible outside trace, in live and reloaded conversation', () => {
  const history = [{ id: 'a', role: 'assistant', activityCard: true, trace: [entry] }, { id: 'b', role: 'assistant', content: '完成' }];
  const result = presentConversationMessages(history);
  assert.equal(result[1].assistantCard.card_id, cardId);
  assert.equal(result[2].content, '完成');
  assert.equal(presentConversationMessages([{ id: 'live', runtimeActivity: { progress: [entry] } }])[1].id, result[1].id);
  assert.equal(history.length, 2);
});
test('failed/pending calls, updates and duplicate results do not create extra cards', () => {
  assert.equal(projectInlineCards([{ trace: [entry, entry] }]).length, 2);
  for (const changed of [{ ...entry, failed: true }, { ...entry, status: 'running' }, { ...entry, presentation: { ...entry.presentation, operation: 'card.update' } }, { ...entry, presentation: { ...entry.presentation, card_id: '../bad' } }]) {
    assert.deepEqual(cardReferences({ trace: [changed] }), []);
  }
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
  const context = { parent, window: {}, document: { querySelectorAll: () => [control] },
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
  const message = { role: 'assistant', trace: [{ status: 'completed', presentation: {
    kind: 'assistant_card', operation: 'card.create', card_id: 'card_' + 'a'.repeat(32), revision: 1, home_kind: 'overview',
  } }] };
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
  const block = {block_id:'pending',type:'action_list'};
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
