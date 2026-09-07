const test = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const vm = require('node:vm');
const fs = require('node:fs');
function setup() {
 const timers=new Map();let timerId=0;
 const context={module:{exports:{}},console,setTimeout(fn,ms){timers.set(++timerId,{fn,ms});return timerId;},clearTimeout(id){timers.delete(id);}};
 vm.runInNewContext(fs.readFileSync(require.resolve('./browser-tab-picker-owner'),'utf8'),context);
 const views=[];
 class View {
  constructor(options){this.options=options;this.visible=false;const wc=this.webContents=new EventEmitter();wc.dead=false;wc.focused=false;wc.messages=[];
   wc.isDestroyed=()=>wc.dead;wc.loadURL=async url=>{wc.url=url;};wc.insertCSS=async css=>{wc.css=css;};wc.send=(...args)=>wc.messages.push(args);wc.focus=()=>{wc.focused=true;};wc.isFocused=()=>wc.focused;wc.close=()=>{wc.dead=true;};views.push(this);
  }
  setVisible(value){this.visible=value;} setBounds(bounds){this.bounds=bounds;} setBackgroundColor(){}
 }
 function window(){const win=new EventEmitter();win.contentView={children:new Set(),addChildView(v){this.children.add(v);},removeChildView(v){this.children.delete(v);}};win.isDestroyed=()=>false;win.isFocused=()=>true;win.webContents={send(){},focus(){win.refocused=true;}};return win;}
 let win=window(),ready=true;
 const picker=new context.module.exports.BrowserTabPicker({sessionId:'session',View,preloadPath:'preload',flatChromeCSS:'flat',pickerUrl:()=>'/picker',ownerWindow:()=>win,activeTabId:()=>'tab',tabSnapshots:()=>[{id:'tab'}],tabCount:()=>1,surfaceBounds:()=>({x:0,y:60,width:1000,height:700}),hostReady:()=>ready});
 return {picker,timers,views,get win(){return win;},move(){win=window();},block(){ready=false;},flush(ms){for(const [id,t] of [...timers])if(t.ms===ms){timers.delete(id);t.fn();}}};
}

test('picker restores visibility during animated hide and disposes view, timer and window listener',async()=>{
 const h=setup(),p=h.picker;p.setTabPicker({visible:true,variant:'split'});const view=h.views[0];
 assert.equal(view.options.webPreferences.backgroundThrottling,true);assert.equal(view.options.webPreferences.sandbox,true);assert.equal(view.webContents.url,'/picker');
 view.webContents.emit('did-finish-load');await new Promise(resolve=>setImmediate(resolve));assert.equal(p.tabPickerReady,true);assert.equal(view.webContents.css,'flat');
 h.win.emit('blur');assert.equal(p.tabPickerState.visible,false);assert.equal(p.tabPickerState.closing,true);assert.equal([...h.timers.values()].filter(t=>t.ms===220).length,1);
 p.setTabPicker({visible:true,variant:'split'});assert.equal(p.tabPickerState.closing,false);assert.equal([...h.timers.values()].filter(t=>t.ms===220).length,0);
 const oldWin=h.win;h.move();p.syncTabPicker(h.win.contentView,true);assert.equal(oldWin.listenerCount('blur'),0);assert.equal(oldWin.contentView.children.size,0);assert.equal(h.win.listenerCount('blur'),1);
 p.dismissTabPicker(true);p.dispose();h.flush(0);h.flush(220);assert.equal(h.win.listenerCount('blur'),0);assert.equal(h.win.contentView.children.size,0);assert.equal(view.webContents.dead,true);assert.equal(p.tabPickerState.visible,false);
 p.dispose();assert.equal(h.views.length,1);
});

test('stale host hide cannot dismiss the other picker variant; unavailable host hides immediately',async()=>{
 const h=setup(),p=h.picker;p.setTabPicker({visible:true,variant:'split',labels:{title:'x'.repeat(200)}});
 assert.equal(p.tabPickerState.labels.title.length,120);
 p.setTabPicker({visible:false,variant:'maximized'});assert.equal(p.tabPickerState.visible,true);
 h.block();p.syncTabPicker(h.win.contentView);assert.equal(p.tabPickerState.visible,false);assert.equal(h.views[0].visible,false);p.dispose();
});
