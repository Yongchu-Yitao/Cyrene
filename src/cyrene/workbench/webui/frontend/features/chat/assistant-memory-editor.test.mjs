import test from 'node:test';
import assert from 'node:assert/strict';
import { harness } from './hook-harness.test.mjs';

test('memory generation opts out of the generic deadline, renders the response and remains cancellable', async () => {
  const calls = [];
  let finish;
  const React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) };
  const h = harness('../shell/assistant-menu-panels.jsx', {
    React,
    workbenchServices: { api: () => ({ json: (path, options) => {
      calls.push({ path, options });
      return new Promise(resolve => { finish = resolve; });
    } }) },
  });
  h.run((mod, hooks) => {
    Object.assign(React, { useState: hooks.useWbcState, useRef: hooks.useWbcRef,
      useEffect: hooks.useWbcEffect, useLayoutEffect: hooks.useWbcLayoutEffect });
    return mod.AssistantMemory({ projectId: '__personal_assistant__' });
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/api/projects/__personal_assistant__/memory-editor');
  assert.equal(calls[0].options.timeout, 0);
  assert.equal(calls[0].options.signal.aborted, false);
  assert.match(JSON.stringify(h.value), /正在整理记忆/);
  finish({ summary: '真实摘要已返回', changes: [], version: 'v1' });
  await new Promise(resolve => setImmediate(resolve));
  h.flush();
  assert.match(JSON.stringify(h.value), /真实摘要已返回/);
  assert.doesNotMatch(JSON.stringify(h.value), /正在整理记忆/);
  h.unmount();
  assert.equal(calls[0].options.signal.aborted, true);
});
