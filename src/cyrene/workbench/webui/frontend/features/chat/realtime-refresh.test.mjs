import assert from 'node:assert/strict'
import test from 'node:test'
import vm from 'node:vm'
import {readFileSync} from 'node:fs'

function harness() {
  const requests=[],timers=[];
  const context={AbortController,DATA:{sessions:[],status:{model:'old'}},bumps:0,
    bumpData(){context.bumps++;},
    fetch(url,options) {return new Promise(resolve=>requests.push({url,options,resolve}));},
    window:{setTimeout(fn,delay){timers.push({fn,delay});return timers.length;},clearTimeout(){}}, UI_EVENTS:{close(){}},
  };
  const source=readFileSync(new URL('../../platform/data-store.jsx',import.meta.url),'utf8');
  vm.runInNewContext(source.slice(source.indexOf('let __sessionsRequestSeq'),source.indexOf('// ── Global SSE'))+
    '\nlet __eventsClosed=false, __eventReconnectTimer=null;\n'+source.slice(source.indexOf('function disposeDataStore()'),source.indexOf('function disposePageData()'))+'\nthis.dispose=disposeDataStore;this.refreshSessions=refreshSessions;this.refreshStatus=refreshStatus;this.schedule=scheduleRealtimeRefresh;',context);
  const respond=(index,payload,ok=true)=>requests[index].resolve({ok,json:async()=>payload});
  return {context,requests,timers,respond};
}

test('event bursts use one request with the same debounce and one coherent notification',async()=>{
  const {context,requests,timers,respond}=harness();
  context.schedule();context.schedule();assert.equal(timers.length,1);assert.equal(timers[0].delay,80);
  timers[0].fn();assert.equal(requests.length,1);
  assert.equal(requests[0].url,'/api/workbench/sessions?include_status=true');
  respond(0,{sessions:[{id:'a'}],status:{model:'new'}});
  await new Promise(setImmediate);
  assert.equal(context.DATA.sessions[0].id,'a');assert.equal(context.DATA.status.model,'new');assert.equal(context.bumps,1);
});

test('standalone session refresh cannot cancel a shared current status response',async()=>{
  const {context,requests,respond}=harness();
  const combined=context.refreshSessions(true),sessions=context.refreshSessions();
  assert.equal(requests[0].options.signal.aborted,false);
  respond(1,{sessions:[{id:'new'}]});await sessions;
  respond(0,{sessions:[{id:'old'}],status:{model:'current'}});await combined;
  assert.equal(context.DATA.sessions[0].id,'new');assert.equal(context.DATA.status.model,'current');
});

test('standalone status refresh cannot cancel shared current sessions or accept stale status',async()=>{
  const {context,requests,respond}=harness();
  const combined=context.refreshSessions(true),status=context.refreshStatus();
  assert.equal(requests[0].options.signal.aborted,false);
  respond(1,{model:'new'});await status;
  respond(0,{sessions:[{id:'a'}],status:{model:'old'}});await combined;
  assert.equal(context.DATA.sessions[0].id,'a');assert.equal(context.DATA.status.model,'new');
});

test('new combined refresh cancels superseded requests and rejects late responses',async()=>{
  const {context,requests,respond}=harness();
  const first=context.refreshSessions(true),second=context.refreshSessions(true);
  assert.equal(requests[0].options.signal.aborted,true);
  respond(1,{sessions:[{id:'new'}],status:{model:'new'}});await second;
  respond(0,{sessions:[{id:'old'}],status:{model:'old'}});await first;
  assert.equal(context.DATA.sessions[0].id,'new');assert.equal(context.DATA.status.model,'new');assert.equal(context.bumps,1);
});

test('status projection failure preserves successful sessions and previous status',async()=>{
  const {context,respond}=harness();const request=context.refreshSessions(true);
  respond(0,{sessions:[{id:'a'}],status_error:true},false);await request;
  assert.equal(context.DATA.sessions[0].id,'a');assert.equal(context.DATA.status.model,'old');
});


test('page disposal cancels shared and independent requests and remains idempotent',async()=>{
  const {context,requests,respond}=harness();
  const shared=context.refreshSessions(true);context.dispose();context.dispose();
  assert.equal(requests[0].options.signal.aborted,true);respond(0,{},false);await shared;
  const other=harness();const sessions=other.context.refreshSessions(),status=other.context.refreshStatus();
  other.context.dispose();assert.ok(other.requests.every(request=>request.options.signal.aborted));
  other.respond(0,{},false);other.respond(1,{},false);await Promise.all([sessions,status]);
});
