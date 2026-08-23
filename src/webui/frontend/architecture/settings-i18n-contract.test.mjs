import assert from "node:assert/strict"
import path from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import { build } from "esbuild"

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

async function loadCatalogs() {
  const result = await build({
    stdin: {
      contents: [
        'import { WORKBENCH_TRANSLATIONS_EN as en } from "./shared/i18n/catalog-en.jsx";',
        'import { WORKBENCH_TRANSLATIONS_ZH as zh } from "./shared/i18n/catalog-zh.jsx";',
        "export { en, zh };",
      ].join("\n"),
      resolveDir: FRONTEND_ROOT,
      sourcefile: "settings-i18n-contract-entry.mjs",
    },
    bundle: true,
    format: "esm",
    platform: "node",
    write: false,
    logLevel: "silent",
  })
  const source = result.outputFiles[0].text
  return import("data:text/javascript;base64," + Buffer.from(source).toString("base64"))
}

test("settings and shell-visible translation catalogs have locale parity", async () => {
  const { en, zh } = await loadCatalogs()
  const contractKeys = (catalog) => Object.keys(catalog).filter(
    (key) => key.startsWith("settings.") || key.startsWith("workbench."),
  ).sort()
  assert.deepEqual(contractKeys(en), contractKeys(zh))
  for (const key of [
    "settings.shortcuts",
    "settings.resetShortcuts",
    "workbench.page.chat",
    "workbench.resourceShelf.pinned",
    "tour.knowledge-basics.upload.title",
  ]) {
    assert.equal(typeof en[key], "string")
    assert.equal(typeof zh[key], "string")
    assert.ok(en[key].length > 0)
    assert.ok(zh[key].length > 0)
  }
  assert.equal(en["tour.knowledge-basics.upload.title"], "Add content")
  assert.equal(zh["tour.knowledge-basics.upload.title"], "添加内容")
})
