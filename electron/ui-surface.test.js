'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadSurface(search = '') {
  const window = {
    console,
    crypto: { randomUUID: () => '11111111-2222-4333-8444-555555555555' },
    location: { protocol: 'http:', host: '127.0.0.1:8765', search },
  };
  window.window = window;
  const context = vm.createContext({
    window,
    console,
    Map,
    Math,
    Number,
    Promise,
    String,
    URLSearchParams,
  });
  for (const relative of [
    '../src/webui/frontend/platform/runtime.jsx',
    '../src/webui/frontend/platform/ui-surface.jsx',
  ]) {
    vm.runInContext(
      fs.readFileSync(path.join(__dirname, relative), 'utf8'),
      context,
      { filename: relative },
    );
  }
  return window.CyreneUI.require('uiSurface');
}

function loadSurfaceWithDocument() {
  class FakeElement {
    constructor(tagName, attrs = {}) {
      this.tagName = String(tagName).toUpperCase();
      this.nodeType = 1;
      this.attrs = { ...attrs };
      this.id = attrs.id || '';
      this.innerText = attrs.text || '';
      this.hidden = false;
      this.disabled = false;
      this.readOnly = false;
      this.checked = false;
      this.scrollTop = 0;
      this.scrollHeight = attrs.scrollHeight || 20;
      this.clientHeight = attrs.clientHeight || 20;
      this.scrollWidth = 20;
      this.clientWidth = 20;
      this.events = [];
      this.clicks = 0;
      this.children = [];
      this.style = {};
      this.offsetWidth = Number(this.rect && this.rect.width || 0);
      this.parentElement = null;
      this.rect = attrs.rect || { left: 10, top: 20, width: 100, height: 30 };
      const classes = new Set(String(attrs.class || '').split(/\s+/).filter(Boolean));
      this.classList = {
        contains: (name) => classes.has(name),
        add: (name) => classes.add(name),
        remove: (name) => classes.delete(name),
      };
    }
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? String(this.attrs[name]) : null; }
    setAttribute(name, value) { this.attrs[name] = String(value); if (name === 'id') this.id = String(value); }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    querySelector(selector) {
      if (selector === '[data-cyrene-agent-cursor-art]') {
        return this.children.find((child) => child.getAttribute('data-cyrene-agent-cursor-art') === 'true') || null;
      }
      return null;
    }
    getBoundingClientRect() {
      return {
        ...this.rect,
        right: this.rect.right == null ? this.rect.left + this.rect.width : this.rect.right,
        bottom: this.rect.bottom == null ? this.rect.top + this.rect.height : this.rect.bottom,
      };
    }
    querySelectorAll() { return []; } getAnimations() { return []; }
    closest(selector) {
      let current = this;
      while (current) {
        if (selector === '[data-cyrene-node-id]' && current.getAttribute('data-cyrene-node-id')) return current;
        if (selector === '[data-cyrene-revision-volatile="true"]' && current.getAttribute('data-cyrene-revision-volatile') === 'true') return current;
        if (selector.includes('aria-hidden') && (current.getAttribute('aria-hidden') === 'true' || current.inert)) return current;
        current = current.parentElement;
      }
      return null;
    }
    click() { this.clicks += 1; }
    dispatchEvent(event) { this.events.push(event.type); return true; }
    scrollBy(options) { this.scrollTop += Number(options.top || 0); }
  }
  class FakeInput extends FakeElement {
    constructor(attrs) { super('input', attrs); this._value = attrs.value || ''; }
  }
  Object.defineProperty(FakeInput.prototype, 'value', {
    get() { return this._value; },
    set(value) { this._value = String(value); },
  });
  class FakeTextArea extends FakeInput {}
  class FakeSelect extends FakeInput {
    constructor(attrs, options) {
      super(attrs);
      this.tagName = 'SELECT';
      this.options = options;
    }
  }
  const button = new FakeElement('button', { text: 'Run action' });
  const input = new FakeInput({ 'aria-label': 'Name', value: '' });
  input.maxLength = 60;
  const secret = new FakeInput({ 'aria-label': 'Secret', type: 'password', value: 'never-expose' });
  const select = new FakeSelect(
    { 'aria-label': 'Theme', value: 'system' },
    [{ value: 'system', label: 'System' }, { value: 'dark', label: 'Dark' }],
  );
  const menuTarget = new FakeElement('div', { text: 'Conversation', 'data-cyrene-context-menu': 'true' });
  const scroller = new FakeElement('div', { text: 'Scrollable list', scrollHeight: 1000, clientHeight: 200 });
  let elements = [button, input, secret, select, menuTarget, scroller];
  const container = new FakeElement('main');
  container.querySelectorAll = (selector) => selector === '*' ? elements : elements;
  const head = new FakeElement('head');
  let surfaces = [];
  let settingsPanel = null;
  const findById = (element, id) => {
    if (!element) return null;
    if (element.id === id) return element;
    for (const child of element.children || []) {
      const found = findById(child, id);
      if (found) return found;
    }
    return null;
  };
  const document = {
    body: container,
    head,
    documentElement: container,
    createElement: (tagName) => new FakeElement(tagName),
    getElementById: (id) => id === 'root' ? container : findById(container, id) || findById(head, id),
    querySelector: (selector) => selector === '.settings-overlay-panel' ? settingsPanel : null,
    querySelectorAll: () => surfaces,
  };
  class FakeEvent { constructor(type) { this.type = type; } }
  class FakeMouseEvent extends FakeEvent {}
  const cursorTimers = [];
  const window = {
    console,
    crypto: { randomUUID: () => 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee' },
    location: { protocol: 'http:', host: '127.0.0.1:8765', search: '' },
    document,
    Event: FakeEvent,
    MouseEvent: FakeMouseEvent,
    HTMLInputElement: FakeInput,
    HTMLTextAreaElement: FakeTextArea,
    HTMLSelectElement: FakeSelect,
    getComputedStyle: () => ({ display: 'block', visibility: 'visible' }),
    requestAnimationFrame: (callback) => { callback(); return 1; },
    setTimeout: (callback, ms) => {
      cursorTimers.push(Number(ms));
      if (Number(ms) < 1000) callback();
      return cursorTimers.length;
    },
    clearTimeout: () => {},
  };
  window.window = window;
  const context = vm.createContext({
    window, console, Map, Math, Number, Promise, String, URLSearchParams, WeakMap,
  });
  for (const relative of [
    '../src/webui/frontend/platform/runtime.jsx',
    '../src/webui/frontend/platform/ui-surface.jsx',
  ]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, relative), 'utf8'), context, { filename: relative });
  }
  return {
    surface: window.CyreneUI.require('uiSurface'), button, input, secret, select, menuTarget, scroller,
    container, document, cursorTimers, FakeElement,
    setSurfaces: (next) => { surfaces = next; },
    setElements: (next) => { elements = next; },
    setSettingsPanel: (next) => { settingsPanel = next; },
  };
}

