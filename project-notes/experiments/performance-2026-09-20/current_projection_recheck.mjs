import {createRequire} from 'node:module';
import {readFileSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../../../src/cyrene/workbench/webui/package.json',import.meta.url));
const {transformSync}=require('esbuild');
const oldSource=readFileSync('/tmp/cyrene-full-audit-20260920-b/frontend/features/chat/runtime-timeline.jsx','utf8');
const currentSource=readFileSync(new URL('current-runtime-timeline.jsx',import.meta.url),'utf8');
function compile(source){let hits=0;const module={exports:{}};const text=source.replace('return ordered === null ? wbcMergeChronologicalMessages(messages, additions) : ordered;','if(ordered!==null) hit();return ordered === null ? wbcMergeChronologicalMessages(messages, additions) : ordered;');new Function('require','module','exports','hit',transformSync(text,{loader:'jsx',format:'cjs'}).code)(()=>({}),module,module.exports,()=>hits++);return {api:module.exports,hits:()=>hits};}
const old=compile(oldSource),current=compile(currentSource);let seed=82914,checks=0;const rand=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed%n};const at=n=>new Date(1700000000000+n*1000).toISOString();
for(let i=0;i<2000;i++){
 const history=Array.from({length:80+rand(120)},(_,j)=>({id:'h'+j,role:j%7?'assistant':'user',content:'历史 '+j,createdAt:at(j),status:'completed'}));
 const records=Array.from({length:40+rand(40)},(_,j)=>({id:i%4===0&&j===4?'r3':'r'+j,role:'assistant',content:'实时 🦊 '+j,createdAt:at(180+(i%5===0?0:j)),status:j===0?'running':'completed',timelineRevision:1}));
 if(i%6===0)records[5].createdAt='invalid';if(i%7===0)records[9].role='user';if(i%8===0)records.reverse();if(i%9===0)records[10].clientRequestId='request';if(i%10===0)history[0].createdAt='invalid';
 const runtime={timeline:{runId:'r',messages:records,status:'running',removedMessageIds:i%3===0?['h1','r2']:[]},userMessages:[]};
 for(const list of [history,records]){for(const x of list)Object.freeze(x);Object.freeze(list);}Object.freeze(runtime.timeline);Object.freeze(runtime);
 const before=JSON.stringify([history,runtime]);assert.deepEqual(current.api.wbcProjectTranscript(history,runtime),old.api.wbcProjectTranscript(history,runtime));assert.equal(JSON.stringify([history,runtime]),before);checks++;
}
assert.ok(current.hits()>200);const result={checks,fastPathHits:current.hits(),scope:'current working-tree implementation pinned to current-runtime-timeline.jsx versus original snapshot; same realm; immutable inputs; 2000 long-transcript projections'};writeFileSync(new URL('current-projection-recheck.json',import.meta.url),JSON.stringify(result,null,2));console.log(result);
