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
test('grip logo stays in its own pane and follows live activity',()=>{
  const React={createElement};const target={id:'own-grip'};let state='thinking';
  const surfaceRef={current:{closest:s=>{assert.equal(s,'.wbc-pane-card');return {querySelector:s=>{assert.equal(s,'.wbc-side-split-grip-bar-visual');return target;}};}}};
  const h=harness('../shell/assistant-grip-logo.jsx',{React,CyreneIcon:'icon',useCyreneActivity:id=>{assert.equal(id,'assistant');return state;}});
  h.context.ReactDOM={createPortal:(child,host)=>({child,host})};
  h.run((mod,hk)=>{hooks(React,hk);return mod.AssistantGripLogo({surfaceRef,sessionId:'assistant'});});
  assert.equal(h.value.host,target);assert.equal(h.value.child.props.state,'thinking');
  state='waiting';h.flush();assert.equal(h.value.child.props.state,'waiting');h.unmount();
});
