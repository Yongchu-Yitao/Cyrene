import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../settings-model-configuration.jsx', import.meta.url), 'utf8');
const component = source.slice(source.indexOf('  function ModelIdCombobox(props)'), source.indexOf('  function ProfileEditor(props)'));

function editor(options = []) {
  const states = [];
  let cursor = 0;
  const changes = [];
  const props = { value: 'old-model', options, onChange(value) { changes.push(value); props.value = value; } };
  const context = vm.createContext({
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
    },
    useRef: () => ({ current: null }),
    listFrom: value => value,
    label: (_props, _key, fallback) => fallback,
    settingsGlyph: () => null,
    h: (tag, props, ...children) => ({ tag, props, children: children.flat() }),
    window: { requestAnimationFrame: callback => callback() },
  });
  vm.runInContext(component, context);
  function render() { cursor = 0; return context.ModelIdCombobox(props); }
  const input = tree => tree.children[0].children[0];
  return {
    changes, props, render,
    type(value) { input(render()).props.onChange({ target: { value } }); },
    value() { return input(render()).props.value; },
    blur() { render().props.onBlur({ relatedTarget: null }); },
    key(key) { input(render()).props.onKeyDown({ key, preventDefault() {} }); },
  };
}

test('clearing and typing model IDs stays local until blur', () => {
  const ui = editor();
  ui.type('');
  assert.equal(ui.value(), '');
  ui.type('replacement');
  assert.deepEqual(ui.changes, []);
  ui.blur();
  assert.deepEqual(ui.changes, ['replacement']);
  assert.equal(ui.value(), 'replacement');
});

test('empty edits and Escape restore the saved ID without saving', () => {
  const ui = editor();
  ui.type('   ');
  ui.blur();
  assert.equal(ui.value(), 'old-model');
  ui.type('partial');
  ui.key('Escape');
  ui.blur();
  assert.equal(ui.value(), 'old-model');
  assert.deepEqual(ui.changes, []);
});

test('Enter commits a manual ID and discovered choices commit immediately', () => {
  const ui = editor();
  ui.type(' custom-model ');
  ui.key('Enter');
  assert.deepEqual(ui.changes, ['custom-model']);
  const discovered = editor([{ model: 'listed-model' }]);
  discovered.type('');
  discovered.key('Enter');
  discovered.blur();
  assert.deepEqual(discovered.changes, ['listed-model']);
  assert.equal(discovered.value(), 'listed-model');
});

test('new model drafts update immediately so the Add action can be enabled', () => {
  const ui = editor();
  ui.props.draft = true;
  ui.type('new-model');
  assert.deepEqual(ui.changes, ['new-model']);
});

test('choosing a discovered model invokes its metadata callback without a stale blur save', () => {
  const ui = editor([{ model: 'listed-model' }]);
  const selected = [];
  ui.props.onSelect = item => { selected.push(item.model); ui.props.value = item.model; };
  ui.type('');
  ui.key('Enter');
  ui.blur();
  assert.deepEqual(selected, ['listed-model']);
  assert.deepEqual(ui.changes, []);
});
