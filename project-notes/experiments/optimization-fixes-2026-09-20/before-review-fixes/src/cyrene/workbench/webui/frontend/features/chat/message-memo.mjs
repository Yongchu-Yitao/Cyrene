// Snapshot only simple, completed JSON messages. Complex/live records retain
// ordinary rendering; no deep equality or mutable nested-data cache is used.
function snapshotStaticMessage(message) {
  if (!message || typeof message !== 'object') return null;
  const prototype = Object.getPrototypeOf(message);
  if (prototype !== Object.prototype && prototype !== null) return null;
  const snapshot = {};
  for (const key of Reflect.ownKeys(message)) {
    if (typeof key !== 'string') return null;
    const field = Object.getOwnPropertyDescriptor(message, key);
    if (!field || !field.enumerable || !Object.prototype.hasOwnProperty.call(field, 'value')) return null;
    const value = field.value;
    if (value !== null && !['undefined', 'string', 'number', 'boolean'].includes(typeof value)) return null;
    Object.defineProperty(snapshot, key, { value, enumerable: true, writable: true, configurable: true });
  }
  if (snapshot.role !== 'assistant' || snapshot.status === 'running' || snapshot.kind
      || snapshot.activityCard || snapshot.runtimeActivity || snapshot.notificationCard
      || snapshot.modelStatusCard) return null;
  return snapshot;
}
function shallowEqual(left, right) {
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key =>
    Object.prototype.hasOwnProperty.call(right, key) && Object.is(left[key], right[key]));
}
function equalAssistantProps(previous, next) {
  if (!previous.staticMessage || !next.staticMessage || previous.liveRuntime || next.liveRuntime) return false;
  const { msg: oldMessage, ...oldProps } = previous;
  const { msg: newMessage, ...newProps } = next;
  return shallowEqual(oldProps, newProps) && shallowEqual(oldMessage, newMessage);
}
export { snapshotStaticMessage, equalAssistantProps };
