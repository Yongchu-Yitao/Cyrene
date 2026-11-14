export function assistantDeviceActions(device, remote) {
  const local = device.device_id === 'local';
  return {
    select: local || (!!remote?.enabled && device.task_eligible === true),
    revoke: local ? !!remote?.enabled : !!(device.granted_capabilities?.length || device.granted_project_scopes?.length),
    remove: !local,
  };
}

export async function runAssistantDeviceAction(action, device, { remote, sessionId, api, model, notify }) {
  const allowed = assistantDeviceActions(device, remote);
  if (!allowed[action]) throw new Error(action === 'select' ? '此设备当前不能执行助理任务。' : '此设备不支持该操作。');
  const local = device.device_id === 'local';
  if (action === 'select') {
    const result = await api('/api/workbench/assistant/work-device', { device_id: local ? '' : device.device_id }, 'PUT');
    notify('cyrene:assistant-work-device-changed', { sessionId, workDevice: result.work_device });
    return result;
  }
  if (action === 'remove') {
    await api('/api/remote/peers/' + encodeURIComponent(device.device_id), undefined, 'DELETE');
  } else if (local) {
    await api('/api/remote/settings', { enabled: false, device_name: remote.device_name || remote.identity?.device_name || '当前设备', relay_url: remote.relay_url || '' }, 'PUT');
  } else {
    await api('/api/remote/peers/' + encodeURIComponent(device.device_id), { capabilities: [], project_scopes: [] }, 'PATCH');
  }
  notify('cyrene:remote-devices-changed', { reason: action === 'remove' ? 'revoked' : 'grant_updated' });
}

export function listenAssistantDeviceSelection(sessionId, apply, target = window) {
  function selected(event) {
    if (event.detail?.sessionId !== sessionId || !Array.isArray(event.detail.remoteDeviceIds)) return;
    apply(event.detail.remoteDeviceIds.slice());
  }
  target.addEventListener('cyrene:remote-device-selection', selected);
  return () => target.removeEventListener('cyrene:remote-device-selection', selected);
}

export function deviceMenuPosition(trigger, bounds, width = 168, height = 128) {
  const availableWidth = Math.max(0, bounds.right - bounds.left - 16);
  const availableHeight = Math.max(0, bounds.bottom - bounds.top - 16);
  width = Math.min(width, availableWidth);
  height = Math.min(height, availableHeight);
  const below = trigger.bottom + 4;
  return {
    left: Math.max(bounds.left + 8, Math.min(trigger.right - width, bounds.right - width - 8)),
    top: Math.max(bounds.top + 8, Math.min(below + height <= bounds.bottom - 8 ? below : trigger.top - height - 4, bounds.bottom - height - 8)),
    width, maxHeight: availableHeight,
  };
}

// Render registered peers immediately; each probe updates just its own row.
export async function loadAssistantDevices(api, apply, signal) {
  const path = '/api/workbench/assistant/work-device';
  const inventory = await api(path, undefined, undefined, signal);
  if (signal.aborted) return;
  apply(inventory);
  if (!inventory.enabled) return inventory;
  await Promise.all((inventory.peers || []).map(async device => {
    let peer;
    try {
      ({ peer } = await api(path + '?device_id=' + encodeURIComponent(device.device_id), undefined, undefined, signal, { timeout: 5000 }));
    } catch (_) {
      peer = { device_id: device.device_id, task_eligible: false, task_state: 'offline', task_error: '离线' };
    }
    if (!signal.aborted) apply(current => ({ ...current,
      peers: (current?.peers || []).map(item => item.device_id === device.device_id ? { ...item, ...peer } : item) }));
  }));
  return inventory;
}