function findTreeNode(root, predicate) {
  if (!root) return undefined;
  if (predicate(root)) return root;
  for (const child of root.children || []) {
    const found = findTreeNode(child, predicate);
    if (found) return found;
  }
  return undefined;
}

test('semantic surface exposes only registered current-scope actions with revision checks', async () => {
  const surface = loadSurface('?surface=quick-chat');
  let value = '';
  surface.register({
    node_id: 'composer',
    parent_id: 'root',
    scope: 'main',
    get_node: () => ({ role: 'textbox', name: 'Message', value_summary: value }),
    actions: [{
      action_id: 'set_value',
      kind: 'set_value',
      input_schema: { value: 'text<=20' },
    }],
    handlers: { set_value: (input) => { value = input.value; } },
  });
  surface.register({
    node_id: 'hidden',
    parent_id: 'root',
    scope: 'dialog',
    get_node: () => ({ role: 'button', name: 'Hidden' }),
    actions: [{ action_id: 'invoke', kind: 'invoke' }],
    handlers: { invoke: () => {} },
  });

  const first = surface.snapshot({ max_depth: 12 });
  assert.equal(first.surface.kind, 'quick_chat');
  assert.equal(first.root.children.map((node) => node.node_id).join(','), 'composer');

  const acted = await surface.act({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    node_id: 'composer',
    action_id: 'set_value',
    input: { value: 'hello' },
  });
  assert.equal(acted.ok, true);
  assert.equal(acted.revision, first.revision);
  assert.equal(surface.snapshot({}).root.children[0].value_summary, 'hello');

  const second = await surface.act({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    node_id: 'composer',
    action_id: 'set_value',
    input: { value: 'again' },
  });
  assert.equal(second.ok, true);

  surface.register({
    node_id: 'new_action',
    parent_id: 'root',
    scope: 'main',
    get_node: () => ({ role: 'button', name: 'New action' }),
    actions: [{ action_id: 'invoke', kind: 'invoke' }],
    handlers: { invoke: () => {} },
  });
  const compatibleRead = surface.snapshot({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    parent_node_id: 'composer',
    action_id: 'set_value',
    allow_compatible_action: true,
  });
  assert.equal(compatibleRead.ok, true);
  assert.equal(compatibleRead.requested_revision_compatible, true);
  assert.notEqual(compatibleRead.revision, first.revision);
  const compatible = await surface.act({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    node_id: 'composer',
    action_id: 'set_value',
    input: { value: 'compatible' },
  });
  assert.equal(compatible.ok, true);
  assert.equal(value, 'compatible');

  surface.register({
    node_id: 'composer',
    parent_id: 'root',
    scope: 'main',
    get_node: () => ({ role: 'textbox', name: 'Message', value_summary: value, state: { session_id: 'different-chat' } }),
    actions: [{
      action_id: 'set_value',
      kind: 'set_value',
      input_schema: { value: 'text<=20' },
    }],
    handlers: { set_value: (input) => { value = input.value; } },
  });
  const stale = await surface.act({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    node_id: 'composer',
    action_id: 'set_value',
    input: { value: 'stale' },
  });
  assert.equal(stale.error, 'stale_snapshot');

  const current = surface.snapshot({});
  const forbidden = await surface.act({
    snapshot_id: current.snapshot_id,
    revision: current.revision,
    node_id: 'composer',
    action_id: 'set_value',
    input: { value: 'safe', selector: '#composer' },
  });
  assert.equal(forbidden.error, 'action_failed');
  assert.equal(value, 'compatible');
});

