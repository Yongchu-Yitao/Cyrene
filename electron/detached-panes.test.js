const assert = require('node:assert/strict');
const test = require('node:test');
const {EventEmitter} = require('node:events');
const {DetachedPanes} = require('./detached-panes');

function fixture() {
  const events=[];
  const sender={id:7,isDestroyed:()=>false,send:(...args)=>events.push(args)};
  const source={isDestroyed:()=>false,getBounds:()=>({x:0,y:0,width:1000,height:800}),webContents:sender};
  const panes=new DetachedPanes({
    getMainWindow:()=>source, BrowserWindow:{fromWebContents:()=>source},
    screen:{getCursorScreenPoint:()=>({x:500,y:400}),getDisplayNearestPoint:()=>({bounds:{x:0,y:0,width:1920,height:1080},workArea:{x:0,y:0,width:1920,height:1040}})},
    waitForPort:async()=>42,normalizeBrowserSessionId:value=>String(value||''),
    browserSessions:{browserTabManagers:new Map()},installLocalNavigationGuards:()=>{},AUTH_TOKEN:'test',
  });
  return {panes,sender,source,events};
}

test('drag cancellation clears its timer and transports draft without sharing mutable objects',()=>{
  const {panes,sender,events}=fixture();
  const descriptor={kind:'chat',payload:'c',draft:{text:'keep'}};
  assert.deepEqual(panes.beginDetachedPaneDrag(sender,{descriptor}),{ok:true});
  const drag=panes.detachedPaneDragSessions.get(sender.id);
  assert.notEqual(drag.descriptor.draft,descriptor.draft);
  assert.deepEqual(drag.descriptor.draft,descriptor.draft);
  const result=panes.finishDetachedPaneDragSession(drag,{x:500,y:400});
  assert.equal(result.detached,false);
  assert.equal(drag.timer,null);
  assert.equal(panes.detachedPaneDragSessions.size,0);
  assert.equal(events.at(-1)[1].cancelled,true);
});

test('browser surface return only removes the window owned by that record',()=>{
  const {panes}=fixture(); const window=new EventEmitter(); const newer=new EventEmitter();
  const record={window,descriptor:{kind:'browser',ownerChatId:'chat'}};
  panes.detachBrowserSurface(record);
  assert.equal(panes.detachedBrowserSurfaceWindows.get('chat'),window);
  panes.detachedBrowserSurfaceWindows.set('chat',newer);
  panes.restoreBrowserSurface(record);
  assert.equal(panes.detachedBrowserSurfaceWindows.get('chat'),newer);
  panes.restoreBrowserSurface({...record,window:newer});
  assert.equal(panes.detachedBrowserSurfaceWindows.size,0);
});
