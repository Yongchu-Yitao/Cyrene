'use strict';

/**
 * Runs inside the inspected page. Keep this function self-contained: main.js
 * serializes it with Function#toString before passing it to executeJavaScript.
 */
async function browserTypeTargetInPage(
  modeArg,
  valueArg,
  textValue,
  operationArg,
  findTarget,
) {
  const mode = String(modeArg || 'selector');
  const value = String(valueArg || '');
  const desired = String(textValue ?? '');
  const operation = String(operationArg || 'set-native');
  const nonTextInputTypes = new Set([
    'button',
    'checkbox',
    'color',
    'file',
    'hidden',
    'image',
    'radio',
    'range',
    'reset',
    'submit',
  ]);
  const waitForControlledRender = () => new Promise((resolve) => setTimeout(resolve, 50));
  const readValue = (element, editableKind) => (
    editableKind === 'value'
      ? String(element.value ?? '')
      : String(element.textContent ?? '')
  );

  let info;
  try {
    info = findTarget(mode, value, false, true);
  } catch (error) {
    return {
      ok: false,
      error: 'Element lookup failed: ' + String((error && error.message) || error),
    };
  }
  if (!info || !info.ok) {
    return {
      ok: false,
      error: 'Element ' + ((info && info.error) || 'not found'),
    };
  }

  let element = null;
  try {
    if (mode === 'ref') {
      const ref = value.replace(/^e/i, '').replace(/"/g, '\\"');
      element = document.querySelector('[data-cyrene-ref="' + ref + '"]');
    } else {
      element = document.querySelector(value);
    }
  } catch (error) {
    return {
      ok: false,
      error: 'Element lookup failed: ' + String((error && error.message) || error),
    };
  }
  if (!element) return { ok: false, error: 'Element not found' };

  const tag = String(element.tagName || '').toLowerCase();
  const inputType = tag === 'input'
    ? String((element.getAttribute && element.getAttribute('type')) || 'text').toLowerCase()
    : '';
  let editableKind = '';
  if (tag === 'textarea' || (tag === 'input' && !nonTextInputTypes.has(inputType))) {
    editableKind = 'value';
  } else if (element.isContentEditable) {
    editableKind = 'contenteditable';
  }
  if (!editableKind) {
    return { ok: false, error: 'Element is not text-editable' };
  }
  if (element.disabled) {
    return { ok: false, error: 'Element is disabled' };
  }
  if (editableKind === 'value' && element.readOnly) {
    return { ok: false, error: 'Element is read-only' };
  }

  element.focus();
  const base = { ok: true, tag, inputType, editableKind, box: info.box };

  if (operation === 'prepare-trusted') {
    if (editableKind === 'value') {
      try {
        if (typeof element.select === 'function') {
          element.select();
        } else if (typeof element.setSelectionRange === 'function') {
          const length = String(element.value ?? '').length;
          element.setSelectionRange(0, length);
        } else {
          return { ...base, ok: false, error: 'Element text could not be selected' };
        }
      } catch (error) {
        return {
          ...base,
          ok: false,
          error: 'Element text could not be selected: ' + String((error && error.message) || error),
        };
      }
    } else {
      const selection = window.getSelection && window.getSelection();
      if (!selection || typeof document.createRange !== 'function') {
        return { ...base, ok: false, error: 'Editable content could not be selected' };
      }
      const range = document.createRange();
      range.selectNodeContents(element);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    return { ...base, actualValue: readValue(element, editableKind) };
  }

  if (operation === 'verify') {
    await waitForControlledRender();
    if ('isConnected' in element && !element.isConnected) {
      return {
        ...base,
        actualValue: readValue(element, editableKind),
        persisted: false,
        error: 'The editable element was replaced after input',
      };
    }
    const actualValue = readValue(element, editableKind);
    return { ...base, actualValue, persisted: actualValue === desired };
  }

  if (operation === 'submit') {
    const form = element.form || (element.closest && element.closest('form'));
    if (form && typeof form.requestSubmit === 'function') {
      try {
        form.requestSubmit();
        return { ...base, submitted: true, needsTrustedEnter: false };
      } catch (error) {
        return {
          ...base,
          submitted: false,
          needsTrustedEnter: true,
          submitError: String((error && error.message) || error),
        };
      }
    }
    return { ...base, submitted: false, needsTrustedEnter: true };
  }

  if (operation !== 'set-native') {
    return { ...base, ok: false, error: 'Unsupported typing operation' };
  }

  // Rich editors commonly maintain a separate document model. Mutating
  // textContent would desynchronize that model, so route them directly through
  // Chromium's focused-editor command in the main process.
  if (editableKind === 'contenteditable') {
    return {
      ...base,
      actualValue: readValue(element, editableKind),
      persisted: false,
      needsTrustedInput: true,
    };
  }

  try {
    // React installs an own `value` setter on controlled inputs. Calling that
    // setter also updates React's value tracker, causing the following input
    // event to look unchanged. Prefer the native prototype setter when it
    // differs, matching DOM Testing Library's setNativeValue behavior.
    const ownDescriptor = Object.getOwnPropertyDescriptor(element, 'value') || {};
    let prototype = Object.getPrototypeOf(element);
    let prototypeSetter;
    while (prototype && !prototypeSetter) {
      const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
      prototypeSetter = descriptor && descriptor.set;
      prototype = Object.getPrototypeOf(prototype);
    }
    if (prototypeSetter && ownDescriptor.set !== prototypeSetter) {
      prototypeSetter.call(element, desired);
    } else if (ownDescriptor.set) {
      ownDescriptor.set.call(element, desired);
    } else if (prototypeSetter) {
      prototypeSetter.call(element, desired);
    } else {
      return {
        ...base,
        persisted: false,
        needsTrustedInput: true,
        nativeError: 'Element does not have a value setter',
      };
    }

    const eventInit = {
      bubbles: true,
      composed: true,
      inputType: desired ? 'insertText' : 'deleteContentBackward',
      data: desired || null,
    };
    let inputEvent;
    try {
      inputEvent = new InputEvent('input', eventInit);
    } catch (_) {
      inputEvent = new Event('input', { bubbles: true, composed: true });
    }
    element.dispatchEvent(inputEvent);
    // Some non-React form integrations only observe change. Dispatch it after
    // input so React and browser-like listeners see the expected order.
    element.dispatchEvent(new Event('change', { bubbles: true }));
  } catch (error) {
    return {
      ...base,
      persisted: false,
      needsTrustedInput: true,
      nativeError: 'Unable to set element value: ' + String((error && error.message) || error),
    };
  }

  // A synchronous equality check is insufficient: controlled components can
  // restore stale state on their next render. Wait across an event-loop turn
  // before deciding whether the input persisted.
  await waitForControlledRender();
  if ('isConnected' in element && !element.isConnected) {
    return {
      ...base,
      actualValue: readValue(element, editableKind),
      persisted: false,
      needsTrustedInput: true,
      nativeError: 'The editable element was replaced after input',
    };
  }
  const actualValue = readValue(element, editableKind);
  return {
    ...base,
    actualValue,
    persisted: actualValue === desired,
    needsTrustedInput: actualValue !== desired,
  };
}

function buildBrowserTypeTargetScript(
  findTargetScript,
  { mode = 'selector', value = '', text = '', operation = 'set-native' } = {},
) {
  return `(${browserTypeTargetInPage.toString()})(${
    JSON.stringify(String(mode || 'selector'))
  }, ${
    JSON.stringify(String(value || ''))
  }, ${
    JSON.stringify(String(text ?? ''))
  }, ${
    JSON.stringify(String(operation || 'set-native'))
  }, ${String(findTargetScript || '')})`;
}

module.exports = {
  browserTypeTargetInPage,
  buildBrowserTypeTargetScript,
};
