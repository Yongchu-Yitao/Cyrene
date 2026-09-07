const assert = require('node:assert/strict');
const test = require('node:test');
const { EventEmitter } = require('node:events');
const { BrowserSessions } = require('./browser-sessions');

function fixture() {
  const calls = [];
  let window = null;
  const sessions = new BrowserSessions({
    getMainWindow: () => window,
    normalizeBrowserSessionId: value => String(value || ''),
    createManager: sessionId => ({
      sessionId, tabs: new Map(), visible: true,
      state() { return {sessionId, tabs: [{id: 'tab'}]}; },
      setContext(info) { calls.push(['context', sessionId, info]); },
      syncAttachedView() { calls.push(['sync', sessionId]); },
      emitState() { calls.push(['emit', sessionId]); },
      hideAllAgentCursors() { calls.push(['hide', sessionId]); },
      setBounds(bounds) { calls.push(['bounds', sessionId, bounds]); },
      setObscured(value) { calls.push(['obscured', sessionId, value]); },
      closeAll() { calls.push(['close', sessionId]); },
    }),
  });
  return { sessions, calls, setWindow(value) { window = value; } };
}

test('session activation keeps manager identity and hides the prior owner before activating', () => {
  const {sessions, calls} = fixture();
  const first = sessions.activateBrowserSession({sessionId:'a'});
  sessions.activateBrowserSession({sessionId:'b'});
  assert.equal(sessions.getBrowserTabManager('a'), first);
  assert.equal(first.visible, false);
  assert.deepEqual(calls.slice(3,5), [['hide','a'],['sync','a']]);
  assert.equal(sessions.browserManagerState().activeSessionId,'b');
  sessions.closeAllBrowserSessions();
  assert.equal(sessions.browserTabManagers.size,0);
  assert.equal(sessions.activeBrowserSessionId,'');
});

test('download ownership survives detached contents and completion clears the exact record', () => {
  const {sessions, setWindow} = fixture();
  const events=[];
  const contents = {getTitle:()=> 'Page', getURL:()=> 'https://example.test'};
  sessions.getBrowserTabManager('a').tabs.set('tab',{id:'tab',view:{webContents:contents}});
  const item = Object.assign(new EventEmitter(), {
    getFilename:()=> 'file.txt', getURL:()=> 'https://example.test/file',
    getReceivedBytes:()=> 5, getTotalBytes:()=> 10, isPaused:()=> false,
  });
  setWindow({isDestroyed:()=> false,webContents:{isDestroyed:()=> false,send:(...args)=>events.push(args)}});
  sessions.trackBrowserDownload(item,contents);
  assert.equal(sessions.browserManagerState().downloads[0].sessionId,'a');
  item.emit('updated',{},'progressing');
  assert.ok(sessions.browserManagerPublishTimer);
  item.emit('done',{},'completed');
  assert.equal(sessions.browserManagerState().downloadCount,0);
  assert.equal(events.at(-1)[0],'browser:manager-state');
  sessions.closeAllBrowserSessions();
  assert.equal(sessions.browserManagerPublishTimer,null);
});
