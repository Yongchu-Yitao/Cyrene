// Research only. Intended first integration: wbcProjectTranscript's final merge.
// A null result means call the unchanged wbcMergeChronologicalMessages.
export function tryMergeOrderedMessages(messages, additions, minimumAdditions = 8) {
  if (!Array.isArray(messages) || !Array.isArray(additions)) return null;
  if (additions.length < minimumAdditions) return null;
  if (minimumAdditions > 0 && additions.length * (messages.length + additions.length) < 4096) return null;
  for (const list of [messages, additions]) {
    if (Object.getPrototypeOf(list) !== Array.prototype) return null;
    for (const key of ['slice', 'forEach', 'constructor', Symbol.iterator]) {
      if (Object.getOwnPropertyDescriptor(list, key)) return null;
    }
  }
  const invalid = Symbol('unsupported field');
  function field(item, name) {
    const descriptor = Object.getOwnPropertyDescriptor(item, name);
    if (!descriptor) return name in item ? invalid : undefined;
    if (!Object.prototype.hasOwnProperty.call(descriptor, 'value')) return invalid;
    return descriptor.value;
  }
  function times(list, isAddition) {
    const result = new Float64Array(list.length);
    for (let i = 0; i < list.length; i++) {
      // Descriptor check rejects holes and accessor indices without invoking them.
      const slot = Object.getOwnPropertyDescriptor(list, String(i));
      if (!slot || !Object.prototype.hasOwnProperty.call(slot, 'value')) return null;
      const item = slot.value;
      if (!item || typeof item !== 'object') return null;
      const proto = Object.getPrototypeOf(item);
      if (proto !== Object.prototype && proto !== null) return null;
      const id = field(item, 'id');
      const at = field(item, 'createdAt');
      const legacyAt = field(item, 'created_at');
      if (id !== undefined && typeof id !== 'string') return null;
      if (at !== undefined && typeof at !== 'string') return null;
      if (legacyAt !== undefined && typeof legacyAt !== 'string') return null;
      if (isAddition) {
        // Keep optimistic/user/question/correlation behavior wholly in legacy.
        if (field(item, 'role') !== 'assistant') return null;
        for (const name of ['clientRequestId', 'answerToQuestionId', 'optimistic']) {
          const value = field(item, name);
          if (value === invalid || (value !== undefined && value !== '' && value !== false && value !== null)) return null;
        }
      }
      const parsed = Date.parse(at || legacyAt || '');
      if (!Number.isFinite(parsed) || (i && parsed < result[i - 1])) return null;
      result[i] = parsed;
    }
    return result;
  }
  // Check the smaller/special-case side before scanning a potentially long history.
  const addTimes = times(additions, true);
  if (!addTimes) return null;
  const baseTimes = times(messages, false);
  if (!baseTimes) return null;
  const known = new Set();
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].id) known.add(messages[i].id);
  }
  const merged = [];
  let cursor = 0;
  for (let j = 0; j < additions.length; j++) {
    const item = additions[j];
    const id = item.id || '';
    if (id && known.has(id)) continue;
    while (cursor < messages.length && baseTimes[cursor] <= addTimes[j]) merged.push(messages[cursor++]);
    merged.push(item);
    if (id) known.add(id);
  }
  while (cursor < messages.length) merged.push(messages[cursor++]);
  return merged;
}
