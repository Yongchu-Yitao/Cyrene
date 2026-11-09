export function captureHomePosition(messages, sessionId, now = Date.now()) {
  return {sessionId, afterId: messages.at(-1)?.id || null, at: now};
}
export function homeInsertionIndex(messages, position) {
  if (!position) return messages.length;
  if (!position.afterId) return 0;
  const index = messages.findIndex(message => message.id === position.afterId);
  if (index >= 0) return index + 1;
  // The anchor may have been compacted/deleted. Newer messages still belong below the home.
  const next = messages.findIndex(message => {
    const value = message.createdAt ?? message.created_at;
    const at = typeof value === 'number' ? value : Date.parse(value);
    return Number.isFinite(at) && at > position.at;
  });
  return next >= 0 ? next : 0;
}
