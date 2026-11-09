// Serialized into the sandbox. Only declared controls are hydrated; restoring
// saved state never dispatches input events or manufactures a submission.
export function installCardBridge(fieldIds) {
  const send = (kind, values, action_id) => parent.postMessage({ channel: 'cyrene-card', kind, values, action_id }, '*');
  window.cyreneCard = Object.freeze({
    change: values => send('change', values),
    submit: (id, values) => send('submit', values, id),
    confirm: (id, values) => send('confirm', values, id),
  });
  let lastValues = {};
  function restoreControl(control, value) {
    if (control.type === 'checkbox') control.checked = Array.isArray(value) ? value.includes(control.value) : !!value;
    else if (control.type === 'radio') control.checked = String(value) === control.value;
    else if (control.multiple) for (const option of control.options) option.selected = Array.isArray(value) && value.includes(option.value);
    else control.value = value == null ? '' : String(value);
  }
  function restore(state) {
    const controls = [...document.querySelectorAll('input:not([type=button]):not([type=submit]):not([type=hidden]),select,textarea')];
    for (const control of controls) {
      const names = [control.getAttribute('data-card-field'), control.name, control.id];
      const key = names.find(name => fieldIds.includes(name)) || (fieldIds.length === 1 && controls.length === 1 ? fieldIds[0] : null);
      if (!key || !Object.hasOwn(state.values || {}, key)) continue;
      const value = state.values[key];
      // Repeated host renders must not overwrite in-progress typing.
      if (Object.hasOwn(lastValues, key) && JSON.stringify(lastValues[key]) === JSON.stringify(value)) continue;
      restoreControl(control, value);
    }
    lastValues = { ...(state.values || {}) };
  }
  addEventListener('message', event => {
    if (event.source !== parent || event.data?.channel !== 'cyrene-card-state') return;
    window.cyreneCardState = event.data;
    restore(event.data);
    dispatchEvent(new CustomEvent('cyrene-card-state', { detail: event.data }));
  });
}
