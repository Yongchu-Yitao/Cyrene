import vm from 'node:vm'
import {readFileSync} from 'node:fs'
import {transformSync} from 'esbuild'

// Run effects, dependency changes and cleanup in order, with batched rerenders.
// Storage/network are controlled boundaries; the Hook code is the real module.
export function harness(file, extras = {}) {
  const slots = [], pending = [], listeners = new Map(), timers = new Map();
  let index = 0, dirty = false, nextTimer = 0, render, result;
  const eventTarget = {
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name, fn) { if (listeners.get(name) === fn) listeners.delete(name); },
    setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
  };
  const dependencies = {
    useWbcState(initial) {
      const i = index++;
      if (!slots[i]) slots[i] = {value: typeof initial === 'function' ? initial() : initial};
      slots[i].setter ||= update => {
        const value = typeof update === 'function' ? update(slots[i].value) : update;
        if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; }
      };
      return [slots[i].value, slots[i].setter];
    },
    useWbcMemo(factory, deps) {
      const i=index++, old=slots[i];
      if (!old || deps.some((v,j)=>!Object.is(v,old.deps[j]))) slots[i]={deps,value:factory()};
      return slots[i].value;
    },
    useWbcRef(current) { const i = index++; return slots[i] ||= {current}; },
    useWbcEffect(effect, deps) {
      const i = index++, old = slots[i];
      if (!old || !deps || deps.some((v, j) => !Object.is(v, old.deps?.[j]))) {
        pending.push(() => { old?.cleanup?.(); slots[i] = {deps, cleanup: effect()}; });
      }
    },
    wbcT: (_key, fallback) => fallback,
    ...extras,
  };
  dependencies.useWbcLayoutEffect ||= dependencies.useWbcEffect;
  const context = {module:{exports:{}}, require: () => dependencies, window:eventTarget,
    document:{...eventTarget, visibilityState:'visible'}, AbortController, localStorage:extras.localStorage};
  vm.runInNewContext(transformSync(readFileSync(new URL(file, import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,context);
  function flush() {
    for(let turn=0;turn<30;turn++) {
      dirty=false; index=0; result=render(context.module.exports, dependencies);
      for(const effect of pending.splice(0)) effect();
      if(!dirty) return result;
    }
    throw Error('Hook did not settle');
  }
  return {run(fn) { render=fn; return flush(); }, flush, timers, listeners, context,
    unmount() { for(const slot of slots) slot?.cleanup?.(); },
    get value() { return result; },
  };
}
