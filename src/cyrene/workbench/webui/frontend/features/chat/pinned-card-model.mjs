export async function loadPinnedCards(request, signal) {
  const cards = new Map(), cursors = new Set();
  let cursor = null;
  do {
    const query = new URLSearchParams({ scope: 'pinned', limit: '100' });
    if (cursor) query.set('cursor', cursor);
    const page = await request('/api/assistant/cards?' + query, { signal });
    for (const card of page.cards || []) cards.set(card.card_id, card);
    cursor = page.next_cursor;
    if (cursor && cursors.has(cursor)) throw new Error('固定卡片列表加载失败，请重试。');
    if (cursor) cursors.add(cursor);
  } while (cursor);
  return [...cards.values()];
}
