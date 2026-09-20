import {readFileSync,writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {performance} from 'node:perf_hooks';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const root=new URL('../../../',import.meta.url), here=new URL('.',import.meta.url);
const require=createRequire(new URL('src/cyrene/workbench/webui/package.json',root));
const {transformSync}=require('esbuild');
const paths=[new URL('baseline-runtime-timeline.jsx',here),new URL('src/cyrene/workbench/webui/frontend/features/chat/runtime-timeline.jsx',root)];
const sources=paths.map(p=>readFileSync(p,'utf8'));
const modules=sources.map(source=>{const module={exports:{}};new Function('require','module','exports',transformSync(source,{loader:'jsx',format:'cjs'}).code)(()=>({}),module,module.exports);return module.exports;});
const rows=[];
for(const [n,m] of [[100,1],[1000,1],[1000,40],[1000,1000],[5000,40],[5000,1000]]){
 const item=(i,prefix)=>({id:prefix+i,role:'assistant',content:'message '+i,createdAt:new Date(1700000000000+i*1000).toISOString(),timelineOrder:i,timelineRevision:1});
 const history=Array.from({length:n},(_,i)=>item(i,'h'));
 const runtime={timeline:{runId:'r',revision:1,status:'running',messages:Array.from({length:m},(_,i)=>item(n+i,'l'))}};
 assert.deepEqual(modules[0].wbcProjectTranscript(history,runtime),modules[1].wbcProjectTranscript(history,runtime));
 const samples=[[],[]];
 for(let round=0;round<24;round++)for(const v of round%2?[1,0]:[0,1]){
  const start=performance.now();modules[v].wbcProjectTranscript(history,runtime);const ms=performance.now()-start;
  if(round>=3)samples[v].push(ms);
 }
 const median=v=>v.toSorted((a,b)=>a-b)[Math.floor(v.length/2)];
 rows.push({n,m,baseline_ms:median(samples[0]),candidate_ms:median(samples[1]),speedup:median(samples[0])/median(samples[1]),samples});
}
const result={node:process.version,hashes:sources.map(s=>createHash('sha256').update(s).digest('hex')),rows};
writeFileSync(new URL('benchmark.json',here),JSON.stringify(result,null,2)+'\n');
console.log(rows.map(({samples,...r})=>r));
