// Deterministic event-loop clock: a pause runs timers without inventing moves.
export function resizeClock() {
  let now = 0;
  let id = 0;
  const pending = new Map();
  return {
    setTimeout(fn, delay) { pending.set(++id, { fn, at: now + delay }); return id; },
    clearTimeout(key) { pending.delete(key); },
    advance(ms = 400, blocked = false) {
      now += ms;
      if (blocked) return;
      for (const [key, timer] of pending) if (timer.at <= now) {
        pending.delete(key);
        timer.fn();
      }
    },
    pending: () => pending.size,
  };
}
