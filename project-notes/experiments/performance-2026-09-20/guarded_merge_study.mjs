import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync} from 'node:fs';
import {performance} from 'node:perf_hooks';
const web=new URL('../../../src/cyrene/workbench/webui/',import.meta.url);
const require=createRequire(new URL('package.json',web));
const {transformSync}=require('esbuild');
const source=readFileSync(new URL('frontend/features/chat/runtime-timeline.jsx',web),'utf8');
const helper=readFileSync(new URL('guarded_merge.mjs',import.meta.url),'utf8').replace('export function','function');
function context(candidate=false){
  const module={exports:{}};
  const site='var merged = wbcMergeChronologicalMessages(durable.filter(function (message) {';
  assert.equal(source.split(site).length,2);
  const src=candidate?source.replace(site,'var merged = researchGuardedMerge(durable.filter(function (message) {'):source;
  const compiled=transformSync(src,{loader:'jsx',format:'cjs'}).code;
  // Both functions and their fixture objects must use one realm. A VM plus
  // host-injected Object misclassifies object literals made by the projection.
  const setup=helper+`
    var researchHits = 0, researchFallbacks = 0;
    function researchGuardedMerge(a,b,minimum=8) {
      var result=tryMergeOrderedMessages(a,b,minimum);
      if(result!==null){researchHits++;return result;}
      researchFallbacks++;return wbcMergeChronologicalMessages(a,b);
    }
    return {module,wbcMergeChronologicalMessages,researchGuardedMerge,tryMergeOrderedMessages,
      get researchHits(){return researchHits},get researchFallbacks(){return researchFallbacks}};`;
  return new Function('require','module','exports',compiled+setup)(()=>({}),module,module.exports);
}
const old=context(), next=context(true);
const plain=x=>JSON.parse(JSON.stringify(x));
const date=n=>new Date(1700000000000+n*1000).toISOString();
let seed=20260920;
const rand=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed%n;};
function freeze(x){if(x&&typeof x==='object'){Object.values(x).forEach(freeze);Object.freeze(x);}return x;}
let checks=0,projections=0,refs=0;
function check(a,b){
  freeze(a);freeze(b);
  const before=JSON.stringify([a,b]);
  const expected=old.wbcMergeChronologicalMessages(a,b);
  const actual=next.researchGuardedMerge(a,b,0);
  assert.deepEqual(plain(actual),plain(expected));
  assert.equal(JSON.stringify([a,b]),before);
  for(let i=0;i<expected.length;i++){
    assert.equal(i in actual,i in expected);
    if(a.includes(expected[i])||b.includes(expected[i])){assert.equal(actual[i],expected[i]);refs++;}
  }
  checks++;
}
const alphabet=[
 {id:'a',role:'assistant',createdAt:date(0)},
 {id:'b',role:'assistant',createdAt:date(1)},
 {id:'a',role:'assistant',createdAt:date(1)},
 {id:'',role:'assistant',createdAt:date(0)},
 {role:'assistant',created_at:date(0)},
 {id:'q',role:'user',answerToQuestionId:'q',optimistic:true,createdAt:date(0)},
 {id:'q2',role:'user',answerToQuestionId:'q',createdAt:date(1)},
 {id:'bad',role:'assistant',createdAt:'invalid'},
];
const lists=[[],...alphabet.map(x=>[x]),...alphabet.flatMap(x=>alphabet.map(y=>[x,y]))];
for(const a of lists)for(const b of lists)check(a,b);
for(let i=0;i<15000;i++){
  const make=(count,offset)=>Array.from({length:count},(_,j)=>{
    const x={id:rand(8)?`m${rand(30)}`:'',role:'assistant',createdAt:date(Math.floor((j+offset)/2)),content:`${i}/${j}`,status:rand(2)?'running':'completed',timelineRevision:rand(5)};
    if(i%3===0){if(rand(5)===0)x.clientRequestId=`r${rand(3)}`;if(rand(6)===0){x.role='user';x.answerToQuestionId='q';x.optimistic=!!rand(2);}if(rand(10)===0)x.createdAt='invalid';}
    if(i%7===0){x.activityCard=true;x.trace=[{toolCallId:`t${rand(4)}`,status:'running'}];}
    return x;
  });
  const a=make(rand(30),0),b=make(rand(25),rand(6));
  if(i%11===0)b.reverse();
  check(a,b);
  if(i<4000){
    const runtime=freeze({userMessages:i%4===0?b.filter(x=>x.role==='user'):[],timeline:{runId:'r',messages:b,removedMessageIds:i%9===0?['m1','m2']:[],status:i%2?'running':'completed'},pendingQuestion:i%5===0?{id:'q'}:null,reconnecting:i%7===0});
    assert.deepEqual(plain(next.module.exports.wbcProjectTranscript(a,runtime)),plain(old.module.exports.wbcProjectTranscript(a,runtime)));
    projections++;
  }
}
const directed=[];
for(const [name,build] of [
 ['sparse history',()=>[Array(1),[{id:'b',role:'assistant',createdAt:date(1)}]]],
 ['sparse additions',()=>[[],Array(3)]],
 ['null entries',()=>[[null],[null,{id:'b',role:'assistant',createdAt:date(1)}]]],
 ['numeric id',()=>[[],[{id:12,role:'assistant',createdAt:date(1)}]]],
 ['null prototype',()=>[[],[Object.assign(Object.create(null),{id:'b',role:'assistant',createdAt:date(1)})]]],
 ['request confirmation',()=>[[{id:'local',role:'user',optimistic:true,clientRequestId:'r',createdAt:date(0)}],[{id:'server',role:'user',clientRequestId:'r',createdAt:date(1)}]]],
 ['same-time stability',()=>[[{id:'a',role:'assistant',createdAt:date(1)}],[{id:'b',role:'assistant',createdAt:date(1)},{id:'c',role:'assistant',createdAt:date(1)}]]],
]){check(...build());directed.push(name);}
// Check fallback does not invoke ordinary getters ahead of the original path.
for(const kind of ['record getter','array index getter','custom array method']){
  function run(candidate){
    let calls=0;
    const b=Array.from({length:8},(_,i)=>({id:`a${i}`,role:'assistant',createdAt:date(i)}));
    if(kind==='record getter')Object.defineProperty(b[0],'id',{get(){calls++;return 'a0';}});
    if(kind==='array index getter'){const first=b[0];Object.defineProperty(b,'0',{get(){calls++;return first;}});}
    if(kind==='custom array method')b.forEach=function(fn){calls++;Array.prototype.forEach.call(this,fn);};
    const value=candidate?next.researchGuardedMerge([],b):old.wbcMergeChronologicalMessages([],b);
    return {calls,value:plain(value)};
  }
  assert.deepEqual(run(true),run(false));directed.push(kind);
}
const counters={fast:next.researchHits,fallback:next.researchFallbacks};
// Exercise the actual enabled call site through snapshot/delta/removal/stale-patch transitions.
const history=Array.from({length:100},(_,i)=>({id:`history${i}`,role:'assistant',createdAt:date(i),content:'old'}));
let oldRuntime={},newRuntime={},replayChecks=0;
const replayHits=next.researchHits;
for(let revision=1;revision<=120;revision++){
  let patch;
  if(revision===1||revision===61){
    patch={version:2,snapshot:true,runId:'r',revision,status:'running',messages:Array.from({length:40},(_,i)=>({id:`live${i}`,role:'assistant',createdAt:date(100+i),content:'x',timelineOrder:i,timelineRevision:revision,status:'running'}))};
  }else if(revision%11===0){
    patch={version:2,runId:'r',revision,status:'running',removedMessageIds:['live0'],messages:[]};
  }else{
    const record=oldRuntime.timeline.messages.find(x=>x.id==='live39');
    patch={version:2,runId:'r',revision,status:revision===120?'completed':'running',updates:[{id:record.id,baseRevision:record.timelineRevision,append:{content:'a'},set:{timelineRevision:revision}}]};
  }
  oldRuntime=old.module.exports.wbcApplyTimeline(oldRuntime,patch);
  newRuntime=next.module.exports.wbcApplyTimeline(newRuntime,patch);
  for(const stale of [false,true]){
    if(stale){oldRuntime=old.module.exports.wbcApplyTimeline(oldRuntime,patch);newRuntime=next.module.exports.wbcApplyTimeline(newRuntime,patch);}
    assert.deepEqual(plain(next.module.exports.wbcProjectTranscript(history,newRuntime)),plain(old.module.exports.wbcProjectTranscript(history,oldRuntime)));
    replayChecks++;
  }
}
assert.ok(next.researchHits>replayHits,'replay must exercise the enabled integration');
function median(fn){for(let i=0;i<2;i++)fn();const samples=[];for(let i=0;i<7;i++){const t=performance.now();fn();samples.push(performance.now()-t);}samples.sort((a,b)=>a-b);return samples[3];}
const timings=[];
for(const [n,m,shape='ordered'] of [[20,1],[20,8],[100,8],[1000,1],[1000,4],[1000,8],[1000,32],[1000,1000],[1000,8,'invalid-last-history'],[1000,32,'question']]){
  const a=Array.from({length:n},(_,i)=>({id:`a${i}`,role:'assistant',createdAt:date(i)}));
  const b=Array.from({length:m},(_,i)=>({id:`b${i}`,role:'assistant',createdAt:date(n+i)}));
  const runtime={timeline:{runId:'r',status:'running',messages:b},userMessages:[]};
  if(shape==='invalid-last-history')a[n-1].createdAt='invalid';
  if(shape==='question'){b[0].role='user';b[0].answerToQuestionId='question';}
  assert.deepEqual(plain(next.module.exports.wbcProjectTranscript(a,runtime)),plain(old.module.exports.wbcProjectTranscript(a,runtime)));
  const hits=next.researchHits;
  const baselineMs=median(()=>old.module.exports.wbcProjectTranscript(a,runtime));
  const guardedMs=median(()=>next.module.exports.wbcProjectTranscript(a,runtime));
  const fastPath=next.researchHits>hits;
  assert.equal(fastPath,m>=8&&m*(n+m)>=4096&&shape==='ordered');
  timings.push({n,m,shape,baseline_projection_ms:baselineMs,guarded_projection_ms:guardedMs,fast_path:fastPath});
}
const report={checks,projections,refs,directed,counters,replayChecks,timings,note:'Same-realm Node execution, 2 warmups/7 timing samples, medians. Prototype changes only one in-memory projection call site; no product edits; no browser/GC/peak-RSS validation.'};
writeFileSync(new URL('guarded-merge-study.json',import.meta.url),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
