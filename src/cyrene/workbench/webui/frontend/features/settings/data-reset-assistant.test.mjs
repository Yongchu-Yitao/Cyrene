import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'

const source = readFileSync(new URL('./data.jsx', import.meta.url), 'utf8')
  .replace(/import\s+[\s\S]*?from\s+"[^"\n]+"\n/g, '')
  .replace(/export \{[^}]+\};/, '')

function setup({ confirmed = true, failure = false } = {}) {
  const requests = [], notices = [], busy = []
  let reloads = 0, confirmation
  const element = (type, props, ...children) => ({ type, props: props || {}, children })
  const sandbox = {
    React: { createElement: element, cloneElement: (node, props) => ({ ...node, props }) },
    useStateSt: value => [value, () => {}], useEffectSt() {},
    localStorage: { getItem: () => null },
    workbenchServices: {
      data: () => ({ useVersion() {}, state: { sessions: [{ id: 'ordinary-chat' }] } }),
      feedback: () => ({ confirmModal: async options => { confirmation = options; return confirmed } }),
    },
    window: { location: { reload: () => reloads++ } },
    settingsFetch: async (url, options) => {
      requests.push({ url, options })
      if (failure && options?.method === 'POST') throw new Error('conversation_running')
      return { assistant_id: 'assistant_global', ok: true }
    },
    readSettingsResponse: async response => response,
    showSettingsToast: (...args) => notices.push(args),
    SectionTitle: () => null, SectionBlock: () => element('section'), Toggle: () => null,
    FieldRow: (title, hint, control, unused, id) => element('row', { id }, control),
  }
  vm.createContext(sandbox)
  vm.runInContext(source, sandbox)
  const tree = sandbox.DataPanel({
    t: key => key, resetting: false, setResetting: value => busy.push(value),
    config: {}, backupList: [], exportSids: [], formatBytes: String,
  })
  const row = tree.children.find(node => node?.props?.id === 'setting-reset-assistant')
  return { button: row.children[0], requests, notices, busy,
    counts: () => ({ reloads, confirmation }) }
}

test('reset confirms and clears only the assistant before refreshing', async () => {
  const h = setup()
  await h.button.props.onClick()
  assert.equal(h.counts().confirmation.danger, true)
  assert.deepEqual(h.requests.map(r => r.url), [
    '/api/workbench/assistant', '/api/workbench/sessions/assistant_global/clear',
  ])
  assert.equal(h.requests[1].options.method, 'POST')
  assert.equal(h.counts().reloads, 1)
  assert.deepEqual(h.busy, [true, false])
})

test('cancelling reset leaves conversations untouched', async () => {
  const h = setup({ confirmed: false })
  await h.button.props.onClick()
  assert.equal(h.requests.length, 0)
  assert.equal(h.counts().reloads, 0)
  assert.deepEqual(h.busy, [true, false])
})

test('failed reset reports the error and permits retry without reloading', async () => {
  const h = setup({ failure: true })
  await h.button.props.onClick()
  assert.equal(h.counts().reloads, 0)
  assert.match(h.notices[0][0], /conversation_running/)
  assert.equal(h.notices[0][1], 'error')
  assert.equal(h.busy.at(-1), false)
})
