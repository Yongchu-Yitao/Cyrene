'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const {
  BROWSER_FIND_NESTED_TARGET_SCRIPT,
  browserFrameElementGeometryScript,
} = require('./browser-deep-dom');

class FakeElement {
  constructor(tag, attrs = {}, rect = { left: 0, top: 0, width: 100, height: 40 }) {
    this.tagName = String(tag).toUpperCase();
    this.attrs = { ...attrs };
    this.rect = rect;
    this.children = [];
    this.disabled = false;
    this.innerText = attrs.text || '';
    this.textContent = this.innerText;
    this.shadowRoot = null;
  }
  getAttribute(name) { return this.attrs[name] == null ? null : String(this.attrs[name]); }
  hasAttribute(name) { return this.attrs[name] != null; }
  contains(node) { return node === this || this.children.includes(node); }
  getBoundingClientRect() {
    return {
      ...this.rect,
      right: this.rect.left + this.rect.width,
      bottom: this.rect.top + this.rect.height,
    };
  }
  scrollIntoView() {}
}

test('deep target fallback resolves an element inside an open shadow root', () => {
  const button = new FakeElement('button', { 'data-cyrene-ref': '7', text: 'Continue' });
  const shadowRoot = {
    querySelector: (selector) => selector === '[data-cyrene-ref="7"]' ? button : null,
    querySelectorAll: (selector) => selector === '*' ? [] : [button],
    elementFromPoint: () => button,
  };
  const host = new FakeElement('checkout-widget');
  host.shadowRoot = shadowRoot;
  const document = {
    documentElement: { clientWidth: 800, clientHeight: 600 },
    querySelector: () => null,
    querySelectorAll: (selector) => selector === '*' ? [host] : [],
    elementFromPoint: () => host,
  };
  const context = {
    document,
    window: {
      innerWidth: 800,
      innerHeight: 600,
      getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' }),
    },
    Element: FakeElement,
    Set,
  };
  const result = vm.runInNewContext(
    `${BROWSER_FIND_NESTED_TARGET_SCRIPT}('ref', 'e7', false, true, false)`,
    context,
  );
  assert.equal(result.ok, true);
  assert.equal(result.tag, 'button');
  assert.deepEqual([result.x, result.y], [50, 20]);
});

test('frame geometry finds a cross-origin child by WindowProxy identity', () => {
  const childWindow = {};
  const frameElement = new FakeElement('iframe', {}, { left: 40, top: 60, width: 400, height: 240 });
  frameElement.contentWindow = childWindow;
  frameElement.offsetWidth = 400;
  frameElement.offsetHeight = 240;
  frameElement.clientLeft = 0;
  frameElement.clientTop = 0;
  const document = {
    querySelectorAll: (selector) => selector === '*' ? [frameElement] : [],
  };
  const result = vm.runInNewContext(
    browserFrameElementGeometryScript(0),
    { document, window: { frames: [childWindow] }, Array, Number },
  );
  assert.equal(result.ok, true);
  assert.deepEqual(
    { x: result.x, y: result.y, scaleX: result.scaleX, scaleY: result.scaleY },
    { x: 40, y: 60, scaleX: 1, scaleY: 1 },
  );
});
