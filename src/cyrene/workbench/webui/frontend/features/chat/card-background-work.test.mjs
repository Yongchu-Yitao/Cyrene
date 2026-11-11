import test from 'node:test';
import assert from 'node:assert/strict';
import { observeHomeCards, observeCardVisibility } from './card-background-work.mjs';
import { harness } from './hook-harness.test.mjs';
import { initialCardValues } from './assistant-card-model.mjs';

const settle = async () => {for (let i=0;i<15;i++) await Promise.resolve();};
function targetFixture() {
  const target = new EventTarget(), doc = new EventTarget(), timers = new Map();
  let id=0;
  target.document=doc;doc.hidden=false;
  target.setTimeout=(fn,delay)=>{timers.set(++id,{fn,delay});return id;};
  target.clearTimeout=id=>timers.delete(id);
  return {target,doc,timers};
}

test('one home request supplies all cards, events coalesce, hidden pages stop timers', async () => {
  const {target,doc,timers}=targetFixture();
  const requests=[],cards=[];
  const stop=observeHomeCards({target,request:()=>new Promise(resolve=>requests.push(resolve)),onCards:value=>cards.push(value),onError:()=>{}});
  target.dispatchEvent(new Event('cyrene:card-updated'));
  target.dispatchEvent(new Event('cyrene:card-updated'));
  assert.equal(requests.length,1);
  const rows=['results','overview','resume'].map((kind,i)=>({card_id:String(i),status:'active',spec:{home_kind:kind}}));
  requests[0]({cards:rows});await settle();
  assert.deepEqual(cards[0].map(c=>c.spec.home_kind),['overview','resume','results']);
  assert.equal([...timers.values()][0].delay,0); // a mutation during the request needs a fresh read
  doc.hidden=true;doc.dispatchEvent(new Event('visibilitychange'));assert.equal(timers.size,0);
  doc.hidden=false;doc.dispatchEvent(new Event('visibilitychange'));
  requests[1]({cards:rows});await settle();
  assert.equal([...timers.values()][0].delay,3000);
  stop();assert.equal(timers.size,0);
  target.dispatchEvent(new Event('cyrene:card-updated'));assert.equal(requests.length,2);
});

test('disabled card plugin stops polling; disposed responses cannot replace data', async () => {
  const {target,timers}=targetFixture();let calls=0;
  const stop=observeHomeCards({target,request:async()=>{calls++;throw Object.assign(Error(),{status:404});},onCards:()=>assert.fail(),onError:()=>assert.fail()});
  await settle();assert.equal(timers.size,0);
  target.dispatchEvent(new Event('cyrene:card-updated'));await settle();assert.equal(calls,1);stop();
  let resolve;
  const cancel=observeHomeCards({target,request:()=>new Promise(r=>resolve=r),onCards:()=>assert.fail(),onError:()=>assert.fail()});
  cancel();resolve({cards:[]});await settle();assert.equal(timers.size,0);
});

test('animation pauses only when invisible and resumes without replacing the element', () => {
  const {doc}=targetFixture();let callback,disconnected=false,paused=false;
  doc.defaultView={IntersectionObserver:class {constructor(fn){callback=fn;}observe(){}disconnect(){disconnected=true;}}};
  const element={ownerDocument:doc,toggleAttribute:(_,value)=>paused=value,removeAttribute:()=>paused=false};
  const stop=observeCardVisibility(element);
  assert.equal(paused,false);
  callback([{isIntersecting:false}]);assert.equal(paused,true);
  doc.hidden=true;callback([{isIntersecting:true}]);assert.equal(paused,true);
  doc.hidden=false;doc.dispatchEvent(new Event('visibilitychange'));assert.equal(paused,false);
  stop();assert.equal(disconnected,true);
});

test('home cards accept parent refresh without requests and preserve unsaved draft fields', () => {
  const react={};
  const h=harness('./assistant-cards.jsx',{initialCardValues,
    useState:(...args)=>react.useState(...args),useRef:(...args)=>react.useRef(...args),useEffect:(...args)=>react.useEffect(...args)});
  let network=0;
  h.context.fetch=()=>{network++;throw Error('unexpected per-card request');};
  h.context.setInterval=()=>{throw Error('unexpected per-card poll');};
  h.context.clearInterval=()=>{};
  h.context.setTimeout=h.context.window.setTimeout;
  h.context.clearTimeout=h.context.window.clearTimeout;
  let card={card_id:'c',revision:1,input_revision:0,placement_revision:0,status:'active',spec:{blocks:[],input_schema:{properties:{answer:{type:'string'}}}},input_state:{draft:{answer:'saved'}}};
  h.run((mod,hooks)=>{
    Object.assign(react,{useState:hooks.useWbcState,useRef:hooks.useWbcRef,useEffect:hooks.useWbcEffect});
    return mod.useAssistantCard('c',card,true);
  });
  h.value.editDraft('answer','my unsaved draft');h.flush();
  card={...card,revision:2,input_state:{draft:{answer:'server edit'}}};h.flush();
  assert.equal(h.value.card.revision,2);
  assert.equal(h.value.values.answer,'my unsaved draft');
  assert.equal(network,0);h.unmount();assert.equal(h.timers.size,0);
});
