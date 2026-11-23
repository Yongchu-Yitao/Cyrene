// Serialized into the sandbox. Only declared controls are hydrated; restoring
// saved state never dispatches input events or manufactures a submission.
export function installCardBridge(fields) {
  const fieldIds = Array.isArray(fields) ? fields : Object.keys(fields);
  const watchers = new Set();
  function redraw() {
    for (const render of watchers) render();
  }
  function onBoundInput(event) {
    if (!watchers.size || !event.isTrusted || window.cyreneCardState?.readonly) return;
    const control = event.target;
    const key = control.getAttribute?.('data-card-field');
    if (!fieldIds.includes(key) || !control.matches('input, select, textarea')) return;
    redraw();
    if (control.validity && !control.validity.valid) return;
    const type = fields[key]?.type;
    let value = control.value;
    if (control.multiple) value = Array.from(control.selectedOptions, option => option.value);
    else if (control.type === 'checkbox') {
      value = type === 'array'
        ? Array.from(document.querySelectorAll('input[type=checkbox][data-card-field]'))
          .filter(input => input.checked && input.getAttribute('data-card-field') === key).map(input => input.value)
        : control.checked;
    } else if (control.type === 'radio' && !control.checked) return;
    else if (type === 'number' || type === 'integer') {
      if (!value.trim() || !Number.isFinite(Number(value))) return;
      value = Number(value);
    }
    send('change', { [key]: value });
  }
  addEventListener('input', onBoundInput);
  // Keyboard events inside the sandbox do not bubble into the host document.
  addEventListener('keydown', event => {
    if (event.isTrusted && event.key === 'Escape') parent.postMessage({ channel: 'cyrene-card-fullscreen-exit' }, '*');
  }, true);
  const send = (kind, values, action_id) => parent.postMessage({ channel: 'cyrene-card', kind, values, action_id }, '*');
  window.cyreneCard = Object.freeze({
    watch(render) {
      watchers.add(render);
      render();
      return () => watchers.delete(render);
    },
    change: values => send('change', values),
    submit: (id, values) => send('submit', values, id),
    confirm: (id, values) => send('confirm', values, id),
  });
  addEventListener('DOMContentLoaded', () => {
    const body = document.body;
    // Preserve the controls and their handlers in previously authored cards.
    for (const icon of body.querySelectorAll('summary span, [aria-expanded] span')) {
      if (!icon.childElementCount && /^[▶▷▸▹▼▽▾▿►▻▴▲]$/u.test(icon.textContent.trim())) {
        icon.setAttribute('data-cyrene-caret', '');
        icon.setAttribute('aria-hidden', 'true');
      }
    }
    let lastHeight = 0;
    function measure() {
      const style = getComputedStyle(body);
      const margin = (parseFloat(style.marginTop) || 0) + (parseFloat(style.marginBottom) || 0);
      const height = Math.ceil(Math.max(body.offsetHeight, body.scrollHeight) + margin);
      if (height !== lastHeight) {
        lastHeight = height;
        parent.postMessage({ channel: 'cyrene-card-size', height }, '*');
      }
    }
    new ResizeObserver(measure).observe(body);
    addEventListener('resize', measure);
    measure();
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
    redraw();
    document.dispatchEvent(new CustomEvent('cyrene-card-state', { bubbles: true, detail: event.data }));
  });
}
