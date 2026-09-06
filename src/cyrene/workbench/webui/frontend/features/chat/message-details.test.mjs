import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {transformSync} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

function mount(name, initialExpanded = false) {
  const source = readFileSync(new URL('./messages.jsx',import.meta.url),'utf8');
  let expanded = initialExpanded, ref, count = 0;
  const deps = {
    useWorkbenchI18n(){}, useWbcRef(initial){return ref ||= {current:initial}},
    WBC_ICONS:{}, wbcT:(_key,fallback)=>fallback,
  };
  const injected = `
    wbcUseDisclosure = () => [readExpanded(), () => {}];
    wbcTraceCollapsedSummary = () => ({label:'summary'});
    wbcTraceTimelineItems = entries => { countDetails(); return entries.map(entry => ({kind:'reasoning',text:entry.text})); };
    wbcActivityMessageView = () => { countDetails(); return {visible:true,activity:{}}; };
    export { WbcTraceCard };
  `;
  const {code}=transformSync(source+injected,{loader:'jsx',format:'cjs'});
  const context={module:{exports:{}},require:()=>deps,React,
    readExpanded:()=>expanded,countDetails:()=>count++};
  vm.runInNewContext(code,context);
  const component=context.module.exports[name];
  return {
    render(extra={}){return component({... (name==='WbcTraceCard' ? {trace:[{text:'tool'}],disclosureId:'test'} : {group:{id:'test',activities:[{id:'a'},{id:'b'}]}}), ...extra})},
    setExpanded(value){expanded=value}, get count(){return count},
  };
}
for(const name of ['WbcTraceCard','WbcActivityGroup']) {
  test(`${name} defers unopened details and retains them for closing animation`,()=>{
    const card=mount(name);card.render();card.render();assert.equal(card.count,0);
    card.setExpanded(true);card.render();assert.ok(card.count>0);
    const opened=card.count;card.setExpanded(false);card.render();assert.ok(card.count>opened);
  });
  test(`${name} immediately renders a saved expanded disclosure`,()=>{
    const card=mount(name,true);card.render();assert.ok(card.count>0);
  });
}

test('live summary remains active while closed and first disclosure uses the latest entries',()=>{
  const card=mount('WbcTraceCard');
  const busy=card.render({live:true,running:true,trace:[{text:'first',status:'running'}]});
  assert.equal(busy.props['aria-busy'],'true');
  assert.equal(card.count,0);
  card.setExpanded(true);
  const open=card.render({live:true,running:true,trace:[{text:'latest',status:'running'}]});
  assert.match(renderToStaticMarkup(open),/latest/);
  card.setExpanded(false);
  const completed=card.render({live:true,running:false,trace:[{text:'completed',status:'completed'}]});
  assert.equal(completed.props['aria-busy'],undefined);
  assert.match(renderToStaticMarkup(completed),/completed/);
  card.setExpanded(true);
  assert.match(renderToStaticMarkup(card.render({trace:[{text:'completed'}]})),/completed/);
});
