import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';

test('answers use the same stream consumer as normal messages', async () => {
  const h = harness('./model-api.jsx', {workbenchServices: {i18n: () => ({getLang: () => 'zh'})}, wbcRouteAgentEvent: () => false});
  h.context.window.CyreneUI = {has: () => false};
  h.context.TextDecoder = TextDecoder;
  const events = [ {type:'ack', userMessage:{id:'answer'}}, {type:'reply_delta',delta:'working'}, {type:'saved',assistantMessage:{id:'done'}} ];
  let request;
  h.context.fetch = async (url, options) => {
    request = {url, body:JSON.parse(options.body)};
    let done = false;
    return {ok:true,body:{getReader:()=>({read:async()=> {
      if (done) return {done:true}; done=true;
      return {done:false,value:new TextEncoder().encode(events.map(e=>JSON.stringify(e)).join('\n')+'\n')};
    }})}};
  };
  const received=[];
  await h.context.module.exports.WorkbenchChatModel.sendMessage('chat', {questionId:'q',message:'全部要',mode:'default'}, {
    onAck:e=>received.push(e),onReplyDelta:e=>received.push(e),onSaved:e=>received.push(e),
  });
  assert.equal(request.url,'/api/workbench/chats/chat/answer');
  assert.equal(request.body.question_id,'q');
  assert.equal(request.body.answer,'全部要');
  assert.equal(request.body.stream,true);
  assert.equal(received.length,3);
});
