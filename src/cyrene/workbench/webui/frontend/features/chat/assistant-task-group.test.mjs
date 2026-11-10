import test from 'node:test';
import assert from 'node:assert/strict';
import { wbcChatRailVisualState } from './rail-status.mjs';
import {harness} from './hook-harness.test.mjs';
function find(element, type) {
  if (!element) return [];
  if (Array.isArray(element)) return element.flatMap(e=>find(e,type));
  return (element.type===type?[element]:[]).concat(find(element.props?.children,type));
}
test('task group opens its conversation only on expansion, preserves collapse and supports search',()=>{
  const storage = new Map();
  const React = {createElement(type,props,...children){return {type,props:{...props,children}};}};
  const WbcRailGroupToggle = 'group-toggle';
  const h=harness('../shell/assistant-task-group.jsx',{React,createPortal:node=>node,readTopbarPortalTheme:()=>({}),trackTaskGroupClip:()=>()=>{},WbcChatMenuActions:'menu-actions',wbcChatRailVisualState,WbcRailGroupToggle,WbcRailGroupContent:'group-content',TaskConversationIcon:'task-icon',localStorage:{
    getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),
  }});
  let query='', opens=0, hasChildren=true, status='running';
  h.run((mod,hooks)=>{
    React.useRef=hooks.useWbcRef;
    React.useState=hooks.useWbcState;
    React.useEffect=hooks.useWbcEffect;
    return mod.AssistantTaskGroup({task:{session_id:'a',title:'Task',status},chat:{id:'a'},menuActions:{pinnedChatIds:['a']},query,matches:true,hasChildren,onOpen:()=>opens++,children:'resources'});
  });
  assert.match(h.value.props.className, /status-running/);
  status='planning';h.flush();
  assert.match(h.value.props.className, /status-planning/);
  status='completed';h.flush();
  assert.match(h.value.props.className, /status-completed/);
  const toggle=()=>find(h.value,'group-toggle')[0];
  assert.equal(toggle().props.expanded,false);
  toggle().props.onClick();h.flush();
  assert.equal(toggle().props.expanded,true);
  assert.equal(opens,1);
  toggle().props.onClick();h.flush();
  assert.equal(toggle().props.expanded,false);
  assert.equal(opens,1);
  assert.equal(storage.get('cyrene.assistant.task-group.a'),'collapsed');
  query='report';h.flush();assert.equal(toggle().props.expanded,true);
  toggle().props.onClick();h.flush();assert.equal(toggle().props.expanded,false);
  query='';h.flush();assert.equal(toggle().props.expanded,false);
  hasChildren=false;h.flush();
  assert.equal(toggle().props.expandable,false);
  assert.equal(toggle().props['aria-controls'],undefined);
  toggle().props.onClick();h.flush();
  assert.equal(opens,2);
  assert.equal(toggle().props.expanded,false);
  let prevented=0;
  toggle().props.onContextMenu({preventDefault(){prevented++;},stopPropagation(){},clientX:790,clientY:590,currentTarget:{getBoundingClientRect:()=>({left:20,bottom:40})}});
  h.flush();
  assert.equal(prevented,1);
  assert.equal(opens,2);
  const menu=find(h.value,'menu-actions')[0];
  assert.equal(menu.props.chat.id,'a');
  assert.equal(menu.props.isPinned,true);
  menu.props.onClose();h.flush();
  assert.equal(find(h.value,'menu-actions').length,0);
});
