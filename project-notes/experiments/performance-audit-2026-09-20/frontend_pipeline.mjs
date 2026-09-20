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
for(const m of [1,100,1000,5000]){
 const msg=(i,p)=>({id:p+i,role:'assistant',content:'body',createdAt:new Date(1700000000000+i*1000).toISOString(),timelineOrder:i,timelineRevision:1,status:'completed'});
 const history=Array.from({length:1000},(_,i)=>msg(i,'h'));
 let state={timeline:{runId:'r',revision:1,status:'completed',messages:Array.from({length:m},(_,i)=>msg(1000+i,'l'))}};
 const stable=state.timeline.messages[0],before=JSON.stringify(stable),apply=[],project=[];
 for(let i=0;i<53;i++){
  const prior=state.timeline.messages.at(-1),revision=i+2,t=performance.now();
  state=wbcApplyTimeline(state,{version:2,runId:'r',revision,status:'completed',updates:[{id:prior.id,baseRevision:prior.timelineRevision,append:{content:'x'},set:{timelineRevision:revision}}]});
  const a=performance.now(),output=wbcProjectTranscript(history,state),b=performance.now();
  assert.equal(output.length,1000+m);assert.equal(output.at(-1).content,'body'+'x'.repeat(i+1));
  if(i>=3){apply.push(a-t);project.push(b-a);}
 }
 assert.equal(JSON.stringify(stable),before);
 rows.push({history:1000,active:m,apply_ms:med(apply),project_ms:med(project),samples:{apply,project},unchanged_record_identity:m===1?null:state.timeline.messages[0]===stable});
}
writeFileSync(new URL('frontend-pipeline.json',import.meta.url),JSON.stringify({sha256:createHash('sha256').update(source).digest('hex'),node:process.version,rows},null,2)+'\n');
console.log(rows.map(({samples,...x})=>x));