test('snapshot exposes the session owning the current visible interface', () => {
  const surface = loadSurface();
  surface.register({
    node_id: 'chat_composer_input', parent_id: 'root', scope: 'main', order: 1,
    get_node: () => ({
      role: 'textbox', name: 'Message',
      state: { session_id: 'visible-chat', session_kind: 'chat' },
    }),
    actions: [{ action_id: 'set_value', kind: 'set_value' }],
    handlers: { set_value: () => {} },
  });

  const tree = surface.snapshot({ max_depth: 12 });
  assert.equal(tree.surface.visible_session_id, 'visible-chat');
  assert.equal(tree.surface.visible_session_kind, 'chat');
  assert.equal(tree.root.state.visible_session_id, 'visible-chat');
});

test('current accessibility projection supports visible press, value, select, context menu, and list scroll', async () => {
  const { surface, button, input, select, menuTarget, scroller, document, cursorTimers } = loadSurfaceWithDocument();
  let tree = surface.snapshot({ max_depth: 12 });
  const find = (name) => findTreeNode(tree.root, (node) => node.name === name);
  assert.equal(find('Secret'), undefined);
  assert.equal(tree.root.children.every((node) => node.actions.length === 0), true);

  let node = find('Run action');
  let result = await surface.act({ snapshot_id: tree.snapshot_id, revision: tree.revision, node_id: node.node_id, action_id: 'invoke', input: {} });
  assert.equal(result.ok, true);
  assert.equal(button.clicks, 1);
  const highlight = document.getElementById('cyrene-ui-agent-control-highlight');
  assert.ok(highlight);
  assert.equal(highlight.getAttribute('data-node-id'), node.node_id);
  assert.equal(highlight.getAttribute('data-action-id'), 'invoke');
  assert.equal(highlight.classList.contains('is-active'), true);
  assert.ok(cursorTimers.includes(3600));

  tree = surface.snapshot({ max_depth: 12 });
  node = findTreeNode(tree.root, (item) => item.name === 'Name');
  result = await surface.act({ snapshot_id: tree.snapshot_id, revision: tree.revision, node_id: node.node_id, action_id: 'set_value', input: { value: 'Cyrene' } });
  assert.equal(result.ok, true);
  assert.equal(input.value, 'Cyrene');
  assert.equal(highlight.getAttribute('data-node-id'), node.node_id);
  assert.equal(highlight.getAttribute('data-action-id'), 'set_value');
  assert.deepEqual(input.events, ['input', 'change']);
  assert.equal(node.state.max_length, 60);

  tree = surface.snapshot({ max_depth: 12 });
  node = findTreeNode(tree.root, (item) => item.name === 'Theme');
  assert.deepEqual(
    JSON.parse(JSON.stringify(node.state.options)),
    [{ value: 'system', label: 'System', disabled: false }, { value: 'dark', label: 'Dark', disabled: false }],
  );
  result = await surface.act({ snapshot_id: tree.snapshot_id, revision: tree.revision, node_id: node.node_id, action_id: 'select', input: { value: 'dark' } });
  assert.equal(result.ok, true);
  assert.equal(select.value, 'dark');
  assert.equal(highlight.getAttribute('data-action-id'), 'select');

  tree = surface.snapshot({ max_depth: 12 });
  node = findTreeNode(tree.root, (item) => item.name === 'Conversation');
  result = await surface.act({ snapshot_id: tree.snapshot_id, revision: tree.revision, node_id: node.node_id, action_id: 'open_menu', input: {} });
  assert.equal(result.ok, true);
  assert.deepEqual(menuTarget.events, ['contextmenu']);
  assert.equal(highlight.getAttribute('data-action-id'), 'open_menu');

  tree = surface.snapshot({ max_depth: 12 });
  node = findTreeNode(tree.root, (item) => item.name === 'Scrollable list');
  result = await surface.act({ snapshot_id: tree.snapshot_id, revision: tree.revision, node_id: node.node_id, action_id: 'scroll_page', input: { delta: 300 } });
  assert.equal(result.ok, true);
  assert.equal(scroller.scrollTop, 300);
  assert.equal(highlight.getAttribute('data-action-id'), 'scroll_page');

  surface.register({
    node_id: 'semantic_input', parent_id: 'root', scope: 'main', order: 1,
    get_element: () => input,
    get_highlight_element: () => scroller,
    get_node: () => ({ role: 'textbox', name: 'Semantic input' }),
    actions: [{ action_id: 'set_value', kind: 'set_value', input_schema: { value: 'text<=60' } }],
    handlers: { set_value: (value) => { input.value = value.value; } },
  });
  tree = surface.snapshot({ max_depth: 12 });
  node = findTreeNode(tree.root, (item) => item.node_id === 'semantic_input');
  result = await surface.act({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    node_id: node.node_id,
    action_id: 'set_value',
    input: { value: 'wrapper highlight' },
  });
  assert.equal(result.ok, true);
  assert.equal(highlight.getAttribute('data-node-id'), 'semantic_input');
  assert.equal(highlight.style.width, '106px');
  assert.equal(highlight.style.height, '36px');
});

