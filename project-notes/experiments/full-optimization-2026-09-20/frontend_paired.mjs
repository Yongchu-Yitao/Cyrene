import {readFileSync,writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const root=new URL('../../../',import.meta.url),require=createRequire(new URL('src/cyrene/workbench/webui/package.json',root));
const relative='src/cyrene/workbench/webui/frontend/features/chat/runtime-timeline.jsx';
const modules={};
for(const name of ['baseline','candidate']){
 const source=readFileSync(name==='baseline'?new URL('baseline/'+relative,import.meta.url):new URL(relative,root),'utf8');
 const module={exports:{}};new Function('require','module','exports',require('esbuild').transformSync(source,{loader:'jsx',format:'cjs'}).code)(()=>({}),module,module.exports);modules[name]=module.exports;
}
const item=(i,p)=>({id:p+i,role:'assistant',content:'body',createdAt:new Date(1700000000000+i*1000).toISOString()});
const history=Array.from({length:1000},(_,i)=>item(i,'h')),added=Array.from({length:1000},(_,i)=>item(1000+i,'a'));
const rows=[];
for(let round=0;round<9;round++)for(const name of round%2?['candidate','baseline']:['baseline','candidate']){
 const m=modules[name];
 for(const [path,fn] of [['saved',()=>m.wbcMergeSavedAssistantMessages({messages:history},added).messages],['legacy',()=>m.wbcProjectRuntimeTranscript(history,added)]]){
  const start=performance.now(),result=fn(),ms=performance.now()-start;assert.deepEqual(result,history.concat(added));if(round>1)rows.push({round,name,path,ms});
 }
}
writeFileSync(new URL('frontend-paired.json',import.meta.url),JSON.stringify(rows,null,2)+'\n');
