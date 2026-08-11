'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

async function waitFor(predicate, label) {
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${label}`);
}

test('shortcut UI retries only its action patch and preserves concurrent user or Agent changes', async () => {
  const store = new Map();
  const backend = {
    revision: 1,
    bindings: { 'new-chat': ['mod', 'U'] },
  };
  const putPatches = [];
  let injectConcurrentChange = true;

  const fetch = async (_url, options = {}) => {
    if (!options.method || options.method === 'GET') {
      return response(200, {
        revision: backend.revision,
        values: { shortcut_bindings: { ...backend.bindings } },
      });
    }
    const body = JSON.parse(options.body);
    const patch = body.changes.shortcut_bindings;
    putPatches.push(JSON.parse(JSON.stringify(patch)));
    if (injectConcurrentChange) {
      injectConcurrentChange = false;
      backend.bindings.settings = ['mod', 'S'];
      backend.revision += 1;
    }
    if (body.expected_revision !== backend.revision) {
      return response(409, { error: 'revision_conflict', revision: backend.revision });
    }
    for (const [action, keys] of Object.entries(patch)) {
      if (keys === null) delete backend.bindings[action];
      else backend.bindings[action] = keys;
    }
    backend.revision += 1;
    return response(200, { ok: true, revision: backend.revision });
  };

  class FakeEvent { constructor(type) { this.type = type; } }
  const localStorage = {
    getItem: (key) => store.has(key) ? store.get(key) : null,
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  };
  const window = {
    console,
    navigator: { userAgent: 'Mozilla/5.0 (Macintosh)' },
    fetch,
    localStorage,
    Event: FakeEvent,
    dispatchEvent: () => {},
  };
  window.window = window;
  const context = vm.createContext({
    window, console, Event: FakeEvent, localStorage, Promise, JSON, Object,
    Array, String, Math, setTimeout, clearTimeout,
  });
  for (const relative of [
    '../src/webui/frontend/platform/runtime.jsx',
    '../src/webui/frontend/workbench-shortcuts.jsx',
  ]) {
    vm.runInContext(
      fs.readFileSync(path.join(__dirname, relative), 'utf8'),
      context,
      { filename: relative },
    );
  }

  await waitFor(() => {
    const cached = JSON.parse(store.get('cyrene-shortcuts') || '{}');
    return Array.isArray(cached['new-chat']);
  }, 'initial backend shortcut load');

  window.CyreneUI.require('shortcuts').set('search', ['mod', 'J']);
  await waitFor(() => backend.revision === 3 && backend.bindings.search?.[1] === 'J', 'CAS retry');
  await waitFor(() => {
    const cached = JSON.parse(store.get('cyrene-shortcuts') || '{}');
    return cached.settings?.[1] === 'S' && cached.search?.[1] === 'J';
  }, 'authoritative local cache refresh');

  assert.deepEqual(backend.bindings, {
    'new-chat': ['mod', 'U'],
    settings: ['mod', 'S'],
    search: ['mod', 'J'],
  });
  assert.deepEqual(putPatches, [
    { search: ['mod', 'J'] },
    { search: ['mod', 'J'] },
  ]);
});
