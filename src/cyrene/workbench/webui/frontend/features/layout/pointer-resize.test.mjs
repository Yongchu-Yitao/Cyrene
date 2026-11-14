import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { transformSync } from 'esbuild';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture({ kind = 'column', close = () => ({ closed: true }), animate = true } = {}) {
  const listeners = new Map(), handleListeners = new Map(), classes = new Set(), frames = new Map();
  const animations = [], calls = [];
  let frameId = 0;
  const element = {
    attrs: {}, style: { gridTemplateRows: '0.2fr 0.8fr' },
    setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
  };
  if (animate) element.animate = () => {
    const pending = deferred();
    const animation = { finished: pending.promise, playState: 'running',
      finish() { this.playState = 'finished'; pending.resolve(); },
      cancel() { this.playState = 'idle'; pending.reject(new Error('cancelled')); },
    };
    animations.push(animation);
    return animation;
  };
  const handle = {
    addEventListener: (n, f) => handleListeners.set(n, f), removeEventListener: n => handleListeners.delete(n),
    setPointerCapture() {}, hasPointerCapture: () => false,
  };
  const context = {
    module: { exports: {} },
    document: { body: { classList: { add: n => classes.add(n), remove: n => classes.delete(n) } } },
    window: { addEventListener: (n, f) => listeners.set(n, f), removeEventListener: n => listeners.delete(n),
      getComputedStyle: () => ({ gridTemplateColumns: '500px 12px 380px', gridTemplateRows: '100px 400px', columnGap: '0px', rowGap: '12px' }),
    },
    requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame: id => frames.delete(id),
  };
  vm.runInNewContext(transformSync(readFileSync(new URL('./pointer-resize.mjs', import.meta.url), 'utf8'), {format:'cjs'}).code, context);
  const start = () => context.module.exports.startPointerResize({currentTarget:handle,pointerId:1}, {
    bodyClass:'resizing', collapsePreview:{element,kind,axis:'x',direction:()=>1},
    start:()=>calls.push('start'), update:()=> 'minimum', paint:()=>{},
    commit:()=>calls.push('commit'), close:()=>{calls.push('close'); return close();},
  });
  start();
  return { element, calls, classes, animations, frames, listeners, handleListeners, start,
    move:()=>listeners.get('pointermove')?.({pointerId:1,clientX:100,buttons:1,pointerType:'mouse'}),
    stop:(type='pointerup')=>listeners.get(type)?.({type,pointerId:1}),
    flush() { while(frames.size) {const batch=[...frames];frames.clear();for(const [,fn] of batch)fn();} },
  };
}
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };

for (const kind of ['column','row','resource','panel']) {
  test(`${kind}: quick release retains collapsed layout until exit, async close and React commit finish`, async () => {
    const closing = deferred();
    const f = fixture({kind,close:()=>closing.promise});
    f.move(); f.stop();
    assert.equal(f.classes.size, 0, 'no global drag mode while confirming');
    assert.equal(f.listeners.size + f.handleListeners.size, 0);
    assert.deepEqual(f.calls, ['start']);
    assert.equal(f.element.attrs['data-resize-collapsed'], kind+'-minimum');
    assert.equal(f.element.attrs['data-resize-closing'], '');
    f.start(); assert.deepEqual(f.calls,['start'], 'pending close cannot acquire another gesture');
    f.animations[0].finish(); await tick();
    assert.deepEqual(f.calls,['start','commit','close']);
    f.flush();
    assert.equal(f.element.attrs['data-resize-collapsed'],kind+'-minimum');
    // React has removed the old row template; cleanup must not resurrect it.
    if(kind==='row') f.element.style.gridTemplateRows='';
    closing.resolve({closed:true}); await tick();
    assert.equal(f.element.attrs['data-resize-collapsed'],kind+'-minimum');
    f.flush(); assert.deepEqual(f.element.attrs,{});
    if(kind==='row') assert.equal(f.element.style.gridTemplateRows,'');
  });
}

for (const outcome of ['decline','reject','throw']) {
  test(`a ${outcome} restores the row and releases all preview locks`, async () => {
    const closing = deferred();
    const f = fixture({kind:'row',animate:false,close:()=>{
      if(outcome==='throw') throw new Error('failed');
      return closing.promise;
    }});
    f.move(); f.stop();
    if(outcome==='reject') closing.reject(new Error('failed'));
    else closing.resolve({closed:false,cancelled:true});
    await tick(); f.flush();
    assert.deepEqual(f.element.attrs,{});
    assert.equal(f.element.style.gridTemplateRows,'0.2fr 0.8fr');
    assert.equal(f.classes.size,0);
    f.start(); assert.equal(f.calls.filter(x=>x==='start').length,2);
    f.stop('pointercancel'); f.flush();
  });
}

test('cancelled exit animation still completes the explicit pointerup close exactly once', async () => {
  const f=fixture(); f.move(); f.stop(); f.animations[0].cancel();
  await tick(); f.flush();
  assert.deepEqual(f.calls,['start','commit','close']);
  assert.deepEqual(f.element.attrs,{});
});

test('pointercancel restores the preview and never invokes close', async () => {
  const f=fixture(); f.move(); f.stop('pointercancel');
  await tick(); f.flush();
  assert.deepEqual(f.calls,['start','commit']);
  assert.deepEqual(f.element.attrs,{});
  assert.equal(f.classes.size,0);
});
