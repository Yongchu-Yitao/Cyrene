function createSingleFlight() {
  let generation = 0;
  let active = null;

  function run(work) {
    if (active && active.generation === generation) return active.promise;

    const currentGeneration = generation;
    const entry = {
      generation: currentGeneration,
      promise: null,
    };
    entry.promise = Promise.resolve().then(() => work({
      isCurrent: () => generation === currentGeneration,
    }));
    active = entry;
    const clear = () => {
      if (active === entry) active = null;
    };
    entry.promise.then(clear, clear);
    return entry.promise;
  }

  function invalidate() {
    generation += 1;
    active = null;
  }

  return { run, invalidate };
}

function isAbortedNavigation(error) {
  return Boolean(
    error
    && (
      Number(error.errno) === -3
      || Number(error.code) === -3
      || String(error.code || '') === 'ERR_ABORTED'
      || /ERR_ABORTED\s*\(-3\)/.test(String(error.message || error))
    )
  );
}

function sameOrigin(left, right) {
  try {
    return new URL(left).origin === new URL(right).origin;
  } catch (_) {
    return false;
  }
}

function waitForReplacementNavigation(win, expectedUrl, originalError, timeoutMs) {
  const contents = win && win.webContents;
  if (!contents || win.isDestroyed() || contents.isDestroyed()) {
    return Promise.reject(originalError);
  }

  const isExpectedAndSettled = () => {
    if (win.isDestroyed() || contents.isDestroyed()) return false;
    return sameOrigin(contents.getURL(), expectedUrl) && !contents.isLoadingMainFrame();
  };
  if (isExpectedAndSettled()) return Promise.resolve();

  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      contents.removeListener('did-finish-load', onFinished);
      contents.removeListener('did-fail-load', onFailed);
      contents.removeListener('destroyed', onDestroyed);
    };
    const finish = (error) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error);
      else resolve();
    };
    const onFinished = () => {
      if (isExpectedAndSettled()) finish();
      else finish(originalError);
    };
    const onFailed = (_event, errorCode, errorDescription, validatedUrl, isMainFrame) => {
      if (isMainFrame === false || Number(errorCode) === -3) return;
      const failure = new Error(
        `Replacement navigation failed (${errorCode}) ${validatedUrl || expectedUrl}: ${errorDescription}`,
        { cause: originalError },
      );
      finish(failure);
    };
    const onDestroyed = () => finish(originalError);
    const timer = setTimeout(() => finish(originalError), timeoutMs);

    contents.on('did-finish-load', onFinished);
    contents.on('did-fail-load', onFailed);
    contents.on('destroyed', onDestroyed);
    if (isExpectedAndSettled()) finish();
  });
}

async function loadWindowUrl(win, url, { timeoutMs = 30000 } = {}) {
  try {
    await win.loadURL(url);
  } catch (error) {
    if (!isAbortedNavigation(error)) throw error;
    await waitForReplacementNavigation(win, url, error, timeoutMs);
  }
}

module.exports = {
  createSingleFlight,
  isAbortedNavigation,
  loadWindowUrl,
};
