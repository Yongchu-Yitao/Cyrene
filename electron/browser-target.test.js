'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const { BROWSER_FIND_TARGET_SCRIPT } = require('./browser-target');

class FakeElement {
  constructor(tag, text, rect, attrs = {}) {
    this.tagName = String(tag).toUpperCase();
    this.innerText = text;
    this.textContent = text;
    this.rect = rect;
    this.attrs = attrs;
    this.children = [];
    this.disabled = false;
    this.href = attrs.href || '';
  }
  getAttribute(name) { return this.attrs[name] == null ? null : String(this.attrs[name]); }
  hasAttribute(name) { return this.attrs[name] != null; }
  getBoundingClientRect() {
    return {
      ...this.rect,
      right: this.rect.left + this.rect.width,
      bottom: this.rect.top + this.rect.height,
    };
  }
  scrollIntoView(options) { this.scrollOptions = options; }
  contains(node) { return node === this || this.children.includes(node); }
}

function runTextTarget(actionable, fallback, text, hit = null) {
  const context = {
    Element: FakeElement,
    window: {
      innerWidth: 800,
      innerHeight: 600,
      getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' }),
    },
    document: {
      documentElement: { clientWidth: 800, clientHeight: 600 },
      querySelectorAll: (selector) => selector === 'div,span,section,article' ? fallback : actionable,
      querySelector: () => null,
      elementFromPoint: () => hit || actionable[0] || fallback[0] || null,
    },
    Set,
  };
  return vm.runInNewContext(
    `${BROWSER_FIND_TARGET_SCRIPT}('text', ${JSON.stringify(text)}, false, true)`,
    context,
  );
}

test('text target prefers the one actionable element over a broad text container', () => {
  const card = new FakeElement('a', '小鹏Mona L03真实体验', { left: 100, top: 80, width: 240, height: 120 }, {
    href: 'https://www.bilibili.com/video/BV1kbum6cEcH',
  });
  const feed = new FakeElement('section', '广告 小鹏Mona L03真实体验 另一个视频', { left: 0, top: 0, width: 760, height: 1200 });
  feed.children.push(card);
  const result = runTextTarget([card], [feed], '小鹏Mona L03真实体验', card);
  assert.equal(result.ok, true);
  assert.equal(result.target.tag, 'a');
  assert.equal(result.target.href, card.href);
  assert.deepEqual([result.x, result.y], [220, 140]);
  assert.equal(result.hitMatches, true);
  assert.equal(card.scrollOptions.behavior, 'instant');
});

test('text target refuses ambiguous actionable matches', () => {
  const first = new FakeElement('a', '播放视频', { left: 10, top: 10, width: 100, height: 40 });
  const second = new FakeElement('button', '播放视频', { left: 120, top: 10, width: 100, height: 40 });
  const result = runTextTarget([first, second], [], '播放视频', first);
  assert.equal(result.ok, false);
  assert.equal(result.code, 'AMBIGUOUS_TEXT_TARGET');
  assert.equal(result.matchCount, 2);
});

test('target reports when another element covers its click point', () => {
  const target = new FakeElement('a', '目标视频', { left: 100, top: 100, width: 200, height: 100 });
  const overlay = new FakeElement('div', '推荐覆盖层', { left: 100, top: 100, width: 200, height: 100 });
  const result = runTextTarget([target], [], '目标视频', overlay);
  assert.equal(result.ok, true);
  assert.equal(result.hitMatches, false);
  assert.equal(result.blockedBy.text, '推荐覆盖层');
});

test('browser click dispatch waits for movement and press before sending input', () => {
  const source = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8');
  assert.doesNotThrow(() => new vm.Script(source, { filename: 'electron/main.js' }));
  const dispatch = source.slice(source.indexOf('async _dispatchClick'), source.indexOf('async prepareUpload'));
  const moveIndex = dispatch.indexOf('await this._waitForAgentCursor');
  const pressIndex = dispatch.indexOf('await this._pressAgentCursor');
  const finalValidationIndex = dispatch.indexOf('const finalTarget');
  const inputIndex = dispatch.indexOf("wc.sendInputEvent({ type: 'mouseDown'");
  assert.ok(moveIndex >= 0);
  assert.ok(pressIndex > moveIndex);
  assert.ok(finalValidationIndex > pressIndex);
  assert.ok(inputIndex > finalValidationIndex);
  assert.match(dispatch, /TARGET_OBSCURED/);
  assert.match(dispatch, /TARGET_UNSTABLE/);
});
