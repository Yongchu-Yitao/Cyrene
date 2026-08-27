import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import { build } from "esbuild"

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

async function loadCatalogs() {
  const result = await build({
    stdin: {
      contents: [
        'import { WORKBENCH_TRANSLATIONS_EN as coreEn } from "./shared/i18n/catalog-en.jsx";',
        'import { WORKBENCH_TRANSLATIONS_ZH as coreZh } from "./shared/i18n/catalog-zh.jsx";',
        'import { EXTENSION_TRANSLATIONS as extension } from "./shared/i18n/extension-translations.jsx";',
        'import { ADDITIONAL_TRANSLATIONS as additional } from "./shared/i18n/translations.jsx";',
        "export { coreEn, coreZh, extension, additional };",
        "export const mergedEn = { ...extension.en, ...coreEn, ...additional.en };",
        "export const mergedZh = { ...extension.zh, ...coreZh, ...additional.zh };",
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

function placeholders(value) {
  const withoutPluralChoices = String(value).replace(/\{\{[^{}]*\|[^{}]*\}\}/g, "")
  return [...withoutPluralChoices.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)]
    .map((match) => match[1])
    .sort()
}

function frontendSourceFiles(root) {
  const output = []
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name)
    if (entry.isDirectory()) output.push(...frontendSourceFiles(target))
    else if (/\.jsx?$/.test(entry.name)) output.push(target)
  }
  return output
}

function assertCatalogPair(en, zh, label) {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort())
  for (const key of Object.keys(en)) {
    assert.equal(typeof en[key], "string", `${label}: English value for ${key} must be a string`)
    assert.equal(typeof zh[key], "string", `${label}: Chinese value for ${key} must be a string`)
    assert.ok(en[key].trim().length > 0, `${label}: English value for ${key} must not be empty`)
    assert.ok(zh[key].trim().length > 0, `${label}: Chinese value for ${key} must not be empty`)
    assert.deepEqual(placeholders(en[key]), placeholders(zh[key]), `${label}: placeholder mismatch for ${key}`)
  }
}

test("all translation catalog layers have locale parity and valid values", async () => {
  const { coreEn, coreZh, extension, additional, mergedEn: en, mergedZh: zh } = await loadCatalogs()
  assertCatalogPair(coreEn, coreZh, "core")
  assertCatalogPair(extension.en, extension.zh, "extension")
  assertCatalogPair(additional.en, additional.zh, "additional")
  assertCatalogPair(en, zh, "merged")
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

test("catalog sources do not silently overwrite duplicate keys", () => {
  for (const filename of ["catalog-en.jsx", "catalog-zh.jsx"]) {
    const source = fs.readFileSync(path.join(FRONTEND_ROOT, "shared/i18n", filename), "utf8")
    const seen = new Set()
    for (const match of source.matchAll(/^\s*"([^"]+)"\s*:/gm)) {
      assert.ok(!seen.has(match[1]), `${filename} defines ${match[1]} more than once`)
      seen.add(match[1])
    }
  }
  for (const filename of ["extension-translations.jsx", "translations.jsx"]) {
    const source = fs.readFileSync(path.join(FRONTEND_ROOT, "shared/i18n", filename), "utf8")
    const counts = new Map()
    for (const match of source.matchAll(/^\s*"([^"]+)"\s*:/gm)) {
      counts.set(match[1], (counts.get(match[1]) || 0) + 1)
    }
    for (const [key, count] of counts) {
      assert.equal(count, 2, `${filename} must define ${key} exactly once per locale`)
    }
  }
})

test("catalog layers do not silently override one another", async () => {
  const { coreEn, extension, additional } = await loadCatalogs()
  const layers = [
    ["core", new Set(Object.keys(coreEn))],
    ["extension", new Set(Object.keys(extension.en))],
    ["additional", new Set(Object.keys(additional.en))],
  ]
  for (let left = 0; left < layers.length; left += 1) {
    for (let right = left + 1; right < layers.length; right += 1) {
      const overlap = [...layers[left][1]].filter((key) => layers[right][1].has(key)).sort()
      assert.deepEqual(overlap, [], `${layers[left][0]} and ${layers[right][0]} overlap`)
    }
  }
})

test("literal frontend translation calls are backed by the merged catalog", async () => {
  const { mergedEn: en, mergedZh: zh } = await loadCatalogs()
  const wrappers = [
    "t", "T", "L", "wbcT", "wbT", "wbModelT", "apiT", "bootstrapT",
    "terminalT", "diffT", "translate", "browserLabel", "fallback",
  ]
  const callPattern = new RegExp(`\\b(?:${wrappers.join("|")})\\(\\s*["']([^"']+)["']`, "g")
  const helperPattern = /\btext\(\s*(?!["'])[^,]+,\s*["']([^"']+)["']/g
  const missing = []
  for (const filename of frontendSourceFiles(FRONTEND_ROOT)) {
    const source = fs.readFileSync(filename, "utf8")
    for (const pattern of [callPattern, helperPattern]) {
      pattern.lastIndex = 0
      for (const match of source.matchAll(pattern)) {
        const key = match[1]
        if (key.endsWith(".")) continue
        if (!Object.hasOwn(en, key) || !Object.hasOwn(zh, key)) {
          const line = source.slice(0, match.index).split("\n").length
          missing.push(`${path.relative(FRONTEND_ROOT, filename)}:${line} ${key}`)
        }
      }
    }
  }
  assert.deepEqual(missing, [])
})