test('model settings projection exposes controls and keeps agent-entered credentials write-only', async () => {
  const { surface, secret, FakeElement, setElements, setSettingsPanel } = loadSurfaceWithDocument();
  const settingsPanel = new FakeElement('main', { class: 'settings-overlay-panel' });
  settingsPanel.setAttribute('data-settings-active-tab', 'models');
  settingsPanel.querySelectorAll = () => [secret];
  secret.setAttribute('aria-label', 'API key (write only)');
  secret.setAttribute('data-cyrene-agent-secret-input', 'true');
  secret.setAttribute('data-cyrene-risk', 'R3');
  secret.parentElement = settingsPanel;
  setElements([secret]);
  setSettingsPanel(settingsPanel);
  surface.setScope('settings');

  let tree = surface.snapshot({ max_depth: 12 });
  let node = findTreeNode(tree.root, (item) => item.name === 'API key (write only)');
  assert.ok(node);
  assert.equal(node.value_summary, '');
  assert.equal(node.state.input_type, 'password');
  assert.equal(node.actions.length, 1);
  assert.equal(node.actions[0].action_id, 'set_secret');
  assert.equal(node.actions[0].risk, 'R3');
  assert.deepEqual(JSON.parse(JSON.stringify(node.actions[0].input_schema)), { secret_value: 'text<=4000' });
  assert.equal(JSON.stringify(tree).includes('never-expose'), false);

  const rejected = await surface.act({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    node_id: node.node_id,
    action_id: 'set_secret',
    input: { value: 'wrong-field' },
  });
  assert.equal(rejected.error, 'action_failed');
  assert.equal(secret.value, 'never-expose');

  tree = surface.snapshot({ max_depth: 12 });
  node = findTreeNode(tree.root, (item) => item.name === 'API key (write only)');
  const changed = await surface.act({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    node_id: node.node_id,
    action_id: 'set_secret',
    input: { secret_value: 'replacement-key' },
  });
  assert.equal(changed.ok, true);
  assert.equal(secret.value, 'replacement-key');
  assert.equal(JSON.stringify(surface.snapshot({ max_depth: 12 })).includes('replacement-key'), false);
});

