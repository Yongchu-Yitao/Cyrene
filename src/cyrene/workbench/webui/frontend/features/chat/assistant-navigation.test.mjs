import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';

test('all Cyrene navigation paths request home even when the tab is already active', () => {
  const events=[], pages=[];
  const h=harness('../shell/navigation-controller.jsx', {React:{useEffect:()=>{}}});
  h.context.CustomEvent=class {constructor(type) {this.type=type;}};
  h.context.window.dispatchEvent=event=>events.push(event.type);
  for (const active of ['cyrene','chat']) {
    h.run(mod=>mod.createWorkbenchNavigationActions(active, page=>pages.push(page),()=>{},()=>{},()=>{},
      {current:{}},()=>active,['work','cyrene']));
    h.value.openPage('cyrene');
  }
  assert.deepEqual(events,['cyrene:assistant-home','cyrene:assistant-home']);
  assert.deepEqual(pages,['cyrene']);
});

test('shared toolbar retains independent action, search and collapse controls', () => {
  const React={createElement(type,props,...children) {return {type,props:{...props,children}};}};
  const actions=[],queries=[];
  const h=harness('./rail-toolbar.jsx',{React,WBC_ICONS:{plus:'plus',search:'search',sidebar:'sidebar'}});
  function all(node,type) {
    if (!node) return [];
    if (Array.isArray(node)) return node.flatMap(n=>all(n,type));
    return (node.type===type?[node]:[]).concat(all(node.props?.children,type));
  }
  h.run(mod=>mod.WbcRailToolbar({query:'',actionLabel:'Home',onAction:()=>actions.push('home'),
    onQueryChange:value=>queries.push(value),onToggleCollapsed:()=>actions.push('collapse')}));
  const buttons=all(h.value,'button');
  assert.equal(buttons.length,2);
  buttons.forEach(button=>button.props.onClick());
  all(h.value,'input')[0].props.onChange({target:{value:'report'}});
  assert.deepEqual(actions,['home','collapse']);
  assert.deepEqual(queries,['report']);
});


test('home reuses the mounted conversation and restores it only when missing', () => {
  const fallback={left:[{id:'chat:main',kind:'chat',payload:'main'}],right:[]};
  const h=harness('./pane-layout-transforms.jsx',{wbcDefaultPaneLayout:()=>fallback});
  const card={id:'existing-main',kind:'chat',payload:'main'};
  const layout={left:[card],right:[]};
  h.run(mod=>mod.restoreConversationPaneLayout(layout,'main'));
  assert.equal(h.value,layout);
  h.run(mod=>mod.restoreConversationPaneLayout({left:[card],right:[{kind:'file'}]},'main'));
  assert.equal(h.value.left[0],card);
  h.run(mod=>mod.restoreConversationPaneLayout({left:[{kind:'file'}],right:[]},'main'));
  assert.equal(h.value,fallback);
});
