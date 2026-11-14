import test from 'node:test';
import assert from 'node:assert/strict';
import { assistantDeviceActions, runAssistantDeviceAction, listenAssistantDeviceSelection, deviceMenuPosition } from '../shell/assistant-device-actions.mjs';
import { harness } from './hook-harness.test.mjs';

const peer = { device_id: 'peer/1', task_eligible: true, received_capabilities: ['desktop:input_agent'], received_project_scopes: ['project'], granted_capabilities: ['workspace_file:read'], granted_project_scopes: ['project'] };
function fixture() {
  const calls = [];
  return { calls, options: { remote: { enabled: true, device_name: 'Mac', relay_url: 'https://relay.example' }, sessionId: 'assistant',
    api: async (...args) => { calls.push(['api', ...args]); return { work_device: args[1]?.device_id ? { device_id: args[1].device_id } : null }; }, model: { updateChatPreferences: async (...args) => calls.push(['selection', ...args]) },
    notify: (...args) => calls.push(['event', ...args]) } };
}
test('selecting work devices persists the assistant choice without changing remote context', async () => {
  const f = fixture();
  await runAssistantDeviceAction('select', peer, f.options);
  assert.deepEqual(f.calls, [['api', '/api/workbench/assistant/work-device', { device_id: 'peer/1' }, 'PUT'], ['event', 'cyrene:assistant-work-device-changed', { sessionId: 'assistant', workDevice: { device_id: 'peer/1' } }]]);
  f.calls.length = 0;
  await runAssistantDeviceAction('select', { device_id: 'local' }, f.options);
  assert.deepEqual(f.calls[0], ['api', '/api/workbench/assistant/work-device', { device_id: '' }, 'PUT']);
});
test('revoke clears grants while delete removes pairing via DELETE', async () => {
  for (const action of ['revoke', 'remove']) {
    const f = fixture(); await runAssistantDeviceAction(action, peer, f.options);
    assert.deepEqual(f.calls[0], ['api', '/api/remote/peers/peer%2F1', action === 'revoke' ? { capabilities: [], project_scopes: [] } : undefined, action === 'revoke' ? 'PATCH' : 'DELETE']);
    assert.equal(f.calls[1][1], 'cyrene:remote-devices-changed');
  }
});
test('current device revokes remote access without changing its identity or relay', async () => {
  const f = fixture(); await runAssistantDeviceAction('revoke', { device_id: 'local' }, f.options);
  assert.deepEqual(f.calls[0], ['api', '/api/remote/settings', { enabled: false, device_name: 'Mac', relay_url: 'https://relay.example' }, 'PUT']);
  await assert.rejects(runAssistantDeviceAction('remove', { device_id: 'local' }, f.options));
});
test('unavailable devices cannot be selected and failures do not publish a success event', async () => {
  const f = fixture();
  assert.equal(assistantDeviceActions({ device_id: 'untrusted' }, f.options.remote).select, false);
  await assert.rejects(runAssistantDeviceAction('select', { device_id: 'untrusted' }, f.options));
  assert.equal(f.calls.length, 0);
  f.options.api = async () => { throw Error('network'); };
  await assert.rejects(runAssistantDeviceAction('remove', peer, f.options), /network/);
  assert.equal(f.calls.length, 0);
});
test('selection changes only synchronize the matching conversation and unsubscribe cleanly', () => {
  const target = new EventTarget(), values = [];
  const stop = listenAssistantDeviceSelection('assistant', ids => values.push(ids), target);
  function emit(sessionId) { const e = new Event('cyrene:remote-device-selection'); e.detail = { sessionId, remoteDeviceIds: ['peer'] }; target.dispatchEvent(e); }
  emit('other'); emit('assistant'); stop(); emit('assistant');
  assert.deepEqual(values, [['peer']]);
});
test('device popup flips above the trigger and stays inside the assistant card', () => {
  const bounds = { left: 100, top: 80, right: 376, bottom: 400 };
  const position = deviceMenuPosition({ left: 330, right: 358, top: 352, bottom: 380 }, bounds);
  assert.ok(position.top + 128 < 352);
  assert.ok(position.left >= bounds.left + 8);
  assert.ok(position.left + position.width <= bounds.right - 8);
});
test('API helper preserves DELETE with no request body and defaults reads to GET', async () => {
  const calls = [], h = harness('../shell/assistant-menu-panels.jsx', { workbenchServices: { api: () => ({ json: (...args) => { calls.push(args); return Promise.resolve({}); } }) } });
  h.run(mod => mod.menuApi('/peer', undefined, 'DELETE'));
  assert.equal(calls[0][1].method, 'DELETE'); assert.equal(calls[0][1].body, undefined);
  h.run(mod => mod.menuApi('/settings'));
  assert.equal(calls[1][1].method, undefined);
  h.run(mod => mod.menuApi('/draft', { instruction: 'update' }));
  assert.equal(calls[2][1].method, 'POST'); h.unmount();
});

