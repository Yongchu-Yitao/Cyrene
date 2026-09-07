import assert from 'node:assert/strict'
import test from 'node:test'
import { beginAnswerProjection } from './answer-projection.mjs'

test('regular answer updates summary, existing cache and selected detail in order', () => {
  const other = { id: 'other', messages: [] };
  const original = { id: 'chat', pendingQuestion: { id: 'q' }, messages: [{ id: 'old' }], runStatus: 'waiting' };
  const cache = { chat: original };
  let list = [original, other], detail = original;
  const order = [];
  const optimistic = { id: 'answer' };
  beginAnswerProjection({
    cache,
    setChats(update) { order.push('list'); list = update(list); },
    setActiveChat(update) { order.push('detail'); assert.notEqual(cache.chat, original); detail = update(detail); },
  }, 'chat', optimistic, (messages, additions) => messages.concat(additions));
  assert.deepEqual(order, ['list', 'detail']);
  assert.equal(list[0].runStatus, 'running');
  assert.equal(detail.runStatus, 'waiting'); // detail contract differs from list
  assert.deepEqual(detail.messages, [{ id: 'old' }, optimistic]);
  assert.deepEqual(cache.chat, detail);
  assert.equal(list[1], other);
  assert.equal(original.pendingQuestion.id, 'q');
});

test('live answer leaves messages and cache alone and cannot replace another selected chat', () => {
  const messages = [{ id: 'old' }];
  let detail = { id: 'other', messages };
  const original = detail;
  let list = [{ id: 'chat', messages }];
  beginAnswerProjection({
    setChats(update) { list = update(list); },
    setActiveChat(update) { detail = update(detail); },
  }, 'chat');
  assert.equal(detail, original);
  assert.equal(list[0].messages, messages);
  assert.equal(list[0].pendingQuestion, null);
});

test('answer never creates a missing cache entry or loads a transcript', () => {
  const cache = {};
  let detail = null;
  beginAnswerProjection({ cache, setChats: update => update([]), setActiveChat: update => { detail = update(detail); } }, 'chat', { id: 'answer' }, () => { throw Error('must not merge'); });
  assert.deepEqual(cache, {});
  assert.equal(detail, null);
});

test('guidance confirmation and failure cannot overwrite a different active chat', async () => {
  const {guidanceProjection, hydrateAnswerProjection} = await import('./answer-projection.mjs');
  let active = {id:'b',messages:[]};
  const calls=[];
  const target={setActiveChat:update=>{active=typeof update==='function'?update(active):update;},
    runtimeEngine:{closeTimeline:id=>calls.push(['close',id]),recordUserMessage:(...args)=>calls.push(['record',...args])}};
  guidanceProjection(target,'a',{type:'begin',message:{id:'optimistic'}},(a,b)=>a.concat(b));
  guidanceProjection(target,'a',{type:'confirm',message:{id:'confirmed'},optimisticId:'optimistic'},(a,b)=>a.concat(b));
  guidanceProjection(target,'a',{type:'reject',requestId:'r'},(a,b)=>a.concat(b));
  assert.deepEqual(active,{id:'b',messages:[]});
  assert.equal(calls[2][3],'optimistic');
  const cache={};
  hydrateAnswerProjection({...target,cache,activeChatId:()=> 'b',isCurrent:()=>false},'a',{id:'a'});
  assert.deepEqual(cache,{});
});
