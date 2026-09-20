import { WbcTranscript } from '../../../src/cyrene/workbench/webui/frontend/features/chat/messages.jsx';
import { wbcProjectTranscript, wbcApplyTimeline } from '../../../src/cyrene/workbench/webui/frontend/features/chat/runtime-timeline.jsx';

const root=ReactDOM.createRoot(document.getElementById('transcript'));
// Frame callbacks are throttled by this host; measure synchronous layout directly.
const nextPaint=()=>new Promise(resolve=>setTimeout(resolve,30));
const median=values=>values.slice().sort((a,b)=>a-b)[Math.floor(values.length/2)];
let longTasks=[];
new PerformanceObserver(list=>longTasks.push(...list.getEntries().map(x=>({start:x.startTime,duration:x.duration})))).observe({type:'longtask',buffered:true});
function fixture(n,m) {
  const text='Fixed benchmark paragraph with **bold text** and `inline code`.\n\nSecond paragraph.';
  const messages=Array.from({length:n},(_,i)=>({id:'old'+i,role:'assistant',content:text,createdAt:'2026-09-19T00:00:00Z'}));
  const live=Array.from({length:m},(_,i)=>({id:'live'+i,role:'assistant',content:text,timelineOrder:i,timelineRevision:1,status:'completed',createdAt:'2026-09-20T00:00:00Z'}));
  return {messages,runtime:{timeline:{version:2,runId:'probe',revision:1,messages:live,status:'completed'}}};
}
async function execute() {
  document.getElementById('start').disabled=true;
  document.getElementById('status').textContent='Running';
  const results=[];
  try {
    for(const [n,m] of [[100,1],[100,100],[1000,1],[1000,1000]]) {
      const samples=[];
      for(let repetition=0;repetition<3;repetition++) {
        ReactDOM.flushSync(()=>root.render(null));
        await nextPaint();
        const {messages,runtime}=fixture(n,m);
        const start=performance.now();
        const projectionStart=performance.now();
        const projected=wbcProjectTranscript(messages,runtime);
        const projectionMs=performance.now()-projectionStart;
        const mountStart=performance.now();
        ReactDOM.flushSync(()=>root.render(<WbcTranscript messages={messages} runtime={runtime} chatId="fixture"/>));
        const commitMs=performance.now()-mountStart;
        document.getElementById('transcript').scrollHeight;
        const paintMs=performance.now()-mountStart;
        await nextPaint();
        let updateSamples=[];
        let current=runtime;
        for(let i=0;i<5;i++) {
          const revision=i+2;
          current=wbcApplyTimeline(current,{version:2,runId:'probe',revision,status:'completed',updates:[{id:'live'+(m-1),baseRevision:revision-1,append:{content:' delta'+i},set:{timelineRevision:revision}}]});
          const t=performance.now();
          ReactDOM.flushSync(()=>root.render(<WbcTranscript messages={messages} runtime={current} chatId="fixture"/>));
          const updateCommitMs=performance.now()-t;
          document.getElementById('transcript').scrollHeight;
          updateSamples.push({commitMs:updateCommitMs,layoutMs:performance.now()-t});
          await nextPaint();
        }
        const items=document.querySelectorAll('[data-wbc-thread-item]').length;
        const text=document.getElementById('transcript').textContent;
        if(projected.length!==n+m || items!==n+m || !text.includes('delta4')) throw Error('Transcript completeness failed: '+JSON.stringify({items,projected:projected.length,n,m}));
        samples.push({projectionMs,commitMs,layoutMs:paintMs,updateSamples,domNodes:document.getElementById('transcript').querySelectorAll('*').length,
          items,longTasks:longTasks.filter(x=>x.start>=start),checks:true});
      }
      results.push({history:n,active:m,samples,projectionMs:median(samples.map(x=>x.projectionMs)),
        mountCommitMs:median(samples.map(x=>x.commitMs)),mountLayoutMs:median(samples.map(x=>x.layoutMs)),
        updateCommitMs:median(samples.flatMap(x=>x.updateSamples.map(y=>y.commitMs))),
        updateLayoutMs:median(samples.flatMap(x=>x.updateSamples.map(y=>y.layoutMs)))});
      document.getElementById('status').textContent='Completed '+n+' / '+m;
    }
    // Exercise the real live-text buffering path with an active response.
    ReactDOM.flushSync(()=>root.render(null)); await nextPaint();
    const streaming=fixture(100,1);streaming.runtime.timeline.status='running';
    streaming.runtime.timeline.messages[0].status='running';
    streaming.runtime.timeline.messages[0].content='';
    let stream=streaming.runtime;
    const began=performance.now();let maxTickGap=0,lastTick=began;
    const timer=setInterval(()=>{const now=performance.now();maxTickGap=Math.max(maxTickGap,now-lastTick);lastTick=now;},10);
    for(let i=0;i<100;i++) {
      stream=wbcApplyTimeline(stream,{version:2,runId:'probe',revision:i+2,status:'running',updates:[{id:'live0',baseRevision:i+1,append:{content:'token'+i+' '},set:{timelineRevision:i+2}}]});
      ReactDOM.flushSync(()=>root.render(<WbcTranscript messages={streaming.messages} runtime={stream} chatId="fixture"/>));
      await new Promise(resolve=>setTimeout(resolve,10));
    }
    stream=wbcApplyTimeline(stream,{version:2,runId:'probe',revision:102,status:'completed',updates:[{id:'live0',baseRevision:101,set:{timelineRevision:102,status:'completed'}}]});
    ReactDOM.flushSync(()=>root.render(<WbcTranscript messages={streaming.messages} runtime={stream} chatId="fixture"/>));
    await nextPaint();clearInterval(timer);
    const finalText=document.getElementById('transcript').textContent;
    if(!Array.from({length:100},(_,i)=>'token'+i+' ').every(x=>finalText.includes(x))) throw Error('Missing streaming tokens');
    const output={results,streaming:{chunks:100,intervalMs:10,elapsedMs:performance.now()-began,maxTickGap,checks:true},userAgent:navigator.userAgent};
    document.getElementById('results').textContent=JSON.stringify(output,null,2);
    await fetch('/results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(output)});
    document.getElementById('status').textContent='PASS';
  } catch(error) {
    document.getElementById('status').textContent='FAIL: '+error.message;
    document.getElementById('results').textContent=error.stack;
    console.error(error);
  } finally {document.getElementById('start').disabled=false;}
}
document.getElementById('start').onclick=execute;
document.getElementById('status').textContent='Ready';
