import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';

const plain=value=>JSON.parse(JSON.stringify(value));
function setup(){const h=harness('./split-selection-state.jsx');h.run(m=>m.useWbcSplitSelection());return h;}

test('select and restore retain all historic slots without mutating another chat',()=>{
 const h=setup(), change={setId:'set',path:'p'}, resource={type:'viewer',payload:{path:'p'}};
 h.value.select('b','change',change);h.flush();const b=h.value.changeSplitByChat.b;
 h.value.restore('a',{sideAgentId:'agent',artifactKey:'file',change,resource});h.flush();
 const saved=h.value.snapshot('a');
 assert.deepEqual(plain(saved),{sideAgentId:'agent',artifactKey:'file',change,resource});
 h.value.select('a','resource',{type:'browser'});h.flush();
 assert.deepEqual(plain(h.value.snapshot('a')),{sideAgentId:'',artifactKey:'',change:null,resource:{type:'browser'}});
 assert.equal(h.value.changeSplitByChat.b,b);
 h.value.restore('a',saved);h.flush();assert.equal(h.value.resourceSplitByChat.a,resource);
 assert.equal(h.value.changeSplitByChat.a,change);h.unmount();
});

test('queued operations preserve selection, guarded deletion and resource pruning',()=>{
 const h=setup();
 h.value.select('a','side-agent','old');h.value.select('a','side-agent','new');h.value.close('a','side-agent','old');h.flush();
 assert.equal(h.value.sideAgentSplitByChat.a,'new');
 h.value.select('b','resource',{type:'browser'});h.value.select('c','resource',{type:'chat',payload:'a'});h.flush();
 const same=h.value.resourceSplitByChat;h.value.pruneResources(()=>false);h.flush();assert.equal(h.value.resourceSplitByChat,same);
 h.value.pruneResources(r=>r.type==='browser');h.flush();assert.equal(h.value.resourceSplitByChat.b,undefined);assert.equal(h.value.resourceSplitByChat.c.payload,'a');
 h.value.close('a','side-agent','new');h.flush();assert.equal(h.value.sideAgentSplitByChat.a,undefined);h.unmount();
});

test('selection transitions match the previous four-map contract across operation sequences',()=>{
 const h=setup(),names=['side-agent','artifact','change','resource'],fields=['sideAgentId','artifactKey','change','resource'];
 const legacy=Object.fromEntries(names.map(k=>[k,{}]));let seed=1729;
 const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed;};
 for(let i=0;i<500;i++){
   const chat='chat-'+(random()%7),kind=names[random()%4],op=random()%3;
   if(op===0){const value={id:i};for(const k of names){if(k===kind)legacy[k][chat]=value;else delete legacy[k][chat];}h.value.select(chat,kind,value);}
   if(op===1){delete legacy[kind][chat];h.value.close(chat,kind);}
   if(op===2){const snapshot={sideAgentId:'agent-'+i,artifactKey:'',change:{path:String(i)},resource:null};names.forEach((k,j)=>{if(snapshot[fields[j]])legacy[k][chat]=snapshot[fields[j]];else delete legacy[k][chat];});h.value.restore(chat,snapshot);}
   h.flush();
   for(const id of Array.from({length:7},(_,j)=>'chat-'+j))assert.deepEqual(plain(h.value.snapshot(id)),{sideAgentId:legacy['side-agent'][id]||'',artifactKey:legacy.artifact[id]||'',change:legacy.change[id]||null,resource:legacy.resource[id]||null});
 }
 h.unmount();
});
