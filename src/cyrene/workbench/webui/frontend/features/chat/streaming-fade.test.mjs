import assert from 'node:assert/strict';
import test from 'node:test';
import { wbcClearStreamingFades, wbcFadeInStreamingTail } from './streaming-fade.mjs';
// Minimal text DOM for checking exact wrapping, offsets, and cleanup across
// simulated innerHTML replacements without needing a browser or native deps.
class Node {
  constructor(value = null, tag = '') { this.nodeValue = value; this.tag = tag; this.children = []; this.style = {}; }
  get textContent() { return this.nodeValue === null ? this.children.map(n => n.textContent).join('') : this.nodeValue; }
  get firstChild() { return this.children[0]; }
  get nextSibling() { const a = this.parentNode.children; return a[a.indexOf(this) + 1] || null; }
  insertBefore(node, before) {
    if (node.parentNode) node.parentNode.removeChild(node);
    const i = before ? this.children.indexOf(before) : this.children.length;
    this.children.splice(i, 0, node); node.parentNode = this; return node;
  }
  appendChild(node) { return this.insertBefore(node, null); }
  removeChild(node) { this.children.splice(this.children.indexOf(node), 1); node.parentNode = null; }
  splitText(offset) {
    const tail = new Node(this.nodeValue.slice(offset));
    this.nodeValue = this.nodeValue.slice(0, offset);
    this.parentNode.insertBefore(tail, this.nextSibling); return tail;
  }
  querySelectorAll() { return this.children.flatMap(n => [ ...(n.className === 'wbc-stream-fade' ? [n] : []), ...n.querySelectorAll() ]); }
  normalize() {
    for (let i = this.children.length - 1; i > 0; i--) {
      const a = this.children[i - 1], b = this.children[i];
      if (a.nodeValue !== null && b.nodeValue !== null) { a.nodeValue += b.nodeValue; this.removeChild(b); }
    }
  }
}
function setup() {
  let now = 0, reduced = false;
  const context = {
    performance: { now: () => now }, window: { matchMedia: () => ({ matches: reduced }) },
    document: {
      createElement: tag => new Node(null, tag),
      createTreeWalker(body) {
        const flatten = node => node.nodeValue === null ? node.children.flatMap(flatten) : [node];
        const nodes = flatten(body); let index = nodes.length;
        return { lastChild: () => nodes[--index], previousNode: () => nodes[--index] };
      },
    },
  };
  globalThis.performance = context.performance;
  globalThis.window = context.window;
  globalThis.document = context.document;
  return {
    ...context,
    wbcClearStreamingFades,
    wbcFadeInStreamingTail,
    clock(value) { now = value; },
    reduce() { reduced = true; },
  };
}
function body(...parts) { const root = new Node(); parts.forEach(p => root.appendChild(typeof p === 'string' ? new Node(p) : p)); return root; }

test('new batches resume older fades instead of cutting them off or restarting them', () => {
  const api = setup(), state = { ranges: [] };
  api.wbcFadeInStreamingTail(body('Hello'), 5, state);
  api.clock(48);
  const next = body('Hello world');
  api.wbcFadeInStreamingTail(next, 6, state);
  const spans = next.querySelectorAll().sort((a, b) => Number(b.style.animationDelay.slice(0, -2)) - Number(a.style.animationDelay.slice(0, -2)));
  assert.equal(next.textContent, 'Hello world');
  assert.deepEqual(spans.map(s => [s.textContent, s.style.animationDelay]), [[' world', '0ms'], ['Hello', '-48ms']]);
  api.clock(530);
  const later = body('Hello world!');
  api.wbcFadeInStreamingTail(later, 1, state);
  assert.deepEqual(later.querySelectorAll().map(s => [s.textContent, s.style.animationDelay]), [[' world', '-482ms'], ['!', '0ms']]);
});

test('wrapping and cleanup preserve inline elements, code whitespace and block boundaries', () => {
  const api = setup(), state = { ranges: [] };
  const link = body('link'); link.tag = 'a';
  const code = body(' x\n  y'); code.tag = 'code';
  const root = body('A ', link, '\n', code);
  const before = root.textContent;
  api.wbcFadeInStreamingTail(root, before.length, state);
  assert.equal(root.textContent, before);
  assert.ok(root.querySelectorAll().length > 1);
  api.wbcClearStreamingFades(root);
  assert.equal(root.textContent, before);
  assert.equal(root.querySelectorAll().length, 0);
  assert.equal(root.children[1], link);
  assert.equal(root.children.at(-1), code);
});

test('completed fades, rewritten text and reduced motion leave readable plain text', () => {
  const api = setup(), state = { ranges: [] };
  api.wbcFadeInStreamingTail(body('text'), 4, state);
  api.clock(600);
  const settled = body('text');
  api.wbcFadeInStreamingTail(settled, 0, state);
  assert.equal(settled.querySelectorAll().length, 0);
  api.wbcFadeInStreamingTail(body('shorter'), -2, state);
  assert.equal(state.ranges.length, 0);
  api.reduce();
  const reduced = body('visible');
  api.wbcFadeInStreamingTail(reduced, 7, state);
  assert.equal(reduced.textContent, 'visible');
  assert.equal(reduced.querySelectorAll().length, 0);
});

test('a large batch bounds animation work to the recent tail', () => {
  const api = setup(), root = body('x'.repeat(10000));
  api.wbcFadeInStreamingTail(root, 10000, { ranges: [] });
  assert.equal(root.textContent.length, 10000);
  assert.equal(root.querySelectorAll()[0].textContent.length, 2048);
});
