import test from 'node:test';
import assert from 'node:assert/strict';
import { visibleGraph, mergeCommonPrefixes, graphSvg, graphDiff } from './context-graph-model.mjs';
const node = (chatId, id, parentId, hash = id) => ({ id: chatId + ':' + id, chatId, nodeId: id, parentId: parentId ? chatId + ':' + parentId : '', role: 'user', contentHash: hash, preview: hash });
const edge = (chat, a, b) => ({ id: `${chat}:${a}>${b}`, source: chat + ':' + a, target: chat + ':' + b, kind: 'sequence' });
const graph = { branches: [], nodes: [node('a','root'), node('a','u','root'), node('a','reply','u'), node('b','root'),node('b','u','root'),node('b','reply','u','edited')], edges: [edge('a','root','u'),edge('a','u','reply'),edge('b','root','u'),edge('b','u','reply')] };
test('identical prefixes share display nodes but retain physical identities', () => {
 const result=mergeCommonPrefixes(graph); assert.equal(result.nodes.length,4); assert.equal(result.nodes.find(n=>n.nodeId==='u').members.length,2); assert.equal(result.edges.length,3);
});
test('content edits break prefix equivalence for descendants', () => {
 const changed=structuredClone(graph); changed.nodes[4].contentHash='changed'; changed.nodes[5].contentHash='reply';
 assert.equal(mergeCommonPrefixes(changed).nodes.length,5);
});
test('collapse hides descendants and search reveals matched ancestry',()=>{
 assert.equal(visibleGraph(graph,{collapsed:['a:u']}).nodes.length,2);
 assert.equal(visibleGraph(graph,{collapsed:['a:u']},'edited').nodes.length,3);
});
test('SVG export escapes message content and diff preserves changed lines',()=>{
 const svg=graphSvg([{id:'a',position:{x:0,y:0},data:{preview:'<script>&"'}}],[]); assert.ok(svg.includes('&lt;script&gt;')); assert.ok(!svg.includes('<script>')); assert.equal(graphDiff('a\nb','a\nc')[1].changed,true);
});
test('tool steps fold into connected history and can be expanded', () => {
 const g={branches:[], nodes:[node('a','u'),{...node('a','tool','u'),toolGroup:true},node('a','answer','tool')],edges:[edge('a','u','tool'),edge('a','tool','answer')]};
 const folded=visibleGraph(g); assert.equal(folded.nodes.length,2); assert.equal(folded.edges[0].source,'a:u'); assert.equal(folded.edges[0].target,'a:answer'); assert.equal(folded.edges[0].kind,'tools');
 assert.equal(visibleGraph(g,{showTools:true}).nodes.length,3);
});
test('archiving one copy does not hide its visible shared prefix',()=>{
 const g={...graph,branches:[{chatId:'a',value:{archived:true}},{chatId:'b',value:{}}]};
 assert.equal(visibleGraph(g).nodes.length,3);
 assert.equal(visibleGraph(g,{hidden:['a:u']}).nodes.some(n=>n.id==='a:u'),false);
});
test('context mounts form a side group instead of extending the conversation chain', () => {
 const g={branches:[],nodes:[node('a','u'), {...node('a','c1','u'),role:'context',kind:'memory'}, {...node('a','c2','c1'),role:'context',kind:'system_prompt'}, {...node('a','reply','c2'),role:'assistant'}],edges:[edge('a','u','c1'),edge('a','c1','c2'),edge('a','c2','reply')]};
 const folded=visibleGraph(g); const group=folded.nodes.find(n=>n.groupItems);
 assert.equal(folded.nodes.length,3); assert.equal(group.groupItems.length,2);
 assert.ok(folded.edges.some(e=>e.source==='a:u'&&e.target==='a:reply'));
 assert.ok(folded.edges.some(e=>e.source==='a:u'&&e.target===group.id&&e.kind==='mount'));
 const expanded=visibleGraph(g,{expandedGroups:[group.id]});assert.equal(expanded.nodes.length,5);
 assert.ok(expanded.edges.some(e=>e.source===group.id&&e.target==='a:c1'));
 assert.equal(g.nodes.length,4,'presentation does not mutate execution history');
});
test('grouping preserves all physical sources and reveals matching context on search',()=>{
 const g={...graph,nodes:graph.nodes.map(n=>n.nodeId==='u'?{...n,role:'context',kind:'memory'}:n)};
 const folded=visibleGraph(g),group=folded.nodes.find(n=>n.groupItems);
 assert.equal(group.groupItems[0].members.length,2);
 assert.ok(visibleGraph(g,{},'u').nodes.some(n=>n.id==='a:u'));
});
test('a context revision remains a visible branch endpoint',()=>{
 const g={branches:[{chatId:'a',displayLeafId:'memory'}],nodes:[node('a','u'),{...node('a','memory','u'),role:'context'}],edges:[edge('a','u','memory')]};
 const shown=visibleGraph(g);assert.ok(shown.nodes.some(n=>n.id==='a:memory'));assert.equal(shown.nodes.some(n=>n.groupItems),false);
});