test('inspect, click, type, scroll, and drag render the private agent cursor at semantic element centers', async () => {
  const { surface, button, input, scroller, container, document, cursorTimers, FakeElement, setElements } = loadSurfaceWithDocument();
  let tree = surface.snapshot({ max_depth: 12 });
  let run = findTreeNode(tree.root, (node) => node.name === 'Run action');
  const inspected = surface.snapshot({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    parent_node_id: run.node_id,
    _agent_cursor_mode: 'inspect',
  });
  assert.equal(inspected.ok, true);
  const cursor = document.getElementById('cyrene-ui-agent-cursor');
  assert.ok(cursor);
  assert.equal(cursor.style.transform, 'translate3d(54px,29px,0)');
  const inspectHighlight = document.getElementById('cyrene-ui-agent-control-highlight');
  assert.ok(inspectHighlight);
  assert.equal(inspectHighlight.getAttribute('data-node-id'), run.node_id);
  assert.equal(inspectHighlight.getAttribute('data-action-id'), 'inspect');
  assert.equal(inspectHighlight.classList.contains('is-active'), true);
  assert.ok(cursorTimers.includes(3600));

  button.rect = { left: 150, top: 90, width: 100, height: 30 };

  const clicked = await surface.act({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    node_id: run.node_id,
    action_id: 'invoke',
    input: {},
    _agent_cursor_mode: 'click',
  });
  assert.equal(clicked.ok, true);
  assert.equal(button.clicks, 1);
  assert.equal(cursor.style.transform, 'translate3d(194px,99px,0)');
  assert.match(cursor.style.transition, /transform 180ms/);
  assert.ok(cursorTimers.includes(100));

  input.rect = { left: 300, top: 120, width: 120, height: 40 };
  tree = surface.snapshot({ max_depth: 12 });
  let target = findTreeNode(tree.root, (node) => node.name === 'Name');
  const typed = await surface.act({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    node_id: target.node_id,
    action_id: 'set_value',
    input: { value: 'moving target' },
    _agent_cursor_mode: 'target',
  });
  assert.equal(typed.ok, true);
  assert.equal(cursor.style.transform, 'translate3d(354px,134px,0)');

  scroller.rect = { left: 420, top: 220, width: 160, height: 120 };
  tree = surface.snapshot({ max_depth: 12 });
  target = findTreeNode(tree.root, (node) => node.name === 'Scrollable list');
  const scrolled = await surface.act({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    node_id: target.node_id,
    action_id: 'scroll_page',
    input: { delta: 100 },
    _agent_cursor_mode: 'target',
  });
  assert.equal(scrolled.ok, true);
  assert.equal(cursor.style.transform, 'translate3d(494px,274px,0)');

  const source = new FakeElement('button', { text: 'Source', rect: { left: 20, top: 80, width: 80, height: 30 } });
  const targetElement = new FakeElement('button', { text: 'Target', rect: { left: 220, top: 180, width: 100, height: 40 } });
  source.parentElement = container;
  targetElement.parentElement = container;
  setElements([source, targetElement]);
  surface.register({
    node_id: 'source', parent_id: 'root', scope: 'main', order: 1,
    get_element: () => source,
    get_node: () => ({ role: 'button', name: 'Source' }),
    actions: [{ action_id: 'move_after', kind: 'move', input_schema: { target_node_id: 'text<=160' } }],
    handlers: { move_after: () => {} },
  });
  surface.register({
    node_id: 'target', parent_id: 'root', scope: 'main', order: 2,
    get_element: () => targetElement,
    get_node: () => ({ role: 'button', name: 'Target' }),
    actions: [{ action_id: 'invoke', kind: 'invoke' }], handlers: { invoke: () => {} },
  });
  tree = surface.snapshot({ max_depth: 12 });
  const dragged = await surface.act({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    node_id: 'source',
    action_id: 'move_after',
    input: { target_node_id: 'target' },
    _agent_cursor_mode: 'drag',
  });
  assert.equal(dragged.ok, true);
  assert.equal(cursor.style.transform, 'translate3d(264px,194px,0)');
  assert.match(cursor.style.transition, /transform 350ms/);
});

