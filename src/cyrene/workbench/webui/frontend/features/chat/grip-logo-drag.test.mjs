import test from 'node:test';
import assert from 'node:assert/strict';
import {createGripLogoDrag} from './grip-logo-drag.mjs';
function fixture(reduced=false){
  const listeners=new Map(),frames=new Map();let id=0,time=0;
  const style={transform:'',setProperty(k,v){this[k]=v;},removeProperty(k){delete this[k];}};
  const slot={style,dataset:{},ownerDocument:{documentElement:{dataset:{}}},getBoundingClientRect:()=>({left:200,top:30,right:236,bottom:66})};
  const win={innerWidth:800,innerHeight:600,matchMedia:()=>({matches:reduced}),addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k),requestAnimationFrame:f=>{frames.set(++id,f);return id;},cancelAnimationFrame:i=>frames.delete(i)};
  return {slot,win,listeners,frames,step(n=1){for(let i=0;i<n;i++){time+=1000/60;const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn(time));}}};
}
test('spring lags behind pointer, resists long pulls and animates expression before settling home',()=>{
  const f=fixture(),drag=createGripLogoDrag(f.slot,{clientX:218,clientY:48},f.win);
  drag.move({clientX:418,clientY:148});assert.equal(f.slot.style.transform,'');
  f.step();const first=parseFloat(f.slot.style.transform.slice(10));assert.ok(first>0&&first<20);
  assert.ok(Number(f.slot.style['--logo-drag-squint'])<=1);
  f.step(120);const resting=parseFloat(f.slot.style.transform.slice(10));assert.ok(resting>20&&resting<48,'200px pointer travel has elastic resistance');
  drag.finish();assert.notEqual(f.slot.style.transform,'');f.step(240);
  assert.equal(f.slot.style.transform,'');assert.equal(f.slot.dataset.logoDragging,undefined);assert.equal(f.frames.size,0);drag.destroy();
});
test('blur releases spring and unmount cancels animation and restores slot',()=>{
  const f=fixture(),drag=createGripLogoDrag(f.slot,{clientX:218,clientY:48},f.win);
  drag.move({clientX:318,clientY:148});f.step(10);f.listeners.get('blur')();f.step(240);assert.equal(f.slot.style.transform,'');
  drag.destroy();assert.equal(f.listeners.size,0);assert.equal(f.frames.size,0);
});
test('reduced motion skips inertia and expression while retaining resistance',()=>{
  const f=fixture(true),drag=createGripLogoDrag(f.slot,{clientX:218,clientY:48},f.win);
  drag.move({clientX:318,clientY:48});f.step();assert.ok(parseFloat(f.slot.style.transform.slice(10))<96);
  assert.equal(f.slot.style['--logo-drag-squint'],undefined);drag.finish();f.step();assert.equal(f.slot.style.transform,'');drag.destroy();
});

test('extreme pointer movement stays within the elastic range and pane',()=>{
  const f=fixture(true);f.slot.closest=()=>({getBoundingClientRect:()=>({left:180,top:20,right:300,bottom:150})});
  const drag=createGripLogoDrag(f.slot,{clientX:218,clientY:48},f.win);
  drag.move({clientX:100000,clientY:100000});f.step();
  const values=f.slot.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px/);
  assert.ok(Number(values[1])<=56);assert.ok(Number(values[2])<=72);
  drag.move({clientX:-100000,clientY:-100000});f.step();
  const back=f.slot.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px/);
  assert.ok(Number(back[1])>=-12);assert.ok(Number(back[2])>=-2);drag.destroy();
});

test('held drag keeps directional expression and blinking, then restores normal animation',()=>{
  const f=fixture(),drag=createGripLogoDrag(f.slot,{clientX:218,clientY:48},f.win);
  drag.move({clientX:418,clientY:98});f.step(110);
  assert.ok(parseFloat(f.slot.style['--logo-drag-look-x'])>1);
  const open=Number(f.slot.style['--logo-drag-left-eye']);
  assert.notEqual(f.slot.style['--logo-drag-left-eye'],f.slot.style['--logo-drag-right-eye']);
  f.step(31);assert.ok(Number(f.slot.style['--logo-drag-left-eye'])<open*.5);
  drag.finish();f.step(240);
  assert.equal(f.slot.style['--logo-drag-left-eye'],undefined);assert.equal(f.frames.size,0);drag.destroy();
});

test('approved drag range, initial resistance and no rebound expression',()=>{
  const f=fixture(),drag=createGripLogoDrag(f.slot,{clientX:218,clientY:48},f.win);
  drag.move({clientX:228,clientY:48});f.step(120);
  assert.ok(parseFloat(f.slot.style.transform.slice(10))<3.6);
  assert.equal(f.slot.dataset.logoMood,'curious');
  drag.move({clientX:518,clientY:48});f.step(120);assert.equal(f.slot.dataset.logoMood,'strain');
  assert.ok(parseFloat(f.slot.style.transform.slice(10))<=48);
  drag.finish();assert.equal(f.slot.dataset.logoMood,undefined);f.step(240);drag.destroy();
});
