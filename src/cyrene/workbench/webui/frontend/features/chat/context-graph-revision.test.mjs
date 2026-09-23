import test from 'node:test';
import assert from 'node:assert/strict';
import { updateContextRevision } from './context-graph-revision.mjs';

test('timeline revisions create an independent graph branch and activate without generating', async () => {
 const calls=[], events=[]; const oldFetch=globalThis.fetch, oldWindow=globalThis.window, oldEvent=globalThis.CustomEvent;
 globalThis.window={dispatchEvent:e=>events.push(e)};
 globalThis.CustomEvent=class { constructor(type,options) {this.type=type;this.detail=options.detail;} };
 globalThis.fetch=async (url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>calls.length===1?{updatedAt:'v1',revision:'tree-version'}:{chat:{id:'new-branch'}}};};
 try {await updateContextRevision('source',{nodeId:'node',updatedAt:'v1'},'edited');
  assert.equal(calls[1][1].method,'POST');assert.equal(JSON.parse(calls[1][1].body).expectedRevision,'tree-version');
  assert.equal(events[0].detail.chatId,'new-branch');assert.equal(events[0].detail.replay,false);
 } finally {globalThis.fetch=oldFetch;globalThis.window=oldWindow;globalThis.CustomEvent=oldEvent;}
});
test('stale timeline content cannot silently become a new revision',async()=>{
 const old=globalThis.fetch;let reads=0;globalThis.fetch=async()=>{reads++;return {ok:true,json:async()=>({updatedAt:'new'})};};
 try {await assert.rejects(updateContextRevision('source',{nodeId:'node',updatedAt:'old'},'edit'),/Context changed/);assert.equal(reads,1);}finally{globalThis.fetch=old;}
});
