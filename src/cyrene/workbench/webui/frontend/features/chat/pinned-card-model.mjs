export function conversationCardsUrl(chatId) {
  return chatId && chatId !== 'assistant_global'
    ? '/api/chats/' + encodeURIComponent(chatId) + '/cards'
    : '/api/assistant/cards';
}

export async function loadPinnedCards(request, signal, chatId) {
  const cards = new Map(), cursors = new Set();
  let cursor = null, supportsBulkUnpin = false;
  do {
    const query = new URLSearchParams({ scope: 'pinned', limit: '100' });
    if (cursor) query.set('cursor', cursor);
    const page = await request(conversationCardsUrl(chatId) + '?' + query, { signal });
    if (!cursor) supportsBulkUnpin = page.supports_bulk_unpin === true;
    for (const card of [...(page.cards || []), ...(page.groups || [])]) cards.set(card.card_id, card);
    cursor = page.next_cursor;
    if (cursor && cursors.has(cursor)) throw new Error('固定卡片列表加载失败，请重试。');
    if (cursor) cursors.add(cursor);
  } while (cursor);
  return { cards: [...cards.values()], supportsBulkUnpin };
}

export async function clearPinnedCards(request, chatId, { cards, supportsBulkUnpin }, onCardUnpinned) {
  const url = conversationCardsUrl(chatId);
  if (supportsBulkUnpin) {
    const result = await request(url + '/pins', { method: 'DELETE' });
    result.card_ids.forEach(onCardUnpinned);
    return;
  }
  for (const card of cards) {
    const path = card.group ? '/groups/pin' : '/' + encodeURIComponent(card.card_id) + '/pin';
    const body = card.group ? { pinned: false, group: card.group }
      : { pinned: false, expected_placement_revision: card.placement_revision };
    await request(url + path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!card.group) onCardUnpinned(card.card_id);
  }
}
