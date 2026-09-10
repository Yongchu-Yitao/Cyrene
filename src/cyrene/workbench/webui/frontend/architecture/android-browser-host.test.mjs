import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../shared/browser/android-host.jsx', import.meta.url), 'utf8').replace('export function ', 'function ');
function setup() {
 const requests=[],sockets=[],handlers={};
 const win={location:{protocol:'http:',host:'127.0.0.1:1234'},setTimeout,clearTimeout,addEventListener:(name,fn)=>handlers[name]=fn,
  CyreneAndroidBrowser:{request:(id,raw,capability)=>requests.push({id,capability,...JSON.parse(raw)})},
  WebSocket:class {constructor(url){this.url=url;this.readyState=1;sockets.push(this);}send(raw){this.sent=JSON.parse(raw);}close(){this.readyState=3;}}};
 vm.runInNewContext(source,{window:win,Promise,Map,Set,JSON,Error});
 win.__cyreneInitAndroidBrowser("k".repeat(72));
 return {win,requests,sockets,handlers};
}
test('native UI and Agent RPC target the same conversation; state events unsubscribe', async()=>{
 const {win,requests,sockets,handlers}=setup();
 assert.equal(win.cyrene.browser.platform,'android');
 const ui=win.cyrene.browser.navigate({sessionId:'A',url:'https://example.com'});
 await Promise.resolve();
 assert.equal(requests[0].sessionId,'A');assert.equal(requests[0].capability,'k'.repeat(72));
 win.__cyreneAndroidBrowserResult(requests[0].id,{ok:true,tabId:'shared'});
 assert.equal((await ui).tabId,'shared');
 const received=sockets[0].onmessage({data:JSON.stringify({id:'agent',method:'inspect',sessionId:'A',args:{sessionId:'wrong'}})});
 await Promise.resolve();
 assert.equal(requests[1].sessionId,'A');
 win.__cyreneAndroidBrowserResult(requests[1].id,{ok:true,tabId:'shared'});
 await received;assert.equal(sockets[0].sent.result.tabId,'shared');
 let events=0;const remove=win.cyrene.browser.onState(()=>events++);
 win.__cyreneAndroidBrowserState({},{});remove();win.__cyreneAndroidBrowserState({},{});assert.equal(events,1);
 handlers.pagehide();
});
test('desktop bridge is untouched and absent native hosts do not enable browser',()=>{
 const browser={desktop:true};const win={cyrene:{browser},CyreneAndroidBrowser:{}};
 vm.runInNewContext(source,{window:win});assert.equal(win.cyrene.browser,browser);
 const plain={};vm.runInNewContext(source,{window:plain});assert.equal(plain.cyrene,undefined);
});

test('native DOM snapshot references expire and typing commits input events',()=>{
 class Input {
  constructor(){this.isConnected=true;this.tagName='INPUT';this.type='text';this.events=[];this.value='';}
  getBoundingClientRect(){return {x:1,y:2,width:100,height:30};}
  getAttribute(name){return name==='placeholder'?'Name':null;}
  focus(){} scrollIntoView(){} dispatchEvent(event){this.events.push(event.type);}
 }
 Object.defineProperty(Input.prototype,'value',{get(){return this._value;},set(value){this._value=value;}});
 const input=new Input();
 const context={window:{},location:{href:'https://example.com/'},document:{title:'Form',body:{innerText:'Name'},querySelectorAll:selector=>selector==='a[href]'?[]:[input],querySelector:()=>input},getComputedStyle:()=>({display:'block',visibility:'visible'}),HTMLInputElement:Input,HTMLTextAreaElement:class{},Event:class{constructor(type){this.type=type;}},Map};
 const command=vm.runInNewContext(fs.readFileSync(new URL('../../../../../../mobile/app/src/main/assets/browser/page-commands.js',import.meta.url),'utf8'),context);
 const first=command('inspect',{}).elements[0].ref;
 assert.equal(command('typeRef',{ref:first,text:'你好'}).ok,true);
 assert.equal(input.value,'你好');assert.deepEqual(input.events,['input','change']);
 command('inspect',{});assert.equal(command('typeRef',{ref:first,text:'stale'}).ok,false);
 assert.equal(input.value,'你好');
});