test('agent cursor keeps animated movement but still schedules stale-position fade while running', () => {
  const { surface, document, cursorTimers } = loadSurfaceWithDocument();
  surface.setAgentRunning(true);
  let tree = surface.snapshot({ max_depth: 12 });
  const run = findTreeNode(tree.root, (node) => node.name === 'Run action');
  surface.snapshot({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    parent_node_id: run.node_id,
    _agent_cursor_mode: 'inspect',
  });
  const cursor = document.getElementById('cyrene-ui-agent-cursor');
  assert.ok(cursor);
  assert.equal(cursorTimers.includes(3000), true);

  document.body.rect = { left: 0, top: 0, width: 500, height: 400 };
  tree = surface.snapshot({ max_depth: 12 });
  surface.snapshot({
    snapshot_id: tree.snapshot_id,
    revision: tree.revision,
    parent_node_id: 'root',
    _agent_cursor_mode: 'inspect',
  });
  assert.match(cursor.style.transition, /transform 180ms/);
  assert.equal(cursorTimers.includes(3000), true);

  surface.setAgentRunning(false);
  assert.equal(cursorTimers.includes(3000), true);
});

test('semantic actions wait on renderer animation completion instead of fixed cursor sleeps', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '../src/webui/frontend/platform/ui-surface.jsx'), 'utf8'
  );
  const completion = source.slice(
    source.indexOf('async function waitForAgentCursorCompletion'),
    source.indexOf('function agentControlHighlightElement')
  );
  const act = source.slice(
    source.indexOf('async function act(args)'),
    source.indexOf('async function handleHostRequest')
  );
  assert.match(completion, /getAnimations/);
  assert.match(completion, /animation\.finished/);
  assert.match(completion, /requestAnimationFrame/);
  assert.doesNotMatch(completion, /setTimeout|Promise\.race/);
  assert.doesNotMatch(act, /await delayCursor/);
  assert.match(act, /await waitForAgentCursorCompletion\(clickPress, \{ press: true \}\)/);
});

