'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  browserTypeTargetInPage,
  buildBrowserTypeTargetScript,
} = require('../electron/browser-input');

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    Object.assign(this, init);
  }
}

function installPageGlobals(element) {
  const previous = {
    document: global.document,
    Event: global.Event,
    InputEvent: global.InputEvent,
  };
  global.document = {
    querySelector: () => element,
  };
  global.Event = FakeEvent;
  global.InputEvent = FakeEvent;
  return () => {
    global.document = previous.document;
    global.Event = previous.Event;
    global.InputEvent = previous.InputEvent;
  };
}

function createReactLikeControlledTextarea() {
  class FakeTextarea {
    constructor() {
      this.tagName = 'TEXTAREA';
      this.isContentEditable = false;
      this._nativeValue = '';
      this._state = '';
      this._changeCount = 0;
      this.form = null;
    }

    getAttribute() {
      return null;
    }

    focus() {}

    select() {}

    closest() {
      return null;
    }

    dispatchEvent(event) {
      if (event.type !== 'input') return true;
      const nextValue = this.value;
      if (nextValue !== this._trackedValue) {
        this._trackedValue = nextValue;
        this._state = nextValue;
        this._changeCount += 1;
      }
      setTimeout(() => {
        this.value = this._state;
      }, 0);
      return true;
    }
  }

  Object.defineProperty(FakeTextarea.prototype, 'value', {
    configurable: true,
    get() {
      return this._nativeValue;
    },
    set(value) {
      this._nativeValue = String(value);
    },
  });

  const element = new FakeTextarea();
  const nativeDescriptor = Object.getOwnPropertyDescriptor(FakeTextarea.prototype, 'value');
  element._trackedValue = element.value;
  Object.defineProperty(element, 'value', {
    configurable: true,
    get() {
      return nativeDescriptor.get.call(this);
    },
    set(value) {
      this._trackedValue = String(value);
      nativeDescriptor.set.call(this, value);
    },
  });
  return element;
}

const findTarget = () => ({
  ok: true,
  box: { x: 1, y: 2, w: 3, h: 4 },
});

test('prototype setter updates React-like state and survives the next render', async (t) => {
  const element = createReactLikeControlledTextarea();
  const restore = installPageGlobals(element);
  t.after(restore);

  const result = await browserTypeTargetInPage(
    'selector',
    '#controlled',
    '豆包输入正常',
    'set-native',
    findTarget,
  );

  assert.equal(result.ok, true);
  assert.equal(result.persisted, true);
  assert.equal(result.needsTrustedInput, false);
  assert.equal(element.value, '豆包输入正常');
  assert.equal(element._state, '豆包输入正常');
  assert.equal(element._changeCount, 1);
});

test('setter failures request trusted editor fallback', async (t) => {
  const element = {
    tagName: 'TEXTAREA',
    isContentEditable: false,
    getAttribute: () => null,
    focus() {},
    select() {},
    dispatchEvent() {},
  };
  const restore = installPageGlobals(element);
  t.after(restore);

  const result = await browserTypeTargetInPage(
    'selector',
    '#missing-setter',
    'text',
    'set-native',
    findTarget,
  );

  assert.equal(result.ok, true);
  assert.equal(result.persisted, false);
  assert.equal(result.needsTrustedInput, true);
  assert.match(result.nativeError, /value setter/);
});

test('non-text input types are rejected instead of mutated', async (t) => {
  const element = {
    tagName: 'INPUT',
    isContentEditable: false,
    getAttribute: (name) => (name === 'type' ? 'checkbox' : null),
    focus() {},
  };
  const restore = installPageGlobals(element);
  t.after(restore);

  const result = await browserTypeTargetInPage(
    'selector',
    '#checkbox',
    'text',
    'set-native',
    findTarget,
  );

  assert.equal(result.ok, false);
  assert.match(result.error, /not text-editable/);
});

test('read-only text controls are rejected instead of bypassed', async (t) => {
  const element = createReactLikeControlledTextarea();
  element.readOnly = true;
  const restore = installPageGlobals(element);
  t.after(restore);

  const result = await browserTypeTargetInPage(
    'selector',
    '#readonly',
    'text',
    'set-native',
    findTarget,
  );

  assert.equal(result.ok, false);
  assert.match(result.error, /read-only/);
  assert.equal(element.value, '');
});

test('serialized page expression remains valid with multilingual text', () => {
  const source = buildBrowserTypeTargetScript(
    '(function () { return { ok: false, error: "test" }; })',
    {
      mode: 'selector',
      value: '#composer',
      text: '金丝熊是什么',
      operation: 'verify',
    },
  );

  assert.doesNotThrow(() => new Function(`return ${source}`));
});
