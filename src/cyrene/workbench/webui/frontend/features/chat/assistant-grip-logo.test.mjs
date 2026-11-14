import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';
const createElement=(type,props,...children)=>({type,props:{...props,children}});
function hooks(React,h) {Object.assign(React,{useState:h.useWbcState,useRef:h.useWbcRef,useEffect:h.useWbcEffect,useId:()=> 'test'});}
test('static logo never attaches motion and disabling animation disposes it',()=>{
  const React={createElement};let animated=false,attached=0,destroyed=0,state='idle';const states=[];
  const h=harness('../shell/cyrene-icon.jsx',{React,attachCyreneMotion:()=>{attached++;return {destroy:()=>destroyed++,setState:s=>states.push(s)};}});
  h.run((mod,hk)=>{hooks(React,hk);return mod.CyreneIcon({animated,state});});
  assert.equal(attached,0);
  animated=true;state='thinking';h.flush();assert.equal(attached,1);assert.equal(states.at(-1),'thinking');
  state='waiting';h.flush();assert.equal(states.at(-1),'waiting');
  animated=false;h.flush();assert.equal(destroyed,1);h.unmount();assert.equal(destroyed,1);
});
test('grip delegates to a persistent identity menu with its conversation anchor',()=>{
  const React={createElement,createContext:()=>({}),useContext:()=>null};const surfaceRef={current:{}};
  const h=harness('../shell/assistant-grip-logo.jsx',{React,AssistantIdentityMenu:'identity-menu'});
  h.run((mod,hk)=>{hooks(React,hk);return mod.AssistantGripLogo({surfaceRef,sessionId:'assistant'});});
  assert.equal(h.value.type,'identity-menu');assert.equal(h.value.props.surfaceRef,surfaceRef);
  assert.equal(h.value.props.sessionId,'assistant');h.unmount();
});

test('loading logo survives the bootstrap handoff and hides when its ready surface unmounts', () => {
  const React = {createElement, createContext: () => ({Provider: 'provider'})};
  const h = harness('../shell/assistant-grip-logo.jsx', {React, AssistantIdentityMenu: 'identity-menu'});
  h.run((mod, hk) => {
    hooks(React, hk); React.useCallback = hk.useWbcCallback;
    return mod.AssistantLogoProvider({children: 'workspace'});
  });
  const logo = () => h.value.props.children[1];
  const register = h.value.props.value;
  const bootstrap = {surfaceRef: {current: {}}, loading: true};
  const chat = {surfaceRef: {current: {}}, loading: true, sessionId: 'assistant'};
  const stopBootstrap = register(bootstrap); h.flush();
  assert.equal(logo().props.surfaceRef, bootstrap.surfaceRef);
  stopBootstrap(); h.flush();
  assert.equal(logo().props.loading, true);
  assert.equal(logo().props.hidden, false, 'keep the logo visible between page mounts');
  const stopChat = register(chat); h.flush();
  assert.equal(logo().props.surfaceRef, chat.surfaceRef);
  assert.equal(logo().props.loading, true);
  stopChat(); const stopReady = register({...chat, loading: false}); h.flush();
  assert.equal(logo().props.loading, false);
  assert.equal(logo().props.surfaceRef, chat.surfaceRef);
  stopBootstrap(); h.flush();
  assert.equal(logo().props.hidden, false, 'stale cleanup cannot remove the current surface');
  stopReady(); h.flush();
  assert.equal(logo().props.hidden, true);
  h.unmount();
});
