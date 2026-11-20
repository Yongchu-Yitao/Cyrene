import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveHomeResult } from './home-result.mjs';

const result = { label: '手冲咖啡模拟器游戏文件', sessionId: 'assistant_global',
  action: { intent: '打开原任务中的 coffee-pourover.html。', target_context_id: 'coffee', task_id: 'task' } };

test('saved result opens the original card from its task context', async () => {
  const card = await resolveHomeResult(result, async url => {
    assert.match(url, /scope=current_task&context_id=coffee/);
    return { cards: [{ title: '手冲咖啡模拟器', card_id: 'original' }] };
  }, assert.fail);
  assert.deepEqual(card, { cardId: 'original', owner: 'assistant_global' });
});

test('typed targets preserve the card owner and distinguish same-name files by path', async () => {
  const typed = target => ({ ...result, action: { ...result.action, result_target: target } });
  const card = await resolveHomeResult(typed({ session_id: 'task', card_id: 'game' }), assert.fail, assert.fail);
  assert.deepEqual(card, { owner: 'task', cardId: 'game' });
  const files = ['old', 'new'].map(dir => ({ name: 'report.pdf', path: dir + '/report.pdf' }));
  const opened = await resolveHomeResult(typed({ file_path: 'new/report.pdf' }), assert.fail, async owner => {
    assert.equal(owner, 'task');
    return files;
  });
  assert.equal(opened.file, files[1]);
});

test('legacy file intent opens its file and ambiguous names are reported', async () => {
  const file = { name: 'coffee-pourover.html', path: 'public/coffee-pourover.html' };
  const empty = async () => ({ cards: [] });
  assert.equal((await resolveHomeResult(result, empty, async () => [file])).file, file);
  await assert.rejects(resolveHomeResult(result, empty, async () => [file, { ...file, path: 'old/' + file.name }]), /多个文件/);
});
