import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parser } from '@lezer/javascript'
import { applicationFiles, functionName } from './javascript-complexity.mjs'

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../..')
export const BASELINE = path.join(ROOT, 'project-notes/javascript-structure-baseline.json')
export const LIMITS = Object.freeze({ lines: 99, decisions: 20, nesting: 5, hooks: 15, methods: 25, fields: 25, module_lines: 1000, parse_errors: 0, setter_targets: 10, subscriptions: 10 })
const syntax = parser.configure({ dialect: 'jsx ts' })
const FUNCTIONS = new Set(['ArrowFunction', 'FunctionDeclaration', 'FunctionExpression', 'MethodDeclaration'])
const CONTROLS = new Set(['IfStatement', 'ForStatement', 'WhileStatement', 'DoStatement', 'SwitchStatement', 'TryStatement', 'ConditionalExpression'])
const DECISIONS = new Set(['IfStatement', 'ForStatement', 'WhileStatement', 'DoStatement', 'CaseLabel', 'CatchClause', 'ConditionalExpression', 'LogicOp'])
function isFunction(node) {
  return FUNCTIONS.has(node.name) || (node.name === 'Property' && node.getChild('ParamList') && node.getChild('Block'))
}
function children(node, visit) {
  for (let child = node.firstChild; child; child = child.nextSibling) visit(child)
}
function ownMetrics(node, source) {
  const metrics = { lines: source.slice(node.from, node.to).split('\n').length, decisions: 0, nesting: 0, hooks: 0, setter_targets: 0, subscriptions: 0 }
  const setters = new Set()
  function visit(child, depth) {
    if (isFunction(child) || child.name === 'ClassDeclaration' || child.name === 'ClassExpression') return
    if (DECISIONS.has(child.name)) metrics.decisions++
    if (CONTROLS.has(child.name)) depth++
    metrics.nesting = Math.max(metrics.nesting, depth)
    if (child.name === 'CallExpression') {
      const callee = child.firstChild
      const call = source.slice(callee.from, callee.to)
      if (/^(?:[A-Za-z_$][\w$]*\.)?set[A-Z][\w$]*$/.test(call)) setters.add(call)
      if (/(?:^|\.)(?:addEventListener|subscribe|on|once|onManagerState)$/.test(call)) metrics.subscriptions++
      if (/^(?:use[A-Z]\w*|React\.use[A-Z]\w*)$/.test(source.slice(callee.from, callee.to))) metrics.hooks++
    }
    children(child, nested => visit(nested, depth))
  }
  children(node, child => visit(child, 0))
  metrics.setter_targets = setters.size
  return metrics
}
function classMetrics(node, source) {
  const body = node.getChild('ClassBody')
  let methods = 0
  const fields = new Set()
  function visit(child) {
    if (child.name === 'ClassDeclaration' || child.name === 'ClassExpression') return
    if (child.name === 'AssignmentExpression' || child.name === 'UpdateExpression') {
      const target = child.firstChild
      const match = source.slice(target.from, target.to).match(/^this\.([\w$]+)$/)
      if (match) fields.add(match[1])
    }
    children(child, visit)
  }
  if (body) children(body, child => {
    if (child.name === 'MethodDeclaration') methods++
    if (child.name === 'PropertyDeclaration') {
      const field = child.getChild('PropertyDefinition')
      if (field) fields.add(source.slice(field.from, field.to))
    }
    visit(child)
  })
  return { methods, fields: fields.size }
}

export function analyzeSource(source, filename, onScope = () => {}) {
  const tree = syntax.parse(source)
  const result = { [filename]: { module_lines: source.split(/\n/).length - Number(source.endsWith('\n')) } }
  const counts = new Map()
  function visit(node, scope) {
    if (node.type.isError) result[filename].parse_errors = (result[filename].parse_errors || 0) + 1
    let name, metrics
    if (isFunction(node)) {
      const property = node.getChild('PropertyDefinition')
      name = node.name === 'Property' ? source.slice(property.from, property.to) : functionName(node, source)
      metrics = ownMetrics(node, source)
    } else if (node.name === 'ClassDeclaration' || node.name === 'ClassExpression') {
      const definition = node.getChild('VariableDefinition')
      name = definition ? source.slice(definition.from, definition.to) : '<class>'
      metrics = classMetrics(node, source)
    }
    if (metrics) {
      scope = [...scope, name]
      const key = `${filename}::${scope.join('.')}`
      const count = (counts.get(key) || 0) + 1
      counts.set(key, count)
      const identity = key + (count > 1 ? `#${count}` : '')
      result[identity] = metrics
      onScope(identity, source.slice(node.from, node.to))
    }
    children(node, child => visit(child, scope))
  }
  visit(tree.topNode, [])
  return result
}

export function collectStructure(readSource = file => fs.readFileSync(file, 'utf8')) {
  const extra = path.join(ROOT, 'src/cyrene/plugins/builtin/cyrene_remote_desktop/frontend/remote-desktop.js')
  const files = [...applicationFiles(), extra].filter(file => !/\.(test|spec)\.[cm]?jsx?$/.test(file))
  return Object.assign({}, ...files.sort().map(file => analyzeSource(readSource(file), path.relative(ROOT, file).split(path.sep).join('/'))))
}
export function exceptions(metrics) {
  return Object.fromEntries(Object.entries(metrics).sort(([a], [b]) => a.localeCompare(b)).flatMap(([key, values]) => {
    const excess = Object.fromEntries(Object.entries(values).filter(([name, value]) => value > LIMITS[name]))
    return Object.keys(excess).length ? [[key, excess]] : []
  }))
}
export function violations(current, baseline) {
  return Object.entries(current).flatMap(([key, values]) => Object.entries(values).flatMap(([name, value]) => {
    const limit = Math.max(LIMITS[name], baseline[key]?.[name] || 0)
    return value > limit ? [`${key}: ${name} ${value} > ${limit}`] : []
  }))
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const current = collectStructure()
  if (process.argv.includes('--initialize')) {
    fs.writeFileSync(BASELINE, JSON.stringify(exceptions(current), null, 2) + '\n', { flag: 'wx' })
  } else {
    const failures = violations(current, JSON.parse(fs.readFileSync(BASELINE, 'utf8')))
    if (failures.length) { process.stderr.write(failures.join('\n') + '\n'); process.exitCode = 1 }
    else {
      if (process.argv.includes('--ratchet')) fs.writeFileSync(BASELINE, JSON.stringify(exceptions(current), null, 2) + '\n')
      process.stdout.write(`JavaScript complexity: ${Object.keys(current).length} scopes checked; no budget growth\n`)
    }
  }
}
