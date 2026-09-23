import { groupContextMounts } from './context-graph-groups.mjs';
function mergeable(node) { return node.contentHash && node.nodeId && node.role !== "task"; }
// Merge only identical content with identical ancestry. Keep every physical
// reference so selection and edits still have an unambiguous tree scope.
export function mergeCommonPrefixes(graph) {
  const raw = new Map(graph.nodes.map(n => [n.id, n])), aliases = new Map(), keys = new Map(), result = new Map();
  const resolving = new Set();
  function resolve(id) {
    if (aliases.has(id)) return aliases.get(id);
    const n = raw.get(id); if (!n || resolving.has(id)) return id;
    resolving.add(id);
    const parent = n.parentId ? resolve(n.parentId) : '';
    if (n.internalCheckpoint && parent) { aliases.set(id, parent); resolving.delete(id); return parent; }
    const key = mergeable(n) ? [n.nodeId, n.role, n.kind, n.contentHash, parent].join('|') : n.id;
    let canonical = keys.get(key);
    if (!canonical) { canonical = id; keys.set(key, id); result.set(id, { ...n, parentId: parent, members: [n] }); }
    else {
      const previous = result.get(canonical), members = [...previous.members, n];
      result.set(canonical, { ...(n.active ? n : previous), id: canonical, parentId: parent, members,
        active: previous.active || n.active, historical: previous.historical && n.historical });
    }
    aliases.set(id, canonical); resolving.delete(id); return canonical;
  }
  for (const n of graph.nodes) resolve(n.id);
  const edges = new Map();
  for (const e of graph.edges) {
    const source = resolve(e.source), target = resolve(e.target);
    if (source === target) continue;
    // A fork's copied root is already represented by its shared prefix.
    if (e.kind === 'fork' && (result.get(target)?.members.length || 0) > 1) continue;
    const key = source + '>' + target;
    if (!edges.has(key)) edges.set(key, { ...e, id: key, source, target });
  }
  return { ...graph, nodes: [...result.values()], edges: [...edges.values()] };
}

export function visibleGraph(graph, view = {}, query = '') {
  graph = mergeCommonPrefixes(graph);
  const collapsed = new Set(view.collapsed || []);
  const archived = new Set((graph.branches || []).filter(b => b.value?.archived && !view.showArchived).map(b => b.chatId));
  const children = new Map();
  for (const e of graph.edges || []) {
    if (e.kind !== 'fork') children.set(e.source, [...(children.get(e.source) || []), e.target]);
  }
  const hidden = new Set(view.hidden || []);
  const visit = id => {
    for (const child of children.get(id) || []) {
      if (hidden.has(child)) continue;
      hidden.add(child); visit(child);
    }
  };
  for (const id of collapsed) visit(id);
  const matches = new Set((graph.nodes || []).filter(n => (n.preview || '').toLowerCase().includes(query.toLowerCase())).map(n => n.id));
  // Search reveals matches and their ancestry, even inside folded branches.
  if (query) {
    const incoming = new Map((graph.edges || []).map(e => [e.target, e.source]));
    for (const id of matches) {
      let parent = id; const seen = new Set();
      while (parent && !seen.has(parent)) { seen.add(parent); hidden.delete(parent); parent = incoming.get(parent); }
    }
  }
  const internal = hiddenTools(graph, view, query);
  const byTarget = new Map(graph.edges.filter(e => e.kind !== 'fork').map(e => [e.target, e]));
  const projectedEdges = graph.edges.filter(e => !internal.has(e.target)).map(e => {
    let source = e.source; const seen = new Set();
    while (internal.has(source) && !seen.has(source)) { seen.add(source); source = byTarget.get(source)?.source; }
    return source ? { ...e, source, ...(seen.size ? { kind: 'tools' } : {}) } : null;
  }).filter(Boolean);
  const nodes = (graph.nodes || []).filter(n => !hidden.has(n.id) && !internal.has(n.id) && (!archived.has(n.chatId) || n.members?.some(m => !archived.has(m.chatId))));
  const ids = new Set(nodes.map(n => n.id));
  return groupContextMounts({ branches: graph.branches, nodes: nodes.map(n => ({ ...n, match: !!query && matches.has(n.id), collapsed: collapsed.has(n.id) })), edges: projectedEdges.filter(e => ids.has(e.source) && ids.has(e.target)) }, groupExpansion(view, query));
}

export function graphMarkdown(graph, details = {}) {
  return ['# Conversation context graph', ...(graph.branches || []).map(b => `\n## ${b.value?.title || b.title || b.id}\n\n${(graph.nodes || []).filter(n => n.chatId === b.chatId).map(n => `### ${n.kind || n.role}\n\n${details[n.id]?.content ?? n.preview ?? ''}`).join('\n\n')}`)].join('\n');
}

export function graphDiff(a, b) {
  const left = String(a || '').split('\n'), right = String(b || '').split('\n');
  // Bounded line comparison; long documents remain inspectable without an
  // unbounded quadratic LCS allocation on the UI thread.
  return Array.from({ length: Math.max(left.length, right.length) }, (_, i) => ({ left: left[i] ?? '', right: right[i] ?? '', changed: left[i] !== right[i] }));
}

export function graphSvg(nodes, edges) {
  const escape = s => String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[c]);
  const byId = new Map(nodes.map(n => [n.id, n]));
  const minX = Math.min(0, ...nodes.map(n => n.position.x)) - 30;
  const minY = Math.min(0, ...nodes.map(n => n.position.y)) - 30;
  const width = Math.max(300, ...nodes.map(n => n.position.x + 280)) - minX;
  const height = Math.max(200, ...nodes.map(n => n.position.y + 140)) - minY;
  const paths = edges.map(e => { const a = byId.get(e.source), b = byId.get(e.target); return a && b ? `<path d="M ${a.position.x + 240} ${a.position.y + 45} C ${a.position.x + 280} ${a.position.y + 45}, ${b.position.x - 40} ${b.position.y + 45}, ${b.position.x} ${b.position.y + 45}" fill="none" stroke="#8791aa"/>` : ''; }).join('');
  const cards = nodes.map(n => `<g transform="translate(${n.position.x},${n.position.y})"><rect width="240" height="100" rx="12" fill="#25262d" stroke="${n.data?.active ? '#64bc9d' : '#666'}"/><text x="12" y="25" fill="#b1bdcd" font-family="sans-serif" font-size="12">${escape(n.data?.kind || n.data?.role)}</text><text x="12" y="52" fill="#eee" font-family="sans-serif" font-size="13">${escape((n.data?.preview || '').slice(0, 25))}</text><text x="12" y="74" fill="#eee" font-family="sans-serif" font-size="13">${escape((n.data?.preview || '').slice(25, 50))}</text></g>`).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${minX} ${minY} ${width} ${height}"><rect x="${minX}" y="${minY}" width="${width}" height="${height}" fill="#1c1d23"/>${paths}${cards}</svg>`;
}

function hiddenTools(graph, view, query) { return new Set(!view.showTools && !query ? graph.nodes.filter(n => n.toolGroup).map(n => n.id) : []); }

function groupExpansion(view, query) { return { all: !!view.showContexts || !!query, ids: view.expandedGroups || [] }; }
