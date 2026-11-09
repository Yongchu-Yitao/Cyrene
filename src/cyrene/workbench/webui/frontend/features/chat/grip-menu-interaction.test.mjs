import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';
const React={createElement:(type,props,...children)=>({type,props:{...props,children}})};
function find(node,predicate){if(!node)return null;if(Array.isArray(node))return node.map(n=>find(n,predicate)).find(Boolean)||null;if(predicate(node))return node;return find(node.props?.children,predicate);}
function mount(logo=true, props={}, extras={}){
  const h=harness('./split-pane.jsx',{React,WBC_ICONS:{chevronLeft:'left',chevronRight:'right',sidebar:'panel'},workbenchServices:{browserOverlays:()=>null},...extras});
  h.context.document.body={};h.context.window.ReactDOM={createPortal:node=>node};
  const grip={dataset:{},getBoundingClientRect:()=>({left:100,width:600,bottom:56}),querySelector:()=>logo?{getBoundingClientRect:()=>({left:382,width:36,bottom:48})}:null};
  h.run(mod=>mod.WbcSplitGripBar({dragSource:'assistant',menuDisabled:false,onOpenConversationPanel:()=>{},...props}));
  find(h.value,n=>n.props?.className==='wbc-side-split-grip-bar').props.ref.current=grip;
  find(h.value,n=>n.props?.className==='wbc-split-grip-bar-host').props.ref.current={closest:()=>null,contains:()=>false};
  return {h,grip,button:()=>find(h.value,n=>n.props?.className==='wbc-side-split-grip-bar'),menu:()=>find(h.value,n=>n.props?.role==='menu')};
}
test('logo click toggles the original menu below the stable logo slot; Escape closes it',()=>{
  const {h,grip,button,menu}=mount();
  assert.equal(menu(),null);
  button().props.onClick({currentTarget:grip});h.flush();
  assert.ok(menu());assert.equal(menu().props.style['--wbc-split-grip-menu-top'],'52px');assert.equal(menu().props.style['--wbc-split-grip-menu-left'],'400px');
  button().props.onClick({currentTarget:grip});h.flush();assert.equal(menu(),null);
  button().props.onKeyDown({key:'Enter',preventDefault(){}});h.flush();assert.ok(menu());
  h.listeners.get('keydown')({key:'Escape'});h.flush();assert.equal(menu(),null);h.unmount();
});
test('dragging never opens a menu and ordinary bars retain their original anchor',()=>{
  const {h,grip,button,menu}=mount(false);
  grip.dataset.wbcPointerDragged='true';button().props.onClick({currentTarget:grip});h.flush();assert.equal(menu(),null);
  button().props.onClick({currentTarget:grip});h.flush();assert.equal(menu().props.style['--wbc-split-grip-menu-top'],'60px');h.unmount();
});

test('single-pane logo drag never starts a pane ghost; split mode still does',()=>{
  for (const single of [true,false]) {
    let ghosts=0,moves=0,finishes=0;
    const {h,grip,button}=mount(true,{logoOnlyDrag:single,menuDisabled:single,onSplitDragStart:()=>ghosts++},
      {createGripLogoDrag:()=>({move:()=>moves++,finish:()=>finishes++,destroy(){}})});
    const event={currentTarget:grip,button:0,pointerId:1,clientX:400,clientY:30,preventDefault(){}};
    button().props.onPointerDown(event);
    button().props.onPointerMove({...event,clientX:450,clientY:80});
    button().props.onPointerMove({...event,clientX:460,clientY:90});
    assert.equal(ghosts,single?0:1);assert.equal(moves,single?2:0);
    button().props.onPointerUp(event);assert.equal(finishes,single?1:0);
    assert.equal(grip.dataset.wbcPointerDragged,'true');h.unmount();
  }
});
