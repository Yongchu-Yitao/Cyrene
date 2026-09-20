import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {transformSync} from 'esbuild';
const source=readFileSync(new URL('./runtime-timeline.jsx',import.meta.url),'utf8');
function load(legacy){
 const input=legacy?source.replace('messages: wbcMergeProjectedMessages(current,','messages: wbcMergeChronologicalMessages(current,').replace('return wbcMergeProjectedMessages(durable, runtimeMessages.filter','return wbcMergeChronologicalMessages(durable, runtimeMessages.filter'):source;
 const module={exports:{}};new Function('require','module','exports',transformSync(input,{loader:'jsx',format:'cjs'}).code)(()=>({}),module,module.exports);return module.exports;
}
const current=load(false),old=load(true);
test('saved and legacy projections preserve updates, correlation, timestamps and immutable inputs',()=>{
 let seed=8729;const random=n=>(seed=(Math.imul(seed,1664525)+1013904223)>>>0)%n;
 for(let round=0;round<2000;round++){
  const create=(i,p)=>({id:p+i,role:'assistant',content:'text'+i,createdAt:new Date(1700000000000+i*1000).toISOString()});
  const history=Array.from({length:100},(_,i)=>create(i,'h'));
  const additions=Array.from({length:50},(_,i)=>create(i+random(2),'a'));
  if(round%3===0)additions[0].id='h1';
  if(round%5===0)additions.reverse();
  if(round%7===0)additions[0].createdAt='invalid';
  if(round%11===0)Object.assign(additions[0],{role:'user',clientRequestId:'request',optimistic:true});
  for(const m of [...history,...additions])Object.freeze(m);Object.freeze(history);Object.freeze(additions);
  const chat=Object.freeze({id:'c',messages:history,status:'running'});
  assert.deepEqual(current.wbcMergeSavedAssistantMessages(chat,additions),old.wbcMergeSavedAssistantMessages(chat,additions));
  assert.deepEqual(current.wbcProjectRuntimeTranscript(history,additions),old.wbcProjectRuntimeTranscript(history,additions));
 }
});
