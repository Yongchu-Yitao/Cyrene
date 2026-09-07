// Each disclosure can appear in multiple panes. Notify only those instances
// and groups inheriting it, without rereading every saved card in the history.
export function createDisclosureSubscriptions() {
  const listeners = new Map();
  return {
    subscribe(ids, callback) {
      const keys = [...new Set(ids)];
      for (const key of keys) {
        if (!listeners.has(key)) listeners.set(key, new Set());
        listeners.get(key).add(callback);
      }
      return () => {
        for (const key of keys) {
          const entries = listeners.get(key);
          entries?.delete(callback);
          if (!entries?.size) listeners.delete(key);
        }
      };
    },
    notify(id) {
      for (const callback of listeners.get(id) || []) callback();
    },
  };
}

// Own matching in-window and cross-window subscriptions as one lifecycle.
export function subscribeDisclosureUpdates(target, store, ids, sync) {
  const unsubscribe = store.subscribe(ids, sync);
  function storageChanged(event) {
    if (event.key === null || ids.some(key => event.key === "cyrene:disclosure:" + key)) sync();
  }
  target.addEventListener("storage", storageChanged);
  return () => {
    unsubscribe();
    target.removeEventListener("storage", storageChanged);
  };
}
