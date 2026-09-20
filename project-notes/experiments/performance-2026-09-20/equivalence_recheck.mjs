// Audit the previous experimental fast path, without modifying production files.
import {createRequire} from 'node:module';
import {readFileSync, writeFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const web = new URL('../../../src/cyrene/workbench/webui/', import.meta.url);
const require = createRequire(new URL('package.json', web));
const {transformSync} = require('esbuild');
const source = readFileSync(new URL('frontend/features/chat/runtime-timeline.jsx', web), 'utf8');
const code = transformSync(source, {loader:'jsx',format:'cjs'}).code;
function makeContext() {
  const ctx = {module:{exports:{}}, require:()=>({})}; ctx.exports=ctx.module.exports;
  vm.createContext(ctx); vm.runInContext(code,ctx); return ctx;
}
const baseline = makeContext(), candidate = makeContext();
const probe = readFileSync(new URL('frontend_probe.mjs', import.meta.url),'utf8');
const fast = probe.slice(probe.indexOf('function fastMerge('),probe.indexOf('// Execute the prototype'));
vm.runInContext('var originalMerge=wbcMergeChronologicalMessages;'+fast+';wbcMergeChronologicalMessages=fastMerge;',candidate);
let seed=20260920;
function rand(n) { seed=(Math.imul(seed,1664525)+1013904223)>>>0; return seed%n; }
const date=i=>new Date(1700000000000+i*1000).toISOString();
function record(i) {
  const x={id:rand(5)===0?'':`id${rand(15)}`,role:rand(3)===0?'user':'assistant',content:`text${i}`,createdAt:date(rand(8)),timelineRevision:rand(5)};
  if(rand(8)===0) {delete x.createdAt;x.created_at=date(rand(8));}
  if(rand(10)===0) x.createdAt='invalid';
  if(rand(10)===0) {delete x.createdAt;delete x.created_at;}
  if(rand(5)===0) x.clientRequestId=`request${rand(3)}`;
  if(rand(5)===0) x.answerToQuestionId=`question${rand(3)}`;
  if(rand(3)===0) x.optimistic=!!rand(2);
  if(rand(4)===0) {x.activityCard=true;x.trace=[{toolCallId:`tool${rand(3)}`,status:rand(2)?'running':'completed'}];}
  x.status=rand(2)?'running':'completed';
  return x;
}
function freeze(value) {if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
function outcome(fn,args) {try{return {ok:true,value:fn(...args)}}catch(e){return {ok:false,error:e.name+': '+e.message}}}
function plain(v){return JSON.parse(JSON.stringify(v));}
let mergeChecks=0, projectionChecks=0, referenceChecks=0;
for(let i=0;i<12000;i++) {
  let a=Array.from({length:rand(20)},(_,j)=>record(j));
  let b=Array.from({length:rand(15)},(_,j)=>record(j));
  // Also exercise the fast path frequently with ordered ordinary messages.
  if(i%3===0) for(const list of [a,b]) {list.forEach((x,j)=>{delete x.clientRequestId;delete x.answerToQuestionId;x.createdAt=date(Math.floor(j/2));});}
  const args=freeze([a,b]);
  const before=JSON.stringify(args);
  const old=outcome(baseline.wbcMergeChronologicalMessages,args);
  const next=outcome(candidate.wbcMergeChronologicalMessages,args);
  assert.deepEqual(plain(next),plain(old),`merge ${i}`);
  assert.equal(JSON.stringify(args),before,`input mutation ${i}`);
  if(old.ok) for(let j=0;j<old.value.length;j++) {
    // Copies created for optimistic confirmation are compared structurally;
    // records returned by reference must retain the same identity.
    if(a.includes(old.value[j])||b.includes(old.value[j])) {assert.equal(next.value[j],old.value[j]);referenceChecks++;}
  }
  mergeChecks++;
  if(i<3000) {
    const runtime=freeze({userMessages:i%4===0?b.filter(x=>x.role==='user'):[],
      pendingQuestion:i%5===0?{id:'q'}:null,reconnecting:i%7===0,
      timeline:{runId:'run',status:i%2?'running':'completed',messages:b}});
    for(const name of ['wbcProjectTranscript','wbcProjectRuntimeTranscript']) {
      const input=name==='wbcProjectTranscript'?[a,runtime]:[a,b];
      assert.deepEqual(plain(outcome(candidate.module.exports[name],input)),plain(outcome(baseline.module.exports[name],input)),`${name} ${i}`);
      projectionChecks++;
    }
  }
}
// Arrays with holes can arise internally, although JSON transport makes them dense.
const sparse=Array(1);
const input=[sparse,[{id:'new',createdAt:date(1)}]];
const sparseResult={baseline:outcome(baseline.wbcMergeChronologicalMessages,input),candidate:outcome(candidate.wbcMergeChronologicalMessages,input)};
assert.equal(sparseResult.baseline.ok,true);
assert.equal(sparseResult.candidate.ok,false);
const report={mergeChecks,projectionChecks,referenceChecks,dense_fixture_mismatches:0,
  sparse_array_counterexample:sparseResult,
  conclusion:'Dense ordinary-data fixtures match; existing prototype is not equivalent for all accepted inputs. No production change authorized by this result.'};
writeFileSync(new URL('equivalence-recheck.json',import.meta.url),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
