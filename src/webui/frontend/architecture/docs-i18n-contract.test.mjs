import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

const TEST_DIR = path.dirname(fileURLToPath(import.meta.url))
const DOCS_ROOT = path.resolve(TEST_DIR, "../../../../docs/web")
const html = fs.readFileSync(path.join(DOCS_ROOT, "index.html"), "utf8")
const script = fs.readFileSync(path.join(DOCS_ROOT, "script.js"), "utf8")

function articlesById() {
  return new Map(
    [...html.matchAll(/<article\b[^>]*\bid="page-([^"]+)"[^>]*>([\s\S]*?)<\/article>/g)]
      .map((match) => [match[1], match[2]]),
  )
}

function languageBlock(article, lang) {
  const marker = `<div lang="${lang}">`
  const start = article.indexOf(marker)
  assert.notEqual(start, -1, `missing ${lang} block`)
  if (lang === "zh") {
    const end = article.indexOf('<div lang="en">', start + marker.length)
    assert.notEqual(end, -1, "Chinese block must precede the English block")
    return article.slice(start, end)
  }
  return article.slice(start)
}

function topics(block) {
  return [...block.matchAll(/<section\s+data-topic="([^"]+)"/g)].map((match) => match[1])
}

const CRITICAL_TOPICS = {
  installation: ["prerequisites", "prebuilt-package", "source-installation", "verification", "optional-extensions", "troubleshooting", "related-docs"],
  configuration: ["configuration-architecture", "environment-variables", "runtime-settings", "model-pricing"],
  "usage-workbench": ["launching", "pages", "intent-dispatch", "step-by-step-execution", "project-isolation", "related-docs"],
  "usage-cli": ["http-client", "interactive-cli", "chat-slash-commands", "workflow-example"],
  architecture: ["two-phase-loop", "self-control", "project-structure", "security-model"],
  memory: ["three-layer-architecture", "memory-flow", "memory-retirement", "soul-personality", "workbench-memory"],
  subagents: ["overview", "lifecycle", "communication", "use-cases", "considerations"],
  browser: ["features", "setup", "workflow", "configuration", "tools"],
  mcp: ["overview", "adding-servers", "management-commands", "example-output", "runtime-management"],
  scheduler: ["task-types", "scheduling-mechanism", "proactive-lottery", "steward-agent"],
  search: ["search-providers", "deep-research"],
}

test("every documented page has one Chinese block and one English block", () => {
  const articles = articlesById()
  const pagesSource = script.match(/const PAGES\s*=\s*\[([\s\S]*?)\]/)?.[1] || ""
  const declaredPages = [...pagesSource.matchAll(/'([^']+)'/g)].map((match) => match[1])

  assert.deepEqual([...articles.keys()].sort(), declaredPages.sort())
  for (const [id, article] of articles) {
    assert.equal((article.match(/<div lang="zh">/g) || []).length, 1, `${id} must have one Chinese block`)
    assert.equal((article.match(/<div lang="en">/g) || []).length, 1, `${id} must have one English block`)
    assert.ok(languageBlock(article, "zh").trim().length > 0, `${id} Chinese block must not be empty`)
    assert.ok(languageBlock(article, "en").trim().length > 0, `${id} English block must not be empty`)
  }
})

test("critical documentation topics have exact Chinese and English parity", () => {
  const articles = articlesById()
  for (const [id, expected] of Object.entries(CRITICAL_TOPICS)) {
    const article = articles.get(id)
    assert.ok(article, `missing page-${id}`)
    assert.deepEqual(topics(languageBlock(article, "zh")), expected, `${id} Chinese topics changed`)
    assert.deepEqual(topics(languageBlock(article, "en")), expected, `${id} English topics changed`)
  }
})

test("the first paint and runtime language state use the same locale", () => {
  assert.match(html, /<html\b[^>]*\blang="en"[^>]*\bdata-lang="en"/)
  const earlyScript = html.slice(0, html.indexOf('<link rel="stylesheet"'))
  assert.match(earlyScript, /localStorage\.getItem\(['"]cyrene-docs-lang['"]\)/)
  assert.match(earlyScript, /navigator\.language/)
  assert.match(earlyScript, /document\.documentElement\.dataset\.lang\s*=\s*lang/)
  assert.match(earlyScript, /document\.documentElement\.lang\s*=\s*lang\s*===\s*['"]zh['"]/)

  const applyLang = script.match(/function applyLang\(\)\s*\{([\s\S]*?)\n  \}/)?.[1] || ""
  assert.match(applyLang, /setAttribute\(['"]data-lang['"],\s*currentLang\)/)
  assert.match(applyLang, /setAttribute\(['"]lang['"],\s*currentLang\s*===\s*['"]zh['"]/)
})

test("language changes rebuild search from only the active language", () => {
  const toggle = script.match(/function toggleLang\(\)\s*\{([\s\S]*?)\n  \}\n  window\.toggleLang/)?.[1] || ""
  assert.ok(toggle.indexOf("applyLang()") >= 0)
  assert.ok(toggle.indexOf("searchIndex = buildSearchIndex()") > toggle.indexOf("applyLang()"))

  const storageHandler = script.match(/window\.addEventListener\(['"]storage['"],\s*function\(event\)\s*\{([\s\S]*?)\n  \}\)/)?.[1] || ""
  assert.match(storageHandler, /searchIndex\s*=\s*buildSearchIndex\(\)/)

  const pageContent = script.match(/function getPageContent\(pageId\)\s*\{([\s\S]*?)\n  \}\n\n  function buildSearchIndex/)?.[1] || ""
  assert.match(pageContent, /clone\.querySelectorAll\(['"]\[lang\]['"]\)/)
  assert.match(pageContent, /if\s*\(!matches\)\s*node\.remove\(\)/)
})
