import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('./page.jsx', import.meta.url), 'utf8');
const handler = source.slice(source.indexOf('  function openTaskChat('), source.indexOf('  function openChatSplit('));

for (const side of ['left', 'right']) {
  test(`expanding a task preserves its existing ${side} pane`, () => {
    const card = { id: 'instance-1', kind: 'chat', payload: 'task' };
    const layout = { left: [], right: [] };
    layout[side].push(card);
    const opened = [];
    const context = { activeChatIdRef: { current: 'assistant' }, paneLayoutFor: () => layout,
      openChatSplit: id => opened.push(id) };
    vm.createContext(context);
    vm.runInContext(handler, context);
    context.openTaskChat('task');
    context.openTaskChat('task');
    assert.deepEqual(opened, []);
    assert.equal(layout[side][0], card);
    layout[side] = [];
    context.openTaskChat('task');
    assert.deepEqual(opened, ['task']);
  });
}
