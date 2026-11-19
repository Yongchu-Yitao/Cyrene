import test from 'node:test';
import assert from 'node:assert/strict';
import { clearPinnedCards, loadPinnedCards } from './pinned-card-model.mjs';
test('all pinned pages load without imposing a panel count limit', async () => {
  const cards = Array.from({ length: 237 }, (_, i) => ({ card_id: 'card_' + i }));
  const calls = [];
  const signal = new AbortController().signal;
  const result = await loadPinnedCards(async (url, options) => {
    calls.push(url);
    assert.ok(url.startsWith("/api/chats/project%20chat/cards?"));
    assert.equal(options.signal, signal);
    const query = new URL(url, 'http://localhost').searchParams;
    assert.equal(query.get('scope'), 'pinned');
    const offset = query.get('cursor') ? Number(query.get('cursor').split('_')[1]) + 1 : 0;
    return { cards: cards.slice(offset, offset + 100), next_cursor: offset + 100 < cards.length ? cards[offset + 99].card_id : null };
  }, signal, "project chat");
  assert.deepEqual(result, { cards, supportsBulkUnpin: false });
  assert.equal(calls.length, 3);
});
test('empty pinned list removes the column and repeated cursors fail instead of looping', async () => {
  assert.deepEqual(await loadPinnedCards(async () => ({ cards: [] })), { cards: [], supportsBulkUnpin: false });
  await assert.rejects(loadPinnedCards(async () => ({ cards: [], next_cursor: 'same' })), /加载失败/);
});

test('a running server without bulk unpin uses the ordinary card and group pin endpoints', async () => {
  const cards = [{ card_id: 'a', placement_revision: 3 }];
  const groups = ['book', 'stack'].map(format => ({ card_id: format, group: { format } }));
  const placement = await loadPinnedCards(async () => ({ cards, groups }), undefined, 'work');
  const calls = [], changed = [];
  await clearPinnedCards(async (url, options) => calls.push([url, options.method, JSON.parse(options.body)]), 'work', placement, id => changed.push(id));
  assert.deepEqual(changed, ['a']);
  assert.deepEqual(calls, [
    ['/api/chats/work/cards/a/pin', 'PUT', { pinned: false, expected_placement_revision: 3 }],
    ...groups.map(({ group }) => ['/api/chats/work/cards/groups/pin', 'PUT', { pinned: false, group }]),
  ]);
});
test('servers advertising bulk unpin clear the session in one request', async () => {
  const placement = await loadPinnedCards(async () => ({ supports_bulk_unpin: true }), undefined, 'assistant_global');
  const changed = [];
  await clearPinnedCards(async (url, options) => {
    assert.equal(url, '/api/assistant/cards/pins');
    assert.equal(options.method, 'DELETE');
    return { card_ids: ['a'] };
  }, 'assistant_global', placement, id => changed.push(id));
  assert.deepEqual(changed, ['a']);
});
