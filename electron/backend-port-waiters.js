function createBackendPortWaiters(getCurrentPort) {
  const waiters = new Set();

  function wait(timeoutMs = 30000) {
    const currentPort = getCurrentPort();
    if (currentPort !== null) return Promise.resolve(currentPort);

    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject, timer: null };
      waiter.timer = setTimeout(() => {
        if (!waiters.delete(waiter)) return;
        reject(new Error('Timed out waiting for Python backend to start'));
      }, timeoutMs);
      waiters.add(waiter);
    });
  }

  function resolveAll(port) {
    for (const waiter of waiters) {
      clearTimeout(waiter.timer);
      waiter.resolve(port);
    }
    waiters.clear();
  }

  return { wait, resolveAll };
}

module.exports = { createBackendPortWaiters };
