// Snapshot ordinary completed messages, including the flat metadata records
// emitted by runtime_model_message_fields. Complex/live records still render.
const metadataFields = new Set(['usage', 'latestRequestUsage', 'modelIdentity']);
function scalar(value) {
  return value === null || value === undefined || typeof value === 'string'
    || typeof value === 'number' || typeof value === 'boolean';
}
function snapshotRecord(record, allowMetadata) {
  if (!record || typeof record !== 'object') return null;
  const prototype = Object.getPrototypeOf(record);
  if (prototype !== Object.prototype && prototype !== null) return null;
  // Build without inherited setters, then restore the ordinary source prototype.
  const snapshot = Object.create(null);
  for (const key of Reflect.ownKeys(record)) {
    if (typeof key !== 'string') return null;
    const field = Object.getOwnPropertyDescriptor(record, key);
    if (!field || !field.enumerable || !Object.prototype.hasOwnProperty.call(field, 'value')) return null;
    let value = field.value;
    if (!scalar(value)) {
      if (!allowMetadata || !metadataFields.has(key)) return null;
      value = snapshotRecord(value, false);
      if (!value) return null;
    }
    snapshot[key] = value;
  }
  Object.setPrototypeOf(snapshot, prototype);
  return snapshot;
}
function snapshotStaticMessage(message) {
  const snapshot = snapshotRecord(message, true);
  if (!snapshot || snapshot.role !== 'assistant' || snapshot.status === 'running' || snapshot.kind
      || snapshot.activityCard || snapshot.runtimeActivity || snapshot.notificationCard
      || snapshot.modelStatusCard) return null;
  return snapshot;
}
function shallowEqual(left, right) {
  const keys = Object.keys(left);
  return Object.getPrototypeOf(left) === Object.getPrototypeOf(right)
    && keys.length === Object.keys(right).length && keys.every(key =>
      Object.prototype.hasOwnProperty.call(right, key) && Object.is(left[key], right[key]));
}
function equalMessages(left, right) {
  const keys = Object.keys(left);
  return Object.getPrototypeOf(left) === Object.getPrototypeOf(right)
    && keys.length === Object.keys(right).length && keys.every(key => {
      if (!Object.prototype.hasOwnProperty.call(right, key)) return false;
      const a = left[key], b = right[key];
      return metadataFields.has(key) && a && b && typeof a === 'object' && typeof b === 'object'
        ? shallowEqual(a, b) : Object.is(a, b);
    });
}
function equalAssistantProps(previous, next) {
  if (!previous.staticMessage || !next.staticMessage || previous.liveRuntime || next.liveRuntime) return false;
  const { msg: oldMessage, ...oldProps } = previous;
  const { msg: newMessage, ...newProps } = next;
  return shallowEqual(oldProps, newProps) && equalMessages(oldMessage, newMessage);
}
export { snapshotStaticMessage, equalAssistantProps };
