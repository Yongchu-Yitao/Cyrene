import test from 'node:test';
import assert from 'node:assert/strict';
import { loadPinnedCards } from './pinned-card-model.mjs';
test('all pinned pages load without imposing a panel count limit', async () => {
  const cards = Array.from({ length: 237 }, (_, i) => ({ card_id: 'card_' + i }));
  const calls = [];
  const signal = new AbortController().signal;
  const result = await loadPinnedCards(async (url, options) => {
    calls.push(url);
    assert.equal(options.signal, signal);
    const query = new URL(url, 'http://localhost').searchParams;
    assert.equal(query.get('scope'), 'pinned');
    const offset = query.get('cursor') ? Number(query.get('cursor').split('_')[1]) + 1 : 0;
    return { cards: cards.slice(offset, offset + 100), next_cursor: offset + 100 < cards.length ? cards[offset + 99].card_id : null };
  }, signal);
  assert.deepEqual(result, cards);
  assert.equal(calls.length, 3);
});
test('empty pinned list removes the column and repeated cursors fail instead of looping', async () => {
  assert.deepEqual(await loadPinnedCards(async () => ({ cards: [] })), []);
  await assert.rejects(loadPinnedCards(async () => ({ cards: [], next_cursor: 'same' })), /加载失败/);
});
