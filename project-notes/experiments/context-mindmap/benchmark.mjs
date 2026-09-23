// Run from the repository root: node project-notes/experiments/context-mindmap/benchmark.mjs
import { performance } from 'node:perf_hooks';
import os from 'node:os';
import ELK from '../../../src/cyrene/workbench/webui/node_modules/elkjs/lib/elk.bundled.js';
import { visibleGraph } from '../../../src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs';
const elk = new ELK();
const measurements = [];
for (const count of [100, 500, 1000]) {
  const nodes = Array.from({length: count}, (_,i) => ({id: 'n'+i, nodeId:'n'+i, chatId:'a', parentId:i ? 'n'+Math.floor((i-1)/2):'', role:i%2?'user':'assistant', contentHash:String(i),preview:'message '+i}));
  const edges = nodes.slice(1).map(n=>({id:n.parentId+'>'+n.id,source:n.parentId,target:n.id,kind:'sequence'}));
  const start=performance.now(); const graph=visibleGraph({nodes,edges,branches:[]}); const projected=performance.now();
  await elk.layout({id:'root',layoutOptions:{'elk.algorithm':'layered','elk.direction':'RIGHT'},children:graph.nodes.map(n=>({id:n.id,width:240,height:110})),edges:graph.edges.map(e=>({id:e.id,sources:[e.source],targets:[e.target]}))});
  measurements.push({nodes:count,projectionMs:Math.round((projected-start)*100)/100,layoutMs:Math.round(performance.now()-projected),heapMiB:Math.round(process.memoryUsage().heapUsed/1048576)});
}
console.log(JSON.stringify({node:process.version,cpu:os.cpus()[0].model,platform:os.platform(),measurements,scope:'Synthetic graph projection and ELK layout only; not browser frame rate.'},null,2));
