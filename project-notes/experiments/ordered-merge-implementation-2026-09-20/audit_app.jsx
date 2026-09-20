import React from 'react';
import {flushSync} from 'react-dom';
import {WbcMain} from './frontend/features/chat/conversation.jsx';
import {wbcApplyTimeline,wbcProjectTranscript} from './frontend/features/chat/runtime-timeline.jsx';

const at=i=>new Date(Date.parse('2026-09-20T10:00:00Z')+i*1000).toISOString();
const listeners=new Set();let active=null;
const engine={get:()=>active,snapshot:()=>({audit:active}),subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);},subscribeSummary(fn){listeners.add(fn);return()=>listeners.delete(fn);}};
function publish(value){active=value;for(const fn of listeners)fn(engine.snapshot());}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function fixture(n=80,m=40){
  const history=Array.from({length:n},(_,i)=>({id:'history'+i,role:i%7===0?'user':'assistant',content:i===1?'**Keep selected text** and `code`.':'History message '+i,createdAt:at(i),status:'completed'}));
  const live=Array.from({length:m},(_,i)=>({id:'live'+i,role:'assistant',content:'Live message '+i,createdAt:at(n+i),timelineOrder:i,timelineVersion:1,timelineRevision:1,status:i===m-1?'running':'completed'}));
  live[1]={...live[1],activityCard:true,intermediate:true,content:'',trace:[{kind:'tool',toolCallId:'call-a',text:'Read',preview:'fixture.txt',input:{path:'fixture.txt'},output:{text:'Complete result'},status:'completed',startedAt:at(81),endedAt:at(82)}]};
  return {chat:{id:'audit',projectId:'project',title:'Fixed conversation',messages:history,status:'running'},runtime:{progress:[],activities:[],segments:[],text:'',artifacts:[],chatId:'audit',startedAt:Date.now(),timeline:{runId:'run',revision:1,status:'running',messages:live},userMessages:[]}};
}

