import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import vm from 'node:vm'
import {readFileSync} from 'node:fs'
import {transformSync} from 'esbuild'

const code=transformSync(readFileSync(new URL('./messages.jsx',import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code;
function buttons(element) {
  if(!React.isValidElement(element))return [];
  return (element.type==='button'?[element]:[]).concat(React.Children.toArray(element.props.children).flatMap(buttons));
}

test('terminal error notice renders a translated diagnostic action without a global translator',()=>{
  const dependencies={WBC_ICONS:{alert:null,refresh:null},wbcT:(key)=>'translated:'+key,
    wbcErrorText:()=> 'controlled terminal error',wbcAgentErrorPresentation:()=>null};
  const context={module:{exports:{}},require:()=>dependencies,React};
  vm.runInNewContext(code,context);
  for(const kind of ['message','memory','load']) {
    let diagnosed=0,retried=0;
    const element=context.module.exports.WbcErrorNotice({message:'failed',kind,onDiagnose:()=>diagnosed++,onRetry:()=>retried++});
    assert.equal(element.props.role,'alert');
    const actions=buttons(element);
    const diagnostic=actions.find(button=>button.props.children==='translated:doctor.title');
    assert.ok(diagnostic);diagnostic.props.onClick();assert.equal(diagnosed,1);
    const retry=actions.find(button=>button!==diagnostic);assert.ok(retry);retry.props.onClick();assert.equal(retried,1);
  }
});
