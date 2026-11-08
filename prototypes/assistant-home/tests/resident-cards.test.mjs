import test from 'node:test';
import assert from 'node:assert/strict';
import {newSession,act,finish,budgetFor} from '../server/demo-engine.mjs';
import {refreshCards,applyCardAction} from '../server/resident-cards.mjs';
function session(){return refreshCards(newSession());}
function change(s,kind,action,payload={}){const outcome=act(s[kind],action,payload);if(outcome.workspace)s[kind]=outcome.workspace;s[kind].revision++;if(outcome.run){finish(s[kind],outcome.run);s[kind].revision++;}refreshCards(s);}
function pin(s,kind,type){applyCardAction(s,'pin',{kind,type,taskId:s[kind].taskId});refreshCards(s);return s.shelf.pins.at(-1);}
test('Agent card selection follows task, budget intent and generated research results',()=>{
 const s=session();change(s,'travel','start',{text:'杭州3天'});assert.deepEqual(s.travel.cards.map(c=>c.type),['itinerary']);
 change(s,'travel','chat',{text:'显示预算'});assert.ok(s.travel.cards.some(c=>c.type==='budget'));
 change(s,'travel','chat',{text:'生成出行提示卡片'});assert.ok(s.travel.cards.some(c=>c.type==='notes'));
 change(s,'research','start',{text:'调研 Agent UI'});assert.deepEqual(s.research.cards.map(c=>c.type),['sources']);
 change(s,'research','generate-report',{requirements:'列出风险'});assert.deepEqual(s.research.cards.map(c=>c.type),['result','risks']);
});
test('pinned content stays live and a new task cannot overwrite the old pin',()=>{
 const s=session();change(s,'travel','start',{text:'杭州3天，预算2000'});const p=pin(s,'travel','budget'),oldId=s.travel.taskId;
 const before=budgetFor(p.snapshot).total;const place=s.travel.places.find(p=>p.cost>0);change(s,'travel','toggle-place',{id:place.id});assert.equal(budgetFor(p.snapshot).total,before-place.cost);
 change(s,'travel','chat',{text:'生成地图卡片'});const lastRevision=p.snapshot.revision;
 change(s,'travel','start',{text:'上海2天'});assert.notEqual(s.travel.taskId,oldId);assert.equal(p.snapshot.city.name,'杭州');assert.equal(p.snapshot.revision,lastRevision);assert.ok(!s.travel.cards.some(c=>c.type==='map'));
 pin(s,'travel','budget');assert.equal(s.shelf.pins.length,2);assert.equal(s.shelf.pins[1].snapshot.city.name,'上海');
 assert.throws(()=>applyCardAction(s,'pin',{kind:'travel',taskId:oldId,type:'map'}),/任务已切换/);
});
test('pinning is deduplicated; hidden agent cards and collapse preferences survive serialization',()=>{
 const s=session();change(s,'travel','start',{text:'京都3天，预算5000'});const p=pin(s,'travel','itinerary');pin(s,'travel','itinerary');assert.equal(s.shelf.pins.length,1);
 applyCardAction(s,'collapse',{id:p.id});applyCardAction(s,'remove',{id:p.id});const restored=refreshCards(JSON.parse(JSON.stringify(s)));assert.equal(restored.shelf.pins.length,0);assert.ok(restored.shelf.dismissed.includes(p.id));assert.ok(restored.shelf.collapsed.includes(p.id));
 applyCardAction(restored,'restore');assert.equal(restored.shelf.dismissed.length,0);pin(restored,'travel','itinerary');assert.ok(!restored.shelf.collapsed.includes(p.id));
});
test('pinned report keeps the last result while sources are edited and follows the regenerated result',()=>{
 const s=session();change(s,'research','start',{text:'Agent交互研究'});change(s,'research','generate-report',{requirements:'风险'});const p=pin(s,'research','result');const prior=p.snapshot.result.findings.length;
 change(s,'research','edit-sources');assert.equal(p.snapshot.result.findings.length,prior);
 const selected=s.research.sources.find(c=>c.selected);change(s,'research','toggle-source',{id:selected.id});change(s,'research','generate-report',{});assert.equal(p.snapshot.result.findings.length,prior-1);
 const id=p.taskId;change(s,'research','reset');assert.equal(p.taskId,id);assert.notEqual(s.research.taskId,id);assert.ok(p.snapshot.result);
});
test('invalid content and busy task cannot be pinned',()=>{
 const s=session();assert.throws(()=>pin(s,'research','result'),/还没有/);act(s.travel,'start',{text:'杭州3天'});refreshCards(s);assert.throws(()=>pin(s,'travel','map'),/等待/);finish(s.travel,'prepare');refreshCards(s);assert.throws(()=>pin(s,'travel','unknown'),/还没有/);
});