export function AuditApp(){
  const state=React.useRef(fixture());const [chat,setChat]=React.useState(state.current.chat);
  const [second,setSecond]=React.useState(false);const [clearIds,setClearIds]=React.useState([]);
  const actions=React.useRef([]);const root=React.useRef(null);
  const capture=React.useRef({nodes:[],focus:null,selection:'',scroll:0});
  const animationSamples=React.useRef([]);
  const record=(name,...args)=>actions.current.push({name,args});
  React.useEffect(()=>{
    window.cyrene={writeClipboardText:text=>record('copy',text)};
    publish(state.current.runtime);
    const observer=new MutationObserver(()=>{
      for(const a of root.current.getAnimations({subtree:true})){
        if(a.__checked)continue;a.__checked=true;
        const target=a.effect.target;
        const timing=a.effect.getTiming();
        const keyframes=a.effect.getKeyframes().map(({computedOffset,...frame})=>frame);
        const values=[];a.pause();
        for(const fraction of [0,.25,.5,.75,1]){
          a.currentTime=Number(timing.duration||0)*fraction;
          const style=getComputedStyle(target);
          values.push({fraction,opacity:style.opacity,transform:style.transform});
        }
        animationSamples.current.push({class:target.getAttribute('class'),timing,keyframes,values});
        a.currentTime=Number(timing.duration)||0;
      }
    });
    observer.observe(root.current,{subtree:true,childList:true,attributes:true});
    const before=()=>{
      const thread=root.current.querySelector('.wbc-thread');
      capture.current={nodes:Array.from(root.current.querySelectorAll('.wbc-thread-item')),focus:document.activeElement,
        selection:String(window.getSelection()),scroll:thread?.scrollTop||0};
    };
    window.audit={
      async step(command){
        if(command==='focus'){
          const input=root.current.querySelector('textarea');if(!input)throw Error('Missing composer');
          flushSync(()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Unsent draft 保留');input.dispatchEvent(new Event('input',{bubbles:true}));});
          input.focus();input.setSelectionRange(2,8);before();
          flushSync(()=>{const old=state.current.runtime,last=old.timeline.messages.at(-1),revision=old.timeline.revision+1;state.current.runtime=wbcApplyTimeline(old,{version:2,runId:'run',revision,status:'running',updates:[{id:last.id,baseRevision:last.timelineRevision,append:{content:' focus-preservation delta'},set:{timelineRevision:revision}}]});publish(state.current.runtime);});
          const result=this.snapshot(command);input.blur();window.getSelection().removeAllRanges();await pause(1400);return result;
        }
        before();
        const stateNow=state.current;
        if(command==='delta'){
          const old=stateNow.runtime,last=old.timeline.messages.at(-1),revision=old.timeline.revision+1;
          stateNow.runtime=wbcApplyTimeline(old,{version:2,runId:'run',revision,status:'running',updates:[{id:last.id,baseRevision:last.timelineRevision,append:{content:' **new stream text**'},set:{timelineRevision:revision}}]});publish(stateNow.runtime);
        }else if(command==='focus'){
          const input=root.current.querySelector('textarea');input?.focus();
          if(input){Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Unsent draft 保留');input.dispatchEvent(new Event('input',{bubbles:true}));input.setSelectionRange(2,8);}
        }else if(command==='select'){
          const bold=root.current.querySelector('.wbc-thread strong');
          const range=document.createRange();range.selectNodeContents(bold);window.getSelection().removeAllRanges();window.getSelection().addRange(range);
        }else if(command==='scroll'){
          const thread=root.current.querySelector('.wbc-thread');thread.scrollTop=200;thread.dispatchEvent(new Event('scroll',{bubbles:true}));
        }else if(command==='expand'){
          const target=root.current.querySelector('.wbc-activity-group-summary, .wbc-trace-summary');if(!target)throw Error('Missing activity disclosure');target.click();
        }else if(command==='copy'){
          const buttons=Array.from(root.current.querySelectorAll('button'));buttons.find(b=>/copy|复制/i.test(b.title))?.click();
        }else if(command==='edit'){
          const b=Array.from(root.current.querySelectorAll('button')).find(b=>/edit|编辑/i.test(b.getAttribute('aria-label')||b.title));if(!b)throw Error('Missing edit button');flushSync(()=>b.click());
        }else if(command==='edit-text'){
          const fields=root.current.querySelectorAll('textarea');
          const input=fields[0];
          if(input){Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Exact edit text 中文');input.dispatchEvent(new Event('input',{bubbles:true}));}
        }else if(command==='cancel-edit'){
          Array.from(root.current.querySelectorAll('button')).find(b=>/cancel|取消/i.test(b.textContent))?.click();
        }else if(command==='dark'||command==='light'){document.documentElement.dataset.theme=command;
        }else if(command==='zh'||command==='en'){window.CyreneUI.require('i18n').setLang(command);setChat(current=>({...current}));
        }else if(command==='split'){setSecond(true);
        }else if(command==='unsplit'){setSecond(false);
        }else if(command==='switch'){
          const next={...stateNow.chat,id:'other',messages:[{id:'other-user',role:'user',content:'Other conversation',createdAt:at(0)}]};setChat(next);publish(null);
        }else if(command==='switch-back'){setChat(stateNow.chat);publish(stateNow.runtime);
        }else if(command==='retry-animation'){setClearIds(['live39']);
        }else if(command==='reset'){
          state.current=fixture();setChat(state.current.chat);publish(state.current.runtime);setClearIds([]);setSecond(false);actions.current=[];
        }else if(command==='remove'){
          const old=stateNow.runtime;stateNow.runtime=wbcApplyTimeline(old,{version:2,runId:'run',revision:old.timeline.revision+1,status:'running',removedMessageIds:['live38'],messages:[]});publish(stateNow.runtime);
        }else if(command==='reconnect'||command==='resume'){
          stateNow.runtime={...stateNow.runtime,reconnecting:command==='reconnect'};publish(stateNow.runtime);
        }else if(command==='finish'||command==='cancel'||command==='failure'){
          const messages=stateNow.runtime.timeline.messages.map(x=>({...x,status:x.status==='running'?(command==='cancel'?'cancelled':command==='failure'?'failed':'completed'):x.status}));
          stateNow.chat={...stateNow.chat,messages:stateNow.chat.messages.concat(messages),status:'idle'};setChat(stateNow.chat);publish(null);
        }
        if(command==='edit')document.activeElement?.blur();
        await pause(1400);
        document.activeElement?.blur();
        return this.snapshot(command);
      },
      snapshot(command){
        const bounds=root.current.getBoundingClientRect(),thread=root.current.querySelector('.wbc-thread');
        const elements=Array.from(root.current.querySelectorAll('*'));
        const geometry=elements.map(el=>{const b=el.getBoundingClientRect(),s=getComputedStyle(el);return {
          tag:el.tagName,class:el.getAttribute('class'),x:Math.round((b.x-bounds.x)*10)/10,y:Math.round((b.y-bounds.y)*10)/10,
          w:Math.round(b.width*10)/10,h:Math.round(b.height*10)/10,color:s.color,background:s.backgroundColor,font:s.font,
          animation:s.animationName,duration:s.animationDuration,transition:s.transition,display:s.display};});
        const input=document.activeElement;
        const animations=animationSamples.current.splice(0);
        return {command,text:root.current.textContent,html:root.current.innerHTML,geometry,actions:actions.current,
          inputs:Array.from(root.current.querySelectorAll('textarea')).map(x=>({value:x.value,start:x.selectionStart,end:x.selectionEnd})),
          focus:input?.tagName,focusPreserved:capture.current.focus===input,selection:String(window.getSelection()),
          selectionPreserved:capture.current.selection===String(window.getSelection()),
          scroll:Math.round((thread?.scrollTop||0)*10)/10,priorScroll:Math.round(capture.current.scroll*10)/10,
          preserved:capture.current.nodes.filter(n=>n.isConnected).length,rows:root.current.querySelectorAll('.wbc-thread-item').length,
          animations,fastHits:window.__auditFastHits};
      },
      async performance(){
        const samples=[];
        for(const [n,m] of [[100,1],[1000,1],[1000,40],[1000,1000]]){
          const f=fixture(n,m);const values=[];
          for(let i=0;i<7;i++){const start=performance.now();wbcProjectTranscript(f.chat.messages,f.runtime);values.push(performance.now()-start);}
          samples.push({n,m,values});await pause(30);
        }
        return samples;
      }
    };
    window.auditReady=true;
    return()=>observer.disconnect();
  },[]);
  const common={project:{id:'project',name:'Synthetic project',workspace:'/tmp/cyrene-audit'},chat,runtimeEngine:engine,
    onSend:(...args)=>record('send',...args),onGuidance:(...args)=>record('guidance',...args),onInterrupt:()=>record('interrupt'),
    onAnswer:(...args)=>record('answer',...args),onRetryMessage:(...args)=>record('retry',...args),onEditMessage:(...args)=>record('edit',...args),
    onAskSelection:(...args)=>record('ask-selection',...args),onOpenFile:file=>record('open',file?.name),
    onRetryClearAnimationEnd:()=>setClearIds([]),retryClearingMessageIds:clearIds,browserVisible:false,browserWindowMode:'hidden'};
  return <div className="workbench-shell" ref={root} style={{height:'100vh',display:'flex'}}><div className="wbc-page" style={{height:'100%',width:second?'50%':'100%'}}><WbcMain {...common}/></div>{second?<div className="wbc-page" style={{height:'100%',width:'50%'}}><WbcMain {...common}/></div>:null}</div>;
}
