import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'

const source = readFileSync(new URL('./custom-plugins.jsx', import.meta.url), 'utf8')
  .replace(/import\s+[\s\S]*?from\s+"[^"\n]+"\n/g, '')
  .replace(/export \{ PluginRegistryPanel \}/, '')
const flush = () => new Promise(resolve => setImmediate(resolve))

function setup(picked) {
  const requests = [], notices = [], busy = [], effects = []
  let reloads = 0, refreshes = 0
  const model = {
    t: (key, fallback) => fallback, setBusy: value => busy.push(value), setNotice() {}, setNoticeKind() {},
    pluginService: {
      reload: async () => { reloads++; return {} },
      refresh: async () => { refreshes++; return {} },
    },
  }
  const sandbox = {
    window: { cyrene: { pickExtensionPath: async () => picked } },
    showSettingsToast: (...args) => { notices.push(args); return true },
    settingsFetch: async (...args) => { requests.push(args); return {} },
    readSettingsResponse: async value => value,
    useEffectSt: fn => effects.push(fn),
  }
  vm.createContext(sandbox)
  vm.runInContext(source, sandbox)
  return { sandbox, model, requests, notices, busy, effects, counts: () => ({ reloads, refreshes }) }
}

test('selected source installs and refreshes; cancellation performs no mutation', async () => {
  for (const picked of [{ path: '/tmp/example.zip' }, { cancelled: true }]) {
    const s = setup(picked)
    s.sandbox.usePluginRegistryMutations(s.model).installFromFile()
    await flush()
    assert.equal(s.requests.length, picked.cancelled ? 0 : 1)
    assert.equal(s.counts().refreshes, picked.cancelled ? 0 : 1)
    if (!picked.cancelled) {
      assert.equal(s.requests[0][0], '/api/plugins/install-file')
      assert.equal(JSON.parse(s.requests[0][1].body).path, picked.path)
    }
    assert.equal(s.busy.at(-1), '')
  }
})

test('entering the page schedules a quiet reload', async () => {
  const s = setup({ cancelled: true })
  s.sandbox.usePluginRegistryState = () => ({ ...s.model, registry: {}, query: '' })
  s.sandbox.registryViewModel = () => ({})
  s.sandbox.usePluginRegistryController({})
  assert.equal(s.effects.length, 1)
  s.effects[0]()
  await flush()
  assert.equal(s.counts().reloads, 1)
  assert.equal(s.notices.length, 0)
})
