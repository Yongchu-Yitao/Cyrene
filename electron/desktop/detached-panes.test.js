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

function returnFixture() {
  const fixtureState = fixture();
  const {panes, source, events} = fixtureState;
  const childSender = {id: 8, send: (...args) => events.push(args)};
  let destroyed = false;
  const window = {
    webContents: childSender,
    isDestroyed: () => destroyed,
    destroy: () => { destroyed = true; },
    getBounds: () => ({x: 1100, y: 100, width: 500, height: 400}),
    setBounds: () => {},
    setAlwaysOnTop: () => {},
    moveTop: () => {},
  };
  source.show = () => events.push(['show']);
  source.focus = () => events.push(['focus']);
  const record = {
    id: 'pane', window, sourceWindow: source,
    descriptor: {kind: 'chat', payload: 'chat', draft: {text: 'unsent'}},
    sourceInfo: {cardId: 'chat:chat', sourceSide: 'right', sourceIndex: 1},
  };
  panes.detachedPaneWindows.set(record.id, record);
  return {...fixtureState, childSender, record, window};
}

test('explicit return preserves pane context without screen coordinates or a drag session', () => {
  const {panes, childSender, record, window, events} = returnFixture();
  panes.screen.getCursorScreenPoint = () => { throw new Error('No global coordinates on Wayland'); };
  assert.deepEqual(panes.returnDetachedPaneToSource(childSender), {ok: true, merged: true});
  assert.deepEqual(events[0], ['detached-pane:returned', {
    id: record.id, descriptor: record.descriptor, ...record.sourceInfo,
  }]);
  assert.equal(window.isDestroyed(), true);
  assert.equal(record.returning, true);
  assert.deepEqual(events.slice(1), [['show'], ['focus']]);
});

test('explicit return rejects unrelated senders and keeps the pane when the source is unavailable', () => {
  const {panes, sender, childSender, source, window} = returnFixture();
  assert.deepEqual(panes.returnDetachedPaneToSource(sender), {ok: false, merged: false});
  source.webContents.send = () => { throw new Error('closed'); };
  assert.deepEqual(panes.returnDetachedPaneToSource(childSender), {ok: false, merged: false});
  source.isDestroyed = () => true;
  assert.deepEqual(panes.returnDetachedPaneToSource(childSender), {ok: false, merged: false});
  assert.equal(window.isDestroyed(), false);
});

test('pointer-based return still merges into the source window', () => {
  const {panes, childSender, window} = returnFixture();
  panes.beginDetachedPaneReturnDrag(childSender);
  assert.deepEqual(panes.finishDetachedPaneReturnDrag(childSender, {x: 300, y: 200}), {ok: true, merged: true});
  assert.equal(window.isDestroyed(), true);
});

test('native maximize and restore publish actual detached window state', () => {
  const {panes} = fixture();
  const win = new EventEmitter();
  const updates = [];
  let maximized = false;
  let destroyed = false;
  let rendererDestroyed = false;
  win.isDestroyed = () => destroyed;
  win.isMaximized = () => maximized;
  win.webContents = {
    isDestroyed: () => rendererDestroyed,
    send: (...args) => updates.push(args),
  };
  panes.observeDetachedPaneWindowState(win);
  maximized = true;
  win.emit('maximize');
  maximized = false;
  win.emit('unmaximize');
  assert.deepEqual(updates, [
    ['detached-pane:window-state', {maximized: true}],
    ['detached-pane:window-state', {maximized: false}],
  ]);
  rendererDestroyed = true;
  win.emit('maximize');
  rendererDestroyed = false;
  destroyed = true;
  win.emit('unmaximize');
  assert.equal(updates.length, 2);
});

test('detached renderer initializes state, favors native events, and unsubscribes', async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname,
    '../../src/cyrene/workbench/webui/frontend/features/chat/context-panel.jsx'), 'utf8');
  // Execute the production initialization effect with controlled IPC timing.
  const effect = source.slice(source.indexOf('function WbcDetachedPaneApp()'))
    .split('useWbcEffect(function () {')[1].split('}, []);')[0];
  for (const nativeEventFirst of [false, true]) {
    let resolveContext;
    let listener;
    let unsubscribed = false;
    const states = [];
    const bridge = {
      onWindowState(callback) {
        listener = callback;
        return () => { unsubscribed = true; };
      },
      getContext: () => new Promise(resolve => { resolveContext = resolve; }),
    };
    const cleanup = new Function('bridge', 'setWindowMaximized', 'setContext',
      'setLoadError', 'wbcErrorText', effect)(bridge, state => states.push(state),
      () => {}, error => { throw new Error(error); }, String);
    if (nativeEventFirst) listener({maximized: false});
    resolveContext({ok: true, maximized: true, descriptor: {kind: 'chat'}});
    await Promise.resolve();
    assert.deepEqual(states, [!nativeEventFirst]);
    listener({maximized: true});
    listener({maximized: false});
    assert.deepEqual(states.slice(-2), [true, false]);
    cleanup();
    assert.equal(unsubscribed, true);
    listener({maximized: true});
    assert.equal(states.at(-1), false);
  }
});