function find(node, predicate) {
  if (!node) return null;
  if (Array.isArray(node)) return node.map(n => find(n, predicate)).find(Boolean);
  return predicate(node) ? node : find(node.props?.children, predicate);
}
test('three-dot control opens a separate three-action menu with a disabled local delete', () => {
  const React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) };
  const h = harness('../shell/assistant-device-list.jsx', { React, assistantDeviceActions });
  h.context.ReactDOM = { createPortal: node => node };
  const trigger = { focus() {}, contains: () => false };
  h.run((mod, hooks) => {
    Object.assign(React, { useState: hooks.useWbcState, useRef: hooks.useWbcRef, useEffect: hooks.useWbcEffect, useLayoutEffect: hooks.useWbcLayoutEffect });
    return mod.AssistantDeviceList({ remote: { enabled: true, device_name: 'Mac', peers: [] }, sessionId: 'assistant', panelRef: { current: null }, portalRef: { current: {} }, Icon: 'icon' });
  });
  const more = find(h.value, n => n.props?.['aria-haspopup'] === 'menu');
  assert.equal(more.props['aria-label'], 'Mac的更多操作');
  more.props.onClick({ currentTarget: trigger }); h.flush();
  const menu = find(h.value, n => n.props?.role === 'menu');
  const items = menu.props.children.filter(Boolean);
  assert.deepEqual(items.map(n => n.props.children[1].props.children[0]), ['选择工作设备', '撤销访问权限', '删除']);
  assert.equal(items[2].props.disabled, true);
  assert.ok(items[2].props.className.includes('danger')); h.unmount();
});

test('paired devices need verified task eligibility even when they have remote context grants', () => {
  assert.equal(assistantDeviceActions({ ...peer, task_eligible: false }, { enabled: true }).select, false);
  assert.equal(assistantDeviceActions(peer, { enabled: false }).select, false);
});

test('device inventory renders before slow probes, and offline peers stay in the list', async () => {
  const { loadAssistantDevices } = await import('../shell/assistant-device-actions.mjs');
  let state, resolveOffline;
  const offline = new Promise(resolve => { resolveOffline = resolve; });
  const api = async path => {
    if (!path.includes('?')) return { enabled: true, peers: [{ device_id: 'slow' }, { device_id: 'ready' }] };
    if (path.endsWith('slow')) return offline;
    return { peer: { device_id: 'ready', task_state: 'online', task_eligible: true } };
  };
  const pending = loadAssistantDevices(api, value => { state = typeof value === 'function' ? value(state) : value; }, new AbortController().signal);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(state.peers.map(p => p.device_id), ['slow', 'ready']);
  assert.equal(state.peers[1].task_eligible, true);
  state.work_device = { device_id: 'ready' };
  resolveOffline({ peer: { device_id: 'slow', task_state: 'offline', task_eligible: false, task_error: '离线' } });
  await pending;
  assert.equal(state.peers[0].task_state, 'offline');
  assert.equal(state.work_device.device_id, 'ready');
  assert.equal(assistantDeviceActions(state.peers[0], state).select, false);
});

test('offline rows have a red-light state and disabled selection; online rows switch by click', async () => {
  const React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) };
  const selected = [];
  const h = harness('../shell/assistant-device-list.jsx', { React, assistantDeviceActions,
    runAssistantDeviceAction: async (action, device) => selected.push([action, device.device_id]) });
  h.context.ReactDOM = { createPortal: node => node };
  h.run((mod, hooks) => {
    Object.assign(React, { useState: hooks.useWbcState, useRef: hooks.useWbcRef, useEffect: hooks.useWbcEffect, useLayoutEffect: hooks.useWbcLayoutEffect });
    return mod.AssistantDeviceList({ remote: { enabled: true, peers: [
      { device_id: 'offline', display_name: 'Windows', task_state: 'offline', task_eligible: false, task_error: '离线' },
      { device_id: 'online', display_name: 'Ubuntu', task_state: 'online', task_eligible: true }] },
      sessionId: 'assistant', panelRef: { current: null }, portalRef: { current: {} }, Icon: 'icon' });
  });
  const offline = find(h.value, n => n.props?.className === 'aim-device-choice is-offline');
  assert.equal(offline.props.disabled, true);
  assert.equal(find(offline, n => n.props?.role === 'img').props.className, 'aim-device-light is-offline');
  const online = find(h.value, n => n.props?.className === 'aim-device-choice is-online' && find(n, child => child.props?.children?.includes('Ubuntu')));
  assert.equal(online.props.disabled, false);
  await online.props.onClick();
  h.flush();
  assert.deepEqual(selected, [['select', 'online']]);
  assert.equal(find(h.value, n => n.props?.['aria-pressed'] === true && find(n, child => child.props?.children?.includes('Ubuntu'))).props['aria-pressed'], true);
  h.unmount();
});

test('initial local row uses bootstrap device name before the device list request completes', () => {
  const React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) };
  const h = harness('../shell/assistant-device-list.jsx', { React, assistantDeviceActions });
  h.run((mod, hooks) => {
    Object.assign(React, { useState: hooks.useWbcState, useRef: hooks.useWbcRef, useEffect: hooks.useWbcEffect, useLayoutEffect: hooks.useWbcLayoutEffect });
    return mod.AssistantDeviceList({ remote: null, localDeviceName: 'My MacBook', sessionId: 'assistant',
      panelRef: { current: null }, portalRef: { current: null }, Icon: 'icon' });
  });
  const title = find(h.value, n => n.props?.className === 'aim-item-title');
  assert.deepEqual(title.props.children, ['My MacBook']);
  h.unmount();
});
