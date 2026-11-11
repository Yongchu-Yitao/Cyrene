import test from 'node:test';
import assert from 'node:assert/strict';
import {attachCyreneMotion} from '../shell/cyrene-motion-dom.mjs';
import {attachCyreneIdleAttention} from '../shell/cyrene-idle-attention.mjs';
function fixture() {
  const target=()=>{const listeners=new Map();return {listeners,addEventListener:(k,f)=>{const set=listeners.get(k)||new Set();set.add(f);listeners.set(k,set);},removeEventListener:(k,f)=>listeners.get(k)?.delete(f),emit(k,e={}){for(const f of listeners.get(k)||[])f(e);}};};
  const media=Object.assign(target(),{matches:false});let time=1000,cancelled=0;const animations=[];
  const win=Object.assign(target(),{matchMedia:()=>media,performance:{now:()=>time},setTimeout:()=>1,clearTimeout:()=>{},requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{},MutationObserver:class {observe(){}disconnect(){}},innerWidth:1200,innerHeight:800,getComputedStyle:()=>({visibility:'visible',display:'block'})});
  const doc=Object.assign(target(),{defaultView:win,documentElement:{dataset:{}},hidden:false,querySelectorAll:()=>[]});
  const button=Object.assign(target(),{tagName:'DIV',dataset:{},setPointerCapture:()=>{throw Error('grip owns capture');}});
  const anchor={isConnected:true,closest:s=>s.includes('aria-hidden')?{}:null,getBoundingClientRect:()=>({x:300,y:20,width:36,height:36,top:20,left:300,bottom:56,right:336}),animate:(frames,options)=>{animations.push({frames,options});return {cancel:()=>cancelled++};}};
  const parts=new Map();
  const svg={ownerDocument:doc,parentElement:anchor,dataset:{},closest:s=>{assert.equal(s,'button, [role="button"]');return button;},querySelector:s=>{if(!parts.has(s))parts.set(s,{style:{}});return parts.get(s);}};
  return {svg,doc,button,media,animations,get cancelled(){return cancelled;},advance:()=>time+=500};
}
test('role-button grip retains click, keyboard, hover and cleanup without taking drag capture',()=>{
  const f=fixture(), controller=attachCyreneMotion(f.svg);
  for(const event of ['pointerenter','pointerleave','pointerdown','pointerup','keydown','keyup'])assert.ok(f.button.listeners.get(event)?.size,event);
  f.button.emit('pointerdown',{button:0,pointerId:1});f.button.emit('pointerup');f.button.emit('click',{detail:1});
  assert.equal(f.animations.length,1);assert.equal(f.animations[0].options.duration,440);
  f.button.emit('keydown',{key:'Enter'});f.button.emit('keyup',{key:'Enter'});f.button.emit('click',{detail:0});
  assert.equal(f.animations.length,2,'keyboard activation animates once');
  f.button.dataset.wbcPointerDragged='true';f.button.emit('click',{detail:1});assert.equal(f.animations.length,2);
  f.button.dataset.wbcPointerDragged='false';f.media.matches=true;f.media.emit('change');f.button.emit('click',{detail:1});assert.equal(f.animations.length,2);
  controller.destroy();assert.ok(f.cancelled>0);
  for(const set of f.button.listeners.values())assert.equal(set.size,0);
});
test('decorative grip still tracks pointer geometry while hidden ancestors suppress attention',()=>{
  const f=fixture();let targets;
  const motion={state:'idle',resting:false,setAttentionTargets:value=>targets=value};
  const attention=attachCyreneIdleAttention(f.svg,motion);
  f.doc.emit('pointermove',{pointerType:'mouse',clientX:600,clientY:38});attention.sample(true);
  assert.ok(targets.pointer.x>.99);
  f.svg.parentElement.closest=()=>({});attention.sample(true);assert.deepEqual(targets,{});
  attention.destroy();
});
