// Presentation only: lifecycle mounts are dependencies, not conversation turns.
// The original graph and physical identities remain available to the inspector.
export function groupContextMounts(graph, expanded = false) {
  const byId = new Map(graph.nodes.map(n => [n.id, n]));
  const incoming = new Map(graph.edges.filter(e => e.kind !== 'fork').map(e => [e.target, e.source]));
  const mounts = groupedMountIds(graph);
  const anchors = new Map(), groups = new Map();
  function anchor(id) {
    if (anchors.has(id)) return anchors.get(id);
    let parent = incoming.get(id); const seen = new Set([id]);
    while (mounts.has(parent) && !seen.has(parent)) { seen.add(parent); parent = incoming.get(parent); }
    const resolved = byId.has(parent) ? parent : null;
    anchors.set(id, resolved); return resolved;
  }
  for (const n of graph.nodes) {
    if (!mounts.has(n.id) || !anchor(n.id)) continue;
    const kind = n.role === 'task' ? 'tasks' : 'contexts', id = `${kind}:${anchor(n.id)}`;
    if (!groups.has(id)) groups.set(id, { id, anchorId: anchor(n.id), role: 'context_group', groupKind: kind, groupItems: [], members: [], preview: '', active: false });
    const group = groups.get(id); group.groupItems.push(n); group.active ||= n.active;
  }
  const grouped = new Map([...groups.values()].flatMap(g => g.groupItems.map(n => [n.id, g.id])));
  const nodes = graph.nodes.filter(n => !grouped.has(n.id));
  const edges = new Map();
  for (const e of graph.edges) {
    if (grouped.has(e.target)) continue;
    const source = grouped.has(e.source) ? anchor(e.source) : e.source;
    if (source && source !== e.target) edges.set(source + '>' + e.target, conversationEdge(e, source));
  }
  for (const group of groups.values()) appendGroup(nodes, edges, group, expanded);

  return { ...graph, nodes, edges: [...edges.values()] };
}

function appendGroup(nodes, edges, group, expansion) {
  const expanded = expansion === true || expansion?.all || expansion?.ids?.includes(group.id);
  group.collapsed = !expanded;
  group.preview = group.groupItems.map(n => n.kind || n.role).join(' · ');
  nodes.push(group);
  edges.set(group.id, {id:group.id,source:group.anchorId,target:group.id,kind:'mount'});
  if (!expanded) return;
  for (const n of group.groupItems) {
    nodes.push(n); edges.set('item:'+n.id, {id:'item:'+n.id,source:group.id,target:n.id,kind:'mount'});
  }
}

function conversationEdge(edge, source) { return { ...edge, id: source + '>' + edge.target, source, kind: edge.kind === 'mount' ? 'sequence' : edge.kind }; }

function groupedMountIds(graph) {
  const ends = new Set((graph.branches || []).map(b => b.chatId + ':' + (b.displayLeafId || b.leafId)));
  return new Set(graph.nodes.filter(n => {
    if (n.role === 'task') return true;
    if (n.role !== 'context') return false;
    return !(n.members || [n]).some(member => ends.has(member.id));
  }).map(n => n.id));
}
