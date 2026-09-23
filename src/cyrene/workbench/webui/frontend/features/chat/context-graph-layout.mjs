import ELK from 'elkjs/lib/elk-api.js';
let engine;
export async function layoutContextGraph(nodes, edges) {
  if (!engine) engine = new ELK({ workerFactory: () => new Worker(new URL('../elk-worker.min.js', import.meta.url)) });
  const result = await engine.layout({ id: 'root', layoutOptions: {
    'elk.algorithm': 'layered', 'elk.direction': 'RIGHT',
    'elk.spacing.nodeNode': '35', 'elk.layered.spacing.nodeNodeBetweenLayers': '75',
    'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
  }, children: nodes.map(n => ({ id: n.id, width: 240, height: 110 })),
  edges: edges.map(e => ({ id: e.id, sources: [e.source], targets: [e.target] })) });
  return Object.fromEntries(result.children.map(n => [n.id, { x: n.x, y: n.y }]));
}