test('projection keeps only the current layer and deduplicates explicitly registered elements', () => {
  const { surface, button, FakeElement, setSurfaces } = loadSurfaceWithDocument();
  surface.register({
    node_id: 'run_action',
    parent_id: 'root',
    scope: 'main',
    order: 10,
    get_element: () => button,
    get_node: () => ({ role: 'button', name: 'Run action' }),
    actions: [{ action_id: 'invoke', kind: 'invoke' }],
    handlers: { invoke: () => {} },
  });
  let tree = surface.snapshot({ max_depth: 12 });
  assert.equal(
    findTreeNode(tree.root, (node) => node.name === 'Run action').node_id,
    'run_action',
  );

  const dialogButton = new FakeElement('button', { text: 'Confirm dialog' });
  const dialog = new FakeElement('div', { role: 'dialog', 'aria-modal': 'true', rect: { left: 0, top: 0, width: 400, height: 300 } });
  dialogButton.parentElement = dialog;
  dialog.querySelectorAll = (selector) => selector === '*' ? [dialogButton] : [dialogButton];
  setSurfaces([dialog]);
  tree = surface.snapshot({ max_depth: 12 });
  assert.equal(!!findTreeNode(tree.root, (node) => node.node_id === 'run_action'), false);
  assert.equal(!!findTreeNode(tree.root, (node) => node.name === 'Confirm dialog'), true);
});

test('projection excludes controls outside a scroll container viewport', () => {
  const { surface, scroller, FakeElement, setElements } = loadSurfaceWithDocument();
  scroller.rect = { left: 0, top: 0, width: 200, height: 200 };
  const offscreen = new FakeElement('button', {
    text: 'Offscreen action',
    rect: { left: 10, top: 400, width: 100, height: 30 },
  });
  offscreen.parentElement = scroller;
  setElements([scroller, offscreen]);
  const tree = surface.snapshot({ max_depth: 12 });
  assert.equal(!!findTreeNode(tree.root, (node) => node.name === 'Scrollable list'), true);
  assert.equal(!!findTreeNode(tree.root, (node) => node.name === 'Offscreen action'), false);
});

test('message-content projection remains readable without expiring stable UI actions', async () => {
  const { surface, container, FakeElement, setElements } = loadSurfaceWithDocument();
  const stableButton = new FakeElement('button', { text: 'New chat' });
  surface.register({
    node_id: 'new_chat', parent_id: 'root', scope: 'main', order: 1,
    get_node: () => ({ role: 'button', name: 'New chat' }),
    actions: [{ action_id: 'invoke', kind: 'invoke' }],
    handlers: { invoke: () => { stableButton.click(); } },
  });
  const transcript = new FakeElement('section', { 'data-cyrene-revision-volatile': 'true' });
  transcript.parentElement = container;
  const firstMessageAction = new FakeElement('button', { text: 'Copy first message' });
  firstMessageAction.parentElement = transcript;
  setElements([firstMessageAction]);

  const first = surface.snapshot({ max_depth: 12 });
  assert.ok(findTreeNode(first.root, (node) => node.name === 'Copy first message'));

  const streamedMessageAction = new FakeElement('button', { text: 'Copy streamed message' });
  streamedMessageAction.parentElement = transcript;
  setElements([streamedMessageAction]);
  const afterMessageChange = surface.snapshot({ max_depth: 12 });
  assert.equal(afterMessageChange.revision, first.revision);
  assert.ok(findTreeNode(afterMessageChange.root, (node) => node.name === 'Copy streamed message'));

  const action = await surface.act({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    node_id: 'new_chat',
    action_id: 'invoke',
    input: {},
  });
  assert.equal(action.ok, true);
  assert.equal(stableButton.clicks, 1);
});

