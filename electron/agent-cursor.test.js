'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');

const {
  AGENT_CURSOR_FADE_IN_MS,
  AGENT_CURSOR_FADE_OUT_MS,
  AGENT_CURSOR_IDLE_MS,
  AGENT_CURSOR_MOVE_MS,
  AGENT_CURSOR_MAX_VISUAL_SCALE,
  AGENT_CURSOR_PRESS_MS,
  AGENT_CURSOR_SIZE,
  AGENT_CURSOR_SVG,
  agentCursorCommand,
  agentCursorHideCommand,
  agentCursorRunningCommand,
  agentCursorVisualScaleForZoom,
} = require('./agent-cursor');

test('agent cursor contract uses the rounded triangle with fixed interaction timing', () => {
  assert.equal(AGENT_CURSOR_SIZE, 32);
  assert.equal(AGENT_CURSOR_FADE_IN_MS, 150);
  assert.equal(AGENT_CURSOR_MOVE_MS, 180);
  assert.equal(AGENT_CURSOR_IDLE_MS, 3000);
  assert.equal(AGENT_CURSOR_FADE_OUT_MS, 250);
  assert.equal(AGENT_CURSOR_PRESS_MS, 100);
  assert.equal(AGENT_CURSOR_MAX_VISUAL_SCALE, 8);
  assert.match(AGENT_CURSOR_SVG, /stroke-linejoin="round"/);
  assert.doesNotMatch(AGENT_CURSOR_SVG, /<circle|filter=|box-shadow/i);

  const command = agentCursorCommand({ x: 120, y: 80, press: true, moveDurationMs: 420, running: true });
  assert.match(command, /"x":120/);
  assert.match(command, /"y":80/);
  assert.match(command, /"press":true/);
  assert.match(command, /"running":true/);
  assert.match(command, /"moveDurationMs":420/);
  assert.match(command, /"visualScale":1/);
  assert.match(command, /"idleMs":3000/);
  assert.match(command, /"fadeOutMs":250/);
  assert.match(command, /"pressMs":100/);
  assert.match(command, /transform ' \+ config\.moveDurationMs/);
  assert.doesNotMatch(command, /reducedMotion \? 0/);
  const hideCommand = agentCursorHideCommand();
  const runningCommand = agentCursorRunningCommand(true);
  const stoppedCommand = agentCursorRunningCommand(false);
  assert.match(hideCommand, /opacity 250ms ease-in/);
  assert.match(runningCommand, /state\.running = true/);
  assert.doesNotMatch(runningCommand, /clearTimeout|state\.fading = false/);
  assert.doesNotMatch(stoppedCommand, /clearTimeout|3000 - elapsed/);
  assert.doesNotThrow(() => new Function(command));
  assert.doesNotThrow(() => new Function(hideCommand));
  assert.doesNotThrow(() => new Function(runningCommand));
  assert.doesNotThrow(() => new Function(stoppedCommand));
});

test('browser page zoom is counter-scaled without enlarging unzoomed cursors', () => {
  assert.equal(agentCursorVisualScaleForZoom(1), 1);
  assert.equal(agentCursorVisualScaleForZoom(0.25), 4);
  assert.equal(agentCursorVisualScaleForZoom(0.01), 8);
  assert.equal(agentCursorVisualScaleForZoom(2), 1);
  assert.equal(agentCursorVisualScaleForZoom(0), 1);
});

test('shared browser and App Use cursor fades stale positions during a run and animates every update', () => {
  class FakeElement {
    constructor() {
      this.id = '';
      this.style = {};
      this.children = [];
      this.attrs = {};
      this.classList = { add() {}, remove() {} };
      this.offsetWidth = 32;
    }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    appendChild(child) { this.children.push(child); return child; }
    querySelector(selector) {
      return selector === '[data-cyrene-agent-cursor-art]' ? this.children[0] || null : null;
    }
  }
  const body = new FakeElement();
  const head = new FakeElement();
  const findById = (root, id) => {
    if (root.id === id) return root;
    for (const child of root.children) {
      const found = findById(child, id);
      if (found) return found;
    }
    return null;
  };
  const document = {
    body,
    head,
    documentElement: body,
    createElement: () => new FakeElement(),
    getElementById: (id) => findById(body, id) || findById(head, id),
    elementFromPoint: () => body,
    addEventListener() {},
  };
  let now = 1000;
  let nextTimer = 1;
  const timers = new Map();
  const context = {
    document,
    Date: { now: () => now },
    MutationObserver: null,
    getComputedStyle: () => ({ display: 'block', visibility: 'visible' }),
    addEventListener() {},
    requestAnimationFrame: (callback) => { callback(); return 1; },
    setTimeout: (callback, ms) => {
      const id = nextTimer++;
      timers.set(id, { callback, ms: Number(ms) });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
  };
  context.window = context;

  const first = vm.runInNewContext(agentCursorCommand({ x: 120, y: 80, running: true }), context);
  const cursor = document.getElementById('cyrene-agent-cursor');
  assert.ok(cursor);
  assert.equal(first.first, true);
  assert.equal(first.waitMs, 184);
  assert.equal([...timers.values()].some((timer) => timer.ms === 3000), true);

  now = 1200;
  const moved = vm.runInNewContext(agentCursorCommand({ x: 220, y: 180, running: true }), context);
  assert.equal(moved.moved, true);
  assert.equal(moved.waitMs, 180);
  assert.match(cursor.style.transition, /transform 180ms/);
  assert.equal(cursor.style.transform, 'translate3d(214px,174px,0) scale(1)');
  assert.equal([...timers.values()].some((timer) => timer.ms === 3000), true);

  const zoomCompensated = vm.runInNewContext(agentCursorCommand({
    x: 220, y: 180, running: true, visualScale: 4,
  }), context);
  assert.equal(zoomCompensated.moved, true);
  assert.equal(zoomCompensated.waitMs, 180);
  assert.equal(cursor.style.transform, 'translate3d(196px,156px,0) scale(4)');

  const pressed = vm.runInNewContext(agentCursorCommand({
    x: 220, y: 180, running: true, press: true, moveDurationMs: 0, visualScale: 4,
  }), context);
  assert.equal(pressed.moved, false);
  assert.equal(pressed.waitMs, 0);
  assert.equal(pressed.pressMs, 100);

  const timersBeforeLifecycleChange = timers.size;
  vm.runInNewContext(agentCursorRunningCommand(false), context);
  assert.equal(timers.size, timersBeforeLifecycleChange);
});
