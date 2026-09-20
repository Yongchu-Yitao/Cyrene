import React from 'react';
import {WbcConversationMessages} from './frontend/features/chat/conversation.jsx';
import {wbcApplyTimeline} from './frontend/features/chat/runtime-timeline.jsx';

const at=i=>new Date(Date.parse('2026-09-20T10:00:00Z')+i*1000).toISOString();
const MARKDOWN='## 显示一致性 / Display\n\n**加粗**、*斜体*、中文与 emoji 🦊。\n\n- 项目一\n- 项目二\n\n| 字段 | 值 |\n|---|---|\n| 顺序 | 保持 |\n\n```js\nconst answer = 42;\n```\n\n[本地附件](#attachment)';
const subscribers=new Set();
let currentRuntime=null;
const engine={get:()=>currentRuntime,snapshot:()=>({audit:currentRuntime}),subscribe(fn){subscribers.add(fn);return()=>subscribers.delete(fn);},subscribeSummary(fn){return this.subscribe(fn);}};
function publish(value){currentRuntime=value;for(const fn of subscribers)fn(engine.snapshot());}
function fixture(name){
  const history=Array.from({length:80},(_,i)=>({id:'history'+i,role:i%7===0?'user':'assistant',content:i===0?'比较消息顺序、复制、展开和流式显示。':'历史消息 '+i,createdAt:at(i),status:'completed'}));
  const live=Array.from({length:40},(_,i)=>({id:'live'+i,role:'assistant',content:i===0?MARKDOWN:'运行消息 '+i,createdAt:at(80+i),timelineOrder:i,timelineVersion:1,timelineRevision:1,status:i===39?'running':'completed'}));
  live[1]={...live[1],activityCard:true,intermediate:true,content:'',reasoning:'检查文件与工具结果。',reasoningActive:false,trace:[{kind:'tool',toolCallId:'call-a',text:'Read',preview:'fixture.txt',input:{path:'fixture.txt'},output:{text:'完整结果'},status:'completed',startedAt:at(81),endedAt:at(82)}]};
  live[2]={...live[2],attachments:[{id:'attachment',name:'fixture.txt',url:'/fixture.txt',content_type:'text/plain',size:42}]};
  if(name==='same-time')live.forEach(x=>{x.createdAt=at(80)});
  if(name==='invalid-date')history[79].createdAt='invalid';
  if(name==='question')live[3]={id:'answer-local',role:'user',content:'确认继续',createdAt:at(83),answerToQuestionId:'q',optimistic:true};
  const chat={id:'audit',messages:history,status:'running'};
  if(name==='question')chat.pendingQuestion={id:'q2',kind:'user_question',text:'选择一个选项',options:['继续','暂停']};
  let runtime={chatId:'audit',startedAt:Date.now(),timeline:{runId:'run',revision:1,status:'running',messages:live},userMessages:[]};
  if(name==='reconnect')runtime.reconnecting=true;
  return {chat,runtime};
}

export function AuditApp(){
  const [chat,setChat]=React.useState(()=>fixture('normal').chat);
  const [actions,setActions]=React.useState([]);
  const ref=React.useRef(null);
  const previousNodes=React.useRef(new Map());
  const [step,setStep]=React.useState(0);
  const runState=React.useRef(fixture('normal'));
  const action=(name,...args)=>setActions(old=>old.concat({name,args}));
  React.useEffect(()=>{
    window.cyrene={writeClipboardText:text=>action('copy',text)};
    function receive(event){
      if(event.origin!==location.origin||event.data?.kind!=='audit-control')return;
      const data=event.data;
      if(data.command==='scenario'){
        runState.current=fixture(data.value);setChat(runState.current.chat);publish(runState.current.runtime);setActions([]);
      }else if(data.command==='delta'){
        const old=runState.current.runtime;const record=old.timeline.messages.find(x=>x.id==='live39');
        const revision=old.timeline.revision+1;
        runState.current.runtime=wbcApplyTimeline(old,{version:2,runId:'run',revision,status:'running',updates:[{id:'live39',baseRevision:record.timelineRevision,append:{content:'\n\n新增 **流式文字** 🦊'},set:{timelineRevision:revision}}]});publish(runState.current.runtime);
      }else if(data.command==='cleanup'){
        const old=runState.current.runtime;runState.current.runtime=wbcApplyTimeline(old,{version:2,runId:'run',revision:old.timeline.revision+1,status:'running',removedMessageIds:['live38'],messages:[]});publish(runState.current.runtime);
      }else if(data.command==='finish'||data.command==='cancel'){
        const old=runState.current.runtime;
        const messages=old.timeline.messages.map(x=>({...x,status:data.command==='cancel'&&x.status==='running'?'cancelled':'completed'}));
        const next={...runState.current.chat,messages:runState.current.chat.messages.concat(messages),status:'idle'};
        runState.current.chat=next;setChat(next);publish(null);
      }else if(data.command==='theme'){document.documentElement.dataset.theme=data.value;
      }else if(data.command==='language'){window.CyreneUI.require('i18n').setLang(data.value);}
      setStep(n=>n+1);
    }
    window.addEventListener('message',receive);publish(runState.current.runtime);
    return()=>window.removeEventListener('message',receive);
  },[]);
  React.useEffect(()=>{
    let timeout;
    const report=()=>{
      const root=ref.current;if(!root)return;
      const bounds=root.getBoundingClientRect();
      const elements=Array.from(root.querySelectorAll('*'));
      const geometry=elements.map(x=>{const b=x.getBoundingClientRect(),s=getComputedStyle(x);return {tag:x.tagName,class:x.getAttribute('class')||'',x:Math.round((b.x-bounds.x)*100)/100,y:Math.round((b.y-bounds.y)*100)/100,w:Math.round(b.width*100)/100,h:Math.round(b.height*100)/100,font:s.fontSize,color:s.color,background:s.backgroundColor,border:s.border,shadow:s.boxShadow,display:s.display};});
      const rows=Array.from(root.querySelectorAll('.wbc-thread-item'));
      const preserved=rows.filter(x=>previousNodes.current.get(x.textContent.slice(0,35))===x).length;
      previousNodes.current=new Map(rows.map(x=>[x.textContent.slice(0,35),x]));
      parent.postMessage({kind:'audit-result',variant:__AUDIT_VARIANT__,step,html:root.innerHTML,text:root.textContent,geometry,rows:rows.length,preserved,fastHits:window.__auditFastHits,actions},location.origin);
    };
    const schedule=()=>{clearTimeout(timeout);timeout=setTimeout(report,900);};
    const observer=new MutationObserver(schedule);observer.observe(ref.current,{subtree:true,childList:true,attributes:true,characterData:true});
    document.fonts.ready.then(schedule);schedule();
    const resize=new ResizeObserver(schedule);resize.observe(ref.current);
    return()=>{observer.disconnect();resize.disconnect();clearTimeout(timeout);};
  },[step,actions]);
  return <><div className="workbench-shell"><div className="wbc-page"><div className="audit-thread" ref={ref}><WbcConversationMessages chat={chat} composerChat={chat} runtimeEngine={engine} onOpenFile={file=>action('open-file',file?.name||file?.url)} onRetryMessage={message=>action('retry',message?.id||message)} onEditMessage={message=>action('edit',message?.id||message)} onAnswer={(...args)=>action('answer',...args)}/></div></div></div><output id="actions">{JSON.stringify(actions)}</output></>;
}
