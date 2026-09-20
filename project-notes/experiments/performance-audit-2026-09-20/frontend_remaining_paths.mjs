import {readFileSync,writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import assert from 'node:assert/strict';
const root=new URL('../../../',import.meta.url),require=createRequire(new URL('src/cyrene/workbench/webui/package.json',root));
const source=readFileSync(new URL('src/cyrene/workbench/webui/frontend/features/chat/runtime-timeline.jsx',root),'utf8');
const module={exports:{}};new Function('require','module','exports',require('esbuild').transformSync(source,{loader:'jsx',format:'cjs'}).code)(()=>({}),module,module.exports);
const {wbcApplyTimeline,wbcProjectTranscript}=module.exports;
const med=a=>a.toSorted((a,b)=>a-b)[Math.floor(a.length/2)];
const rows=[];
for(const [n,m] of [[1000,40],[1000,1000]]){
 const item=(i,p)=>({id:p+i,role:'assistant',content:'body',createdAt:new Date(1700000000000+i*1000).toISOString()});
 const history=Array.from({length:n},(_,i)=>item(i,'h')),added=Array.from({length:m},(_,i)=>item(n+i,'a'));
 const current=[],saved=[],legacy=[];
 for(let i=0;i<9;i++){
  for(const [name,fn,values] of [['live',()=>wbcProjectTranscript(history,{timeline:{messages:added,status:'completed'}}),current],['saved',()=>module.exports.wbcMergeSavedAssistantMessages({messages:history},added).messages,saved],['legacy',()=>module.exports.wbcProjectRuntimeTranscript(history,added),legacy]]){
   const t=performance.now(),result=fn(),elapsed=performance.now()-t;assert.deepEqual(result,history.concat(added));if(i>=2)values.push(elapsed);
  }
 }
 rows.push({history:n,additions:m,live_ms:med(current),saved_ms:med(saved),legacy_ms:med(legacy),samples:{current,saved,legacy}});
}
writeFileSync(new URL('frontend-remaining-paths.json',import.meta.url),JSON.stringify({sha256:createHash('sha256').update(source).digest('hex'),rows},null,2)+'\n');
console.log(rows.map(({samples,...x})=>x));
