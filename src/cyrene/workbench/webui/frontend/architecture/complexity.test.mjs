import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import { collectLargeJavaScriptFunctions } from "../../build/javascript-complexity.mjs"


const WEBUI_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..")
const baseline = JSON.parse(fs.readFileSync(path.resolve(
  WEBUI_ROOT,
  "..",
  "..",
  "..",
  "..",
  "project-notes",
  "javascript-complexity-baseline.json",
), "utf8"))


test("large JavaScript functions can only shrink", () => {
  const current = collectLargeJavaScriptFunctions(baseline.threshold_lines)
  const newFunctions = Object.keys(current).filter((name) => !(name in baseline.large_functions))
  const grownFunctions = Object.fromEntries(Object.entries(current).filter(
    ([name, lines]) => name in baseline.large_functions && lines > baseline.large_functions[name],
  ))

  assert.deepEqual(newFunctions, [])
  assert.deepEqual(grownFunctions, {})
})
