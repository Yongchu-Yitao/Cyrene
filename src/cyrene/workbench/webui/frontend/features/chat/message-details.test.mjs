import { createDisclosureSubscriptions } from './disclosure-subscriptions.mjs';
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
    createDisclosureSubscriptions,
    wbcTraceTimelineItems(entries) {
      count++;
      return entries.map(entry => entry.kind === 'tool' ? {kind:'trace',entry} : {kind:'reasoning',text:entry.text});
    },
    useWorkbenchI18n(){}, useWbcRef(initial){return ref ||= {current:initial}},
    WBC_ICONS:{}, wbcT:(_key,fallback)=>fallback,
    workbenchServices:{i18n:()=>({getLang:()=>'en'})},
    wbcToolPresentationKind:()=> 'generic',
    wbcToolPresentationText:()=> '',
    wbcToolPreviewText:preview=>preview,
  };
  const injected = `
    wbcUseDisclosure = () => [readExpanded(), () => {}];
    wbcTraceCollapsedSummary = () => ({label:'summary'});
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

test('Bash renders the full command and failure state while other tools keep their preview', () => {
  const card = mount('WbcTraceCard', true);
  const command = "python3 - <<'PY'\n    print({})\nPY\n# " + 'x'.repeat(450);
  const trace = [
    {kind:'tool', toolCallId:'bash', tool:'Bash', text:'执行命令', status:'completed', failed:false, exitCode:1,
      command, preview:'exit_code: 1, stderr: Traceback'},
    {kind:'tool', toolCallId:'read', text:'Read', status:'completed', preview:'read result'},
  ];
  const html = renderToStaticMarkup(card.render({trace}));

  assert.match(html, /class="failed presentation-generic"/);
  assert.ok(html.includes("<code>python3 - &lt;&lt;&#x27;PY&#x27;\n    print({})\nPY\n# " + 'x'.repeat(450) + '</code>'));
  assert.doesNotMatch(html, /exit_code|Traceback/);
  assert.match(html, /\(read result\)/);
});
