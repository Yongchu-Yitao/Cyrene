import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"



const WEBUI_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const REPOSITORY_ROOT = path.resolve(WEBUI_ROOT, "..", "..", "..", "..")
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

export function applicationFiles() {
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

export function functionName(node, source) {
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
