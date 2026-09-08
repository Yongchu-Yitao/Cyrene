import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import { transformSync } from 'esbuild'
import { harness } from '../features/chat/hook-harness.test.mjs'
import { partitionRailItems } from '../features/chat/rail-projection.mjs'

const root = new URL('../', import.meta.url)
const read = file => fs.readFileSync(new URL(file, root), 'utf8')

// Preserve imports in the transformed module. A missing import must not be
// masked by injecting its symbol as a VM global.
function load(file, dependencies, globals = {}, expose = '') {
  const context = { module: { exports: {} }, require: () => dependencies, ...globals }
  vm.runInNewContext(transformSync(read(file) + expose, { loader: 'jsx', format: 'cjs' }).code, context)
  return context.module.exports
}

test('notification center opens, reloads and restores the browser on dismissal', () => {
  let open = false, stateIndex = 0, obscured = 0
  const cleanups = [], listeners = new Map(), reloads = []
  const React = {
    useState(initial) {
      const slot = stateIndex++
      return [slot === 0 ? open : initial, value => { if (slot === 0) open = value }]
    },
    useRef: () => ({ current: { contains: () => false } }),
    useEffect: effect => { const cleanup = effect(); if (cleanup) cleanups.push(cleanup) },
    createElement: (type, props, ...children) => ({ type, props, children }),
  }
  const mod = load('features/shell/topbar.jsx', {
    workbenchServices: { model: () => ({}), i18n: () => ({ use: () => ({ t: key => key }) }) },
    wbSetBrowserOverlayObscured: delta => { obscured += delta },
  }, { React, document: {
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: name => listeners.delete(name),
  } }, '\nexport { WorkbenchNotificationCenter };')
  const render = () => {
    cleanups.splice(0).forEach(cleanup => cleanup())
    stateIndex = 0
    return mod.WorkbenchNotificationCenter({ notifications: { items: [] }, onReload: (...args) => reloads.push(args) })
  }
  for (const dismiss of ['escape', 'outside', 'unmount']) {
    const closed = render()
    assert.equal(obscured, 0)
    closed.children[0].props.onClick()
    const opened = render()
    assert.equal(opened.children[1].props.className, 'workbench-notif-popover')
    assert.equal(obscured, 1)
    assert.deepEqual(reloads.at(-1), ['all', 80])
    if (dismiss === 'escape') listeners.get('keydown')({ key: 'Escape' })
    if (dismiss === 'outside') listeners.get('mousedown')({ target: {} })
    if (dismiss === 'unmount') cleanups.splice(0).forEach(cleanup => cleanup())
    else render()
    assert.equal(obscured, 0)
    assert.equal(listeners.size, 0)
  }
})

test('every browser overlay caller imports its coordinator', () => {
  for (const file of fs.readdirSync(root, { recursive: true }).filter(file => file.endsWith('.jsx'))) {
    const source = read(file)
    if (!/\bwbSetBrowserOverlayObscured\(/.test(source) || file === 'shared/browser/overlays.jsx') continue
    assert.match(source, /import\s*\{[^}]*\bwbSetBrowserOverlayObscured\b[^}]*\}\s*from\s*["'][^"']*browser\/overlays\.jsx["']/, file)
  }
})

test('chat error fallback calls the shared API formatter', () => {
  const error = new Error('unclassified failure')
  const mod = load('features/chat/errors.jsx', {
    wbcT: (_key, fallback) => fallback,
    workbenchServices: { api: () => ({ errorText: actual => { assert.equal(actual, error); return 'formatted failure' } }) },
  })
  assert.equal(mod.wbcErrorText(error), 'formatted failure')
})

test('group drop commits persistent chat order and announces the moved group', () => {
  const saved = new Map(), announcements = []
  const h = harness('./rail-ordering.jsx', {
    partitionRailItems,
    WBC_CHAT_ORDER_PREFIX: 'order:',
    wbcOrderChatsByPinned: chats => chats,
    wbcLoadChatOrder: (_project, order) => order,
    wbcNormalizeChatOrder: (_defaults, order) => order,
    wbcBuildChatRailItems: () => [],
    wbcT: (key, _fallback, params) => ({ key, ...params }),
    localStorage: { setItem: (key, value) => saved.set(key, value) },
  })
  h.run(mod => mod.useWbcRailOrdering({ chats: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], groups: [],
    pinnedChatIds: [], projectId: 'p', query: '', setAnnouncement: value => announcements.push(value) }))
  h.value.commitGroupOrder(['c', 'a', 'b'], { title: 'Group' })
  h.flush()
  assert.deepEqual(JSON.parse(saved.get('order:p')), ['c', 'a', 'b'])
  assert.equal(h.value.order.join(','), 'c,a,b')
  assert.equal(announcements[0].key, 'workbenchChat.groupMoved')
  assert.equal(announcements[0].title, 'Group')
  h.value.commitGroupOrder(['a', 'b', 'c'], undefined)
  assert.deepEqual(JSON.parse(saved.get('order:p')), ['a', 'b', 'c'])
  h.unmount()
})

test('chat surfaces bind dependencies in their owning scope', () => {
  assert.match(read('features/chat/page.jsx'), /import \{ wbcSaveDraftAgentBinding \} from "\.\/capabilities\.jsx"/)
  assert.match(read('features/chat/rail.jsx'), /var commitGroupOrder = ordering\.commitGroupOrder/)
  const sideAgent = read('features/chat/split-pane.jsx').split('function WbcSideAgentTab(')[1].split('function WbcSideAgentsPanel(')[0]
  assert.match(sideAgent, /<WbcTranscript[^>]*chatId=\{agent && agent\.id\}/)
})
