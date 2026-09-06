import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function setup(fetch) {
  const listeners = new Map(), nodes = [], opened = [];
  const root = { fetch, location: {href:'http://localhost/',origin:'http://localhost'},
    document: {documentElement:{lang:'zh'}, querySelector:()=>null,
      body:{appendChild:node=>nodes.push(node)},
      createElement:()=>({setAttribute(){},append(...children){this.children=children;},remove(){this.removed=true;}})},
    addEventListener:(key,fn)=>listeners.set(key,fn), removeEventListener:key=>listeners.delete(key)};
  const source = fs.readFileSync(new URL('./capture.jsx',import.meta.url),'utf8')
    .replace(/^import .*\n/gm, '').replace('export function','function').replace('installDoctorCapture(window);','');
  const context = vm.createContext({URL,Date,Map,wbSetBrowserOverlayObscured:()=>{},openDoctor:scope=>opened.push(scope)});
  vm.runInContext(source,context);
  const close = context.installDoctorCapture(root);
  return {root,nodes,listeners,opened,close};
}

test('HTTP failure preserves response, links ID and never reads body',async()=>{
  const response={status:500, headers:{get:()=> 'incident_'+'a'.repeat(32)}};
  const s=setup(async()=>response);
  assert.equal(await s.root.fetch('/api/chat?secret=private'),response);
  assert.equal(s.nodes.length,1);
  s.nodes[0].children[1].onclick();
  assert.equal(s.opened[0].incident_id,'incident_'+'a'.repeat(32));
  assert.equal(JSON.stringify(s.opened).includes('private'),false);
});

test('Doctor requests, foreign endpoints and cancellation do not report',async()=>{
  const s=setup(async()=>({status:500,headers:{get:()=>null}}));
  await s.root.fetch('/api/doctor/reports'); await s.root.fetch('https://other.test/api/fail');
  assert.equal(s.nodes.length,0);
  const err=Object.assign(new Error('private'),{name:'AbortError'});
  const cancelled=setup(async()=>{throw err;});
  await assert.rejects(cancelled.root.fetch('/api/a'),e=>e===err);
  assert.equal(cancelled.nodes.length,0);
});

test('network and uncaught failures provide bounded guidance and cleanup',async()=>{
  const err=new Error('secret'); const s=setup(async()=>{throw err;});
  await assert.rejects(s.root.fetch('/api/a'),e=>e===err);
  s.listeners.get('error')({message:'private'});
  s.listeners.get('unhandledrejection')({reason:err});
  assert.equal(s.nodes.length,1);
  s.nodes[0].children[1].onclick();
  assert.equal(s.opened[0].client_code,'unhandled_rejection');
  s.close(); assert.equal(s.listeners.size,0);
});

test('failure to render diagnostics cannot change request outcome',async()=>{
  const response={status:400,headers:{get:()=>null}}; const s=setup(async()=>response);
  s.root.document.createElement=()=>{throw new Error('DOM unavailable');};
  assert.equal(await s.root.fetch('/api/a'),response);
});
