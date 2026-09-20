import assert from 'node:assert/strict';
import test from 'node:test';
import {snapshotStaticMessage,equalAssistantProps} from './message-memo.mjs';

test('static snapshots detect in-place scalar edits and never observe getters',()=>{
 const message={id:'a',role:'assistant',content:'before',status:'completed'};
 const old=snapshotStaticMessage(message);message.content='after';const next=snapshotStaticMessage(message);
 assert.equal(old.content,'before');
 assert.equal(equalAssistantProps({msg:old,staticMessage:true},{msg:next,staticMessage:true}),false);
 let reads=0;Object.defineProperty(message,'usage',{enumerable:true,get(){reads++;return {};}});
 assert.equal(snapshotStaticMessage(message),null);assert.equal(reads,0);
});
test('live, nested, special and inherited messages always render normally',()=>{
 for(const message of [{role:'assistant',status:'running'},{role:'assistant',attachments:[]},{role:'assistant',usage:{nested:{}}},{role:'assistant',kind:'goal_milestone'},{role:'user'},Object.create({role:'assistant'})])assert.equal(snapshotStaticMessage(message),null);
 assert.equal(equalAssistantProps({staticMessage:false},{staticMessage:false}),false);
});
test('memoization preserves callback, calendar, timezone and every scalar field dependency',()=>{
 const a={msg:{id:'a',role:'assistant',content:'same'},staticMessage:true,calendarDay:'a',zoneOffset:0,onRetryMessage:()=>{}};
 assert.equal(equalAssistantProps(a,{...a,msg:{...a.msg}}),true);
 for(const change of [{calendarDay:'b'},{zoneOffset:60},{onRetryMessage:()=>{}},{liveRuntime:{}},{chatId:'new'},{msg:{...a.msg,processingDurationMs:12}},{msg:{...a.msg,status:'failed'}}])assert.equal(equalAssistantProps(a,{...a,...change}),false);
});

test('production usage and model metadata are isolated snapshots and can be memoized',()=>{
 const message={id:'a',role:'assistant',content:'body',usage:{prompt_tokens:100,completion_tokens:50,total_tokens:150},latestRequestUsage:{total_tokens:150},modelIdentity:{provider:'test',model:'test-model'}};
 const props=msg=>({msg,staticMessage:true});
 const first=snapshotStaticMessage(message);
 assert.ok(first);
 assert.equal(equalAssistantProps(props(first),props(snapshotStaticMessage(message))),true);
 for(const [field,key,value] of [['usage','total_tokens',151],['latestRequestUsage','total_tokens',152],['modelIdentity','model','new-model']]){
  const before=snapshotStaticMessage(message);message[field][key]=value;
  assert.notEqual(before[field][key],value);
  assert.equal(equalAssistantProps(props(before),props(snapshotStaticMessage(message))),false);
 }
 const before=snapshotStaticMessage(message);delete message.usage.prompt_tokens;
 assert.equal(equalAssistantProps(props(before),props(snapshotStaticMessage(message))),false);
 assert.equal(equalAssistantProps(props(before),props(snapshotStaticMessage({...message,usage:null}))),false);
});
test('metadata getters, arrays and richer records retain the ordinary render path',()=>{
 let reads=0;const usage={};Object.defineProperty(usage,'total_tokens',{enumerable:true,get(){reads++;return 1;}});
 for(const metadata of [usage,[],{details:{cached_tokens:1}},Object.create({total_tokens:1})])
  assert.equal(snapshotStaticMessage({role:'assistant',content:'body',usage:metadata}),null);
 assert.equal(reads,0);
 const data=JSON.parse('{"role":"assistant","usage":{"__proto__":10,"total_tokens":1}}');
 const snapshot=snapshotStaticMessage(data);assert.equal(snapshot.usage.__proto__,10);
 assert.equal(Object.getPrototypeOf(snapshot.usage),Object.prototype);
});
