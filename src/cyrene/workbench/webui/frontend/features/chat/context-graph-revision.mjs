// The model-input timeline shares the graph's immutable revision semantics.
export async function updateContextRevision(chatId, block, content) {
  const base = '/api/workbench/chats/' + encodeURIComponent(chatId) + '/context-graph';
  const read = await fetch(base + '/nodes/' + encodeURIComponent(block.nodeId), { cache: 'no-store' });
  const detail = await read.json();
  if (!read.ok) throw new Error(detail.detail || 'Unable to read context');
  if (block.updatedAt && detail.updatedAt !== block.updatedAt) throw new Error('内容已变化，请刷新 / Context changed; refresh before editing');
  const response = await fetch(base + '/branches', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nodeId: block.nodeId, mode: 'edit', content, scope: 'snapshot', expectedRevision: detail.revision, operationId: crypto.randomUUID() }) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Unable to save revision');
  window.dispatchEvent(new CustomEvent('cyrene:context-graph-activate', { detail: { chatId: result.chat.id, chat: result.chat, replay: false } }));
  return result;
}
