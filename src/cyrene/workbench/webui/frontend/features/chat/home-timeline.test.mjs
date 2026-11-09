import test from 'node:test';
import assert from 'node:assert/strict';
import {captureHomePosition,homeInsertionIndex} from './home-timeline.mjs';
import {presentConversationMessages} from './message-presentation.mjs';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {transformSync} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

test('home keeps its place across new messages and missing history', () => {
  const now=Date.now(), history=[{id:'old',createdAt:now-1000}];
  const position=captureHomePosition(history,'assistant',now);
  const messages=[...history,{id:'new',createdAt:now+1000},{id:'reply',createdAt:now+2000}];
  assert.equal(homeInsertionIndex(messages,position),1);
  assert.equal(homeInsertionIndex(messages.slice(1),position),0);
  assert.equal(homeInsertionIndex(messages,captureHomePosition([],'assistant',now)),0);
});

test('actual transcript renders new human messages and live replies after the home exactly once', () => {
  const source=readFileSync(new URL('./messages.jsx',import.meta.url),'utf8');
  const code=transformSync(source+`
    wbcGroupConsecutiveActivityMessages = messages => messages;
    WbcUserMessage = ({msg}) => React.createElement('p',null,msg.content);
    WbcAssistantMessageBoundary = ({msg}) => React.createElement('p',null,msg.content);
  `,{loader:'jsx',format:'cjs'}).code;
  const deps={homeInsertionIndex,presentConversationMessages,
    createDisclosureSubscriptions:()=>new Map(),
    wbcProjectTranscript:(messages,runtime)=>messages.concat(runtime?.timeline.messages||[]),
    WbcThreadItem:({children})=>React.createElement('section',null,children)};
  const context={module:{exports:{}},require:()=>deps,React};
  vm.runInNewContext(code,context);
  const old={id:'old',role:'assistant',content:'OLD'}, user={id:'new',role:'user',content:'NEW'}, reply={id:'reply',role:'assistant',content:'REPLY'};
  for(const live of [true,false]) {
    const html=renderToStaticMarkup(React.createElement(context.module.exports.WbcTranscript,{
      messages:live?[old,user]:[old,user,reply],runtime:live?{timeline:{messages:[reply]}}:null,
      homeInsertion:{position:captureHomePosition([old],'assistant'),content:React.createElement('aside',null,'HOME')}
    }));
    assert.ok(html.indexOf('OLD')<html.indexOf('HOME'));
    assert.ok(html.indexOf('HOME')<html.indexOf('NEW'));
    assert.ok(html.indexOf('NEW')<html.indexOf('REPLY'));
    assert.equal(html.match(/HOME/g).length,1);
    assert.equal(html.match(/REPLY/g).length,1);
  }
});

test('home claims time slots, keeps its anchor during updates, and stays hidden on restart', async () => {
  const {harness} = await import('./hook-harness.test.mjs');
  const settle = async () => { for(let i=0;i<10;i++) await Promise.resolve(); };
  let slot='2026-11-09:00', owner=null, client=0;
  const mount = () => {
    let messages=[{id:'old'}], currentHooks;
    const h=harness('./assistant-home-transcript.jsx', {
      useState:(...args)=>currentHooks.useWbcState(...args),
      useRef:(...args)=>currentHooks.useWbcRef(...args),
      useEffect:(...args)=>currentHooks.useWbcEffect(...args),
      createElement:(type,props)=>({type,props}),
      AssistantHomeCards:'home', captureHomePosition, wbcProjectTranscript:items=>items,
      requestCard:async(_url,options)=> {
        assert.equal(options.headers['Content-Type'],'application/json');
        const id=JSON.parse(options.body).client_id;
        owner ||= id;
        return {slot:owner===id ? slot : null};
      },
    });
    h.context.crypto={randomUUID:()=>String(++client)};
    h.context.setInterval=h.context.window.setTimeout;
    h.context.clearInterval=h.context.window.clearTimeout;
    h.run((mod,hooks)=> {
      currentHooks=hooks;
      return mod.AssistantHomeTranscript({Transcript:'transcript',messages,sessionId:'a',active:true});
    });
    return {h,send:()=>{messages.push({id:'new'});h.flush();}};
  };
  const {h,send}=mount();
  assert.equal(h.value.props.homeInsertion.content.props.visible,false);
  h.value.props.homeInsertion.content.props.onReady();h.flush();await settle();h.flush();
  assert.equal(h.value.props.homeInsertion.position.afterId,'old');
  send();
  for(const tick of h.timers.values()) tick();
  await settle();h.flush();
  assert.equal(h.value.props.homeInsertion.position.afterId,'old');
  slot='2026-11-09:12';
  for(const tick of h.timers.values()) tick();
  await settle();h.flush();
  assert.equal(h.value.props.homeInsertion.position.afterId,'new');
  h.unmount();assert.equal(h.timers.size,0);
  const reopened=mount().h;
  reopened.value.props.homeInsertion.content.props.onReady();reopened.flush();await settle();reopened.flush();
  assert.equal(reopened.value.props.homeInsertion.content.props.visible,false);
  reopened.unmount();
});
