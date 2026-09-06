import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

test('navigator defers all history measurements during sidebar motion and refreshes after thaw', () => {
  const source = readFileSync(new URL('./conversation-navigator.jsx', import.meta.url), 'utf8');
  const code = source.slice(source.indexOf('function wbcConversationResizeActive'), source.indexOf('function WbcConversationNavigator({'));
  let pending, reads = 0;
  const thread = new EventTarget();
  Object.assign(thread, {wbcResizeActive:true, clientHeight:500, scrollTop:0});
  const context = {document:{body:{classList:{contains:()=>false}}}, window:new EventTarget(),
    requestAnimationFrame(fn) { pending=fn; return 1; }, cancelAnimationFrame(){}};
  vm.createContext(context);
  const Observer = vm.runInContext(code+'\nWbcConversationNavigatorObserver', context);
  const observer = new Observer(thread, fn => fn({markers:[]}));
  observer.itemsDirty = false;
  observer.items = [{get offsetTop(){reads++; return 0},get offsetHeight(){reads++;return 100}}];
  observer.start();
  observer.invalidateAll(); observer.measure();
  assert.equal(reads, 0);
  assert.equal(pending, undefined);
  thread.wbcResizeActive = false;
  thread.dispatchEvent(new Event('workbench:transcript-resize-end'));
  assert.equal(typeof pending, 'function');
  pending();
  assert.equal(reads, 2);
  observer.stop();
});
