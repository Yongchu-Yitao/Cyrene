import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { parser } from "@lezer/javascript"


const WEBUI_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const REPOSITORY_ROOT = path.resolve(WEBUI_ROOT, "..", "..", "..", "..")
const BASELINE_PATH = path.join(
  REPOSITORY_ROOT,
  "project-notes",
  "javascript-complexity-baseline.json",
)
const LARGE_FUNCTION_LINES = 100
const FUNCTION_NODE_TYPES = new Set([
  "ArrowFunction",
  "FunctionDeclaration",
  "FunctionExpression",
  "MethodDeclaration",
])
const javascriptParser = parser.configure({ dialect: "jsx ts" })


function sourceFiles(root) {
  const files = []
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (entry.isDirectory() && ["node_modules", "vendor"].includes(entry.name)) continue
    const target = path.join(root, entry.name)
    if (entry.isDirectory()) files.push(...sourceFiles(target))
    if (entry.isFile() && /\.(?:js|jsx|mjs)$/.test(entry.name)) files.push(target)
  }
  return files
}

function applicationFiles() {
  return [
    ...sourceFiles(path.join(REPOSITORY_ROOT, "electron")),
    ...sourceFiles(path.join(WEBUI_ROOT, "frontend")),
    ...sourceFiles(path.join(
      REPOSITORY_ROOT,
      "src",
      "cyrene",
      "plugins",
      "builtin",
      "cyrene_office",
      "static",
    )),
    path.join(WEBUI_ROOT, "build-jsx.mjs"),
  ].sort()
}

function assignedName(source, offset) {
  const prefix = source.slice(Math.max(0, offset - 240), offset)
  const match = prefix.match(
    /(?:(?:const|let|var)\s+([A-Za-z_$][\w$]*)|([A-Za-z_$][\w$]*))\s*(?:=|:)\s*$/,
  )
  return match ? (match[1] || match[2]) : "<anonymous>"
}

function functionName(node, source) {
  const segment = source.slice(node.from, node.to)
  if (node.type.name === "FunctionDeclaration") {
    const match = segment.match(/^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/)
    return match ? match[1] : "<anonymous>"
  }
  if (node.type.name === "MethodDeclaration") {
    const prefix = segment.slice(0, Math.max(0, segment.indexOf("(")))
    const names = prefix.match(/[A-Za-z_$][\w$]*/g)
    return names?.at(-1) || "<anonymous>"
  }
  if (node.type.name === "FunctionExpression") {
    const match = segment.match(/^function\s*\*?\s*([A-Za-z_$][\w$]*)/)
    if (match) return match[1]
  }
  return assignedName(source, node.from)
}

function lineCount(source, from, to) {
  return (source.slice(from, to).match(/\n/g) || []).length + 1
}

function collectFileFunctions(filePath, threshold) {
  const source = fs.readFileSync(filePath, "utf8")
  const tree = javascriptParser.parse(source)
  const functions = []

  function visit(node, scope) {
    const propertyName = node.getChild("PropertyDefinition")
    const isObjectMethod = node.type.name === "Property"
      && propertyName
      && node.getChild("ParamList")
      && node.getChild("Block")
    const isFunction = FUNCTION_NODE_TYPES.has(node.type.name) || isObjectMethod
    let childScope = scope
    if (isFunction) {
      const name = isObjectMethod
        ? source.slice(propertyName.from, propertyName.to)
        : functionName(node, source)
      childScope = scope.concat(name)
      const lines = lineCount(source, node.from, node.to)
      if (lines >= threshold) functions.push({ name: childScope.join("."), lines })
    }
    for (let child = node.firstChild; child; child = child.nextSibling) {
      visit(child, childScope)
    }
  }

  visit(tree.topNode, [])
  return functions
}

export function collectLargeJavaScriptFunctions(threshold = LARGE_FUNCTION_LINES) {
  const result = {}
  for (const filePath of applicationFiles()) {
    const relative = path.relative(REPOSITORY_ROOT, filePath).split(path.sep).join("/")
    const nameCounts = new Map()
    for (const entry of collectFileFunctions(filePath, threshold)) {
      const count = (nameCounts.get(entry.name) || 0) + 1
      nameCounts.set(entry.name, count)
      const suffix = count === 1 ? "" : `#${count}`
      result[`${relative}::${entry.name}${suffix}`] = entry.lines
    }
  }
  return Object.fromEntries(Object.entries(result).sort(([left], [right]) => (
    left.localeCompare(right)
  )))
}

export function javascriptComplexityBaseline() {
  return {
    threshold_lines: LARGE_FUNCTION_LINES,
    large_functions: collectLargeJavaScriptFunctions(LARGE_FUNCTION_LINES),
  }
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (isMain) {
  const payload = `${JSON.stringify(javascriptComplexityBaseline(), null, 2)}\n`
  if (process.argv.includes("--write")) {
    fs.writeFileSync(BASELINE_PATH, payload, "utf8")
  } else {
    process.stdout.write(payload)
  }
}