test('tree read implements include, subtree reads, and revision-bound child pagination', () => {
  const surface = loadSurface();
  surface.register({
    node_id: 'items', parent_id: 'root', scope: 'main', order: 1,
    get_node: () => ({ role: 'list', name: 'Items' }), actions: [], handlers: {},
  });
  for (let index = 0; index < 5; index += 1) {
    surface.register({
      node_id: `item_${index}`, parent_id: 'items', scope: 'main', order: index,
      get_node: () => ({ role: 'button', name: `Item ${index}` }),
      actions: [{ action_id: 'invoke', kind: 'invoke' }], handlers: { invoke: () => {} },
    });
  }
  surface.register({
    node_id: 'text_only', parent_id: 'root', scope: 'main', order: 2,
    get_node: () => ({ role: 'note', name: 'Read me' }), actions: [], handlers: {},
  });

  const first = surface.snapshot({ parent_node_id: 'items', page_size: 2, max_depth: 1 });
  assert.equal(first.root.children.map((node) => node.node_id).join(','), 'item_0,item_1');
  assert.equal(first.page.total, 5);
  assert.equal(first.page.returned, 2);
  assert.ok(first.page.next_cursor);
  assert.equal(first.root.children[0].actions[0].outcome.inspect_after, true);

  const second = surface.snapshot({
    parent_node_id: 'items', cursor: first.page.next_cursor, page_size: 2, max_depth: 1,
  });
  assert.equal(second.root.children.map((node) => node.node_id).join(','), 'item_2,item_3');
  assert.equal(second.page.offset, 2);

  const interactive = surface.snapshot({ include: ['interactive'], max_depth: 2 });
  assert.equal(!!findTreeNode(interactive.root, (node) => node.node_id === 'items'), true);
  assert.equal(!!findTreeNode(interactive.root, (node) => node.node_id === 'text_only'), false);
  const text = surface.snapshot({ include: ['text'], max_depth: 2 });
  assert.equal(!!findTreeNode(text.root, (node) => node.node_id === 'text_only'), true);
  const empty = surface.snapshot({ include: [], max_depth: 2 });
  assert.equal(empty.root.children.length, 0);

  surface.register({
    node_id: 'item_5', parent_id: 'items', scope: 'main', order: 5,
    get_node: () => ({ role: 'button', name: 'Item 5' }),
    actions: [{ action_id: 'invoke', kind: 'invoke' }], handlers: { invoke: () => {} },
  });
  const staleCursor = surface.snapshot({
    parent_node_id: 'items', cursor: first.page.next_cursor, page_size: 2, max_depth: 1,
  });
  assert.equal(staleCursor.error, 'stale_cursor');
  const strictGlobalRead = surface.snapshot({
    snapshot_id: first.snapshot_id, revision: first.revision, parent_node_id: 'items', max_depth: 1,
  });
  assert.equal(strictGlobalRead.error, 'stale_snapshot');
  const compatibleInspect = surface.snapshot({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    parent_node_id: 'items',
    max_depth: 1,
    allow_compatible_node: true,
  });
  assert.equal(compatibleInspect.ok, true);
  assert.equal(compatibleInspect.requested_revision_compatible, true);
  assert.notEqual(compatibleInspect.revision, first.revision);
  assert.ok(findTreeNode(compatibleInspect.root, (node) => node.node_id === 'item_5'));

  surface.register({
    node_id: 'items', parent_id: 'root', scope: 'main', order: 1,
    get_node: () => ({ role: 'list', name: 'Changed items' }), actions: [], handlers: {},
  });
  const changedTargetInspect = surface.snapshot({
    snapshot_id: first.snapshot_id,
    revision: first.revision,
    parent_node_id: 'items',
    max_depth: 1,
    allow_compatible_node: true,
  });
  assert.equal(changedTargetInspect.error, 'stale_snapshot');
});
