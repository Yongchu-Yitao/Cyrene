// Run the current production projection, never an optimized prototype.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const web = new URL('../../../src/cyrene/workbench/webui/', import.meta.url);
const require = createRequire(new URL('package.json', web));
const {transformSync} = require('esbuild');
const source = readFileSync(new URL('frontend/features/chat/runtime-timeline.jsx', web), 'utf8');
const {code} = transformSync(source, {loader:'jsx', format:'cjs'});
const context = {module:{exports:{}}, require:()=>({})};
context.exports = context.module.exports;
vm.runInNewContext(code, context);
const {wbcProjectTranscript} = context.module.exports;
function measure(fn) {
  fn();
  const samples=[];
  for(let i=0;i<5;i++) {
    const cpu=process.cpuUsage(), wall=performance.now();
    fn();
    const wall_ms=performance.now()-wall, usage=process.cpuUsage(cpu);
    samples.push({wall_ms,cpu_ms:(usage.user+usage.system)/1000});
  }
  const median=key=>samples.map(x=>x[key]).sort((a,b)=>a-b)[2];
  return {wall_ms:median('wall_ms'),cpu_ms:median('cpu_ms'),samples};
}
const results={};
for(const [history,active] of [[100,100],[1000,1],[1000,1000]]) {
  const durable=Array.from({length:history},(_,i)=>({id:`old${i}`,role:'assistant',content:'x'.repeat(2048),createdAt:'2026-09-19T00:00:00Z'}));
  const live=Array.from({length:active},(_,i)=>({id:`live${i}`,role:'assistant',content:'x',timelineOrder:i,timelineRevision:1,status:'completed',createdAt:'2026-09-20T00:00:00Z'}));
  const runtime={timeline:{runId:'probe',revision:1,messages:live,status:'running'}};
  const expected=JSON.stringify(wbcProjectTranscript(durable,runtime));
  const projected=JSON.parse(expected);
  // Production also emits a running-status record; validate actual messages.
  const byId=new Map(projected.map(message=>[message.id,message]));
  for(const message of [...durable,...live]) {
    assert.equal(byId.get(message.id)?.content,message.content);
  }
  results[`frontend_${history}_${active}`]=measure(()=>{
    // Projection only in the timing; full output validation follows below.
    wbcProjectTranscript(durable,runtime);
  });
  assert.equal(JSON.stringify(wbcProjectTranscript(durable,runtime)),expected);
  results[`frontend_${history}_${active}`].checks_passed=true;
}
console.log(JSON.stringify(results));
