import test from 'node:test'
import assert from 'node:assert/strict'
import { harness } from './hook-harness.test.mjs'
import { sessionMenuResources } from '../shell/topbar-resource-projection.mjs'

const settle=()=>new Promise(resolve=>setImmediate(resolve));
const event=()=>({clientX:30,clientY:40,preventDefault(){},stopPropagation(){},currentTarget:{getBoundingClientRect:()=>({left:10,right:80,bottom:40,width:70})}});

function menuHarness(loadResources, loadPreview) {
  let obscured=0;
  const h=harness('../shell/topbar-menus.jsx',{sessionMenuResources,wbSetBrowserOverlayObscured:delta=>{obscured+=delta;}});
  const intervals=new Map();let next=0;
  Object.assign(h.context,{setTimeout:h.context.window.setTimeout,clearTimeout:h.context.window.clearTimeout,
    setInterval:fn=>{intervals.set(++next,fn);return next;},clearInterval:id=>intervals.delete(id)});
  Object.assign(h.context.window,{innerWidth:1000,innerHeight:800,matchMedia:()=>({matches:false})});
  h.run(mod=>mod.useTopbarMenus({browserAvailable:true,onLoadSessionResources:loadResources,
    onLoadSessionBrowserPreview:loadPreview,hoverPreview:null,closeSessionPreview(){},readTopbarPortalTheme:()=>({})}));
  return {h,intervals,obscured:()=>obscured};
}

test('session menu ignores superseded resource responses and cancels preview polling on close', async()=>{
  const pending=new Map();let finishPreview;
  const {h,intervals,obscured}=menuHarness(item=>new Promise(resolve=>pending.set(item.id,resolve)),()=>new Promise(resolve=>{finishPreview=resolve;}));
  h.value.openSessionMenu(event(),{kind:'chat',id:'a'});h.flush();assert.equal(obscured(),1);
  h.value.openSessionMenu(event(),{kind:'chat',id:'b'});h.flush();
  pending.get('b')({files:[{id:'b-file'}]});await settle();h.flush();
  pending.get('a')({files:[{id:'a-file'}]});await settle();h.flush();
  assert.equal(h.value.sessionMenu.item.id,'b');assert.equal(h.value.sessionMenu.resources.files[0].id,'b-file');
  assert.equal(intervals.size,1);[...intervals.values()][0]();
  h.value.closeSessionMenu();h.flush();assert.equal(intervals.size,0);
  finishPreview({previewUrl:'late'});await settle();h.flush();
  assert.equal(h.value.sessionMenu,null);assert.equal(obscured(),0);
  h.unmount();assert.equal(h.listeners.size,0);
});

test('overflow close animation can be reopened and unmount clears its timer',()=>{
  const {h,obscured}=menuHarness();
  h.value.openOverflowMenu(event());h.flush();assert.equal(obscured(),1);
  h.value.closeOverflowMenu();h.flush();assert.equal(h.value.overflowMenu.closing,true);assert.equal(h.timers.size,1);
  h.value.openOverflowMenu(event());h.flush();assert.equal(h.timers.size,0);assert.equal(h.value.overflowMenu.closing,false);
  h.value.closeOverflowMenu();h.flush();h.unmount();assert.equal(h.timers.size,0);assert.equal(obscured(),0);
});

test('floating handoff restores original conversations and widths and abandons only stale ownership',()=>{
  const trace=[];const main={id:'a'},split={id:'b'};
  const h=harness('./floating-pane-handoff.jsx',{
    wbcClampSideSplitWidthForPage:value=>value,
    promoteSplitConversation:({commitPromotionNow})=>commitPromotionNow(),
    restoreSplitConversation:({commitRestoreNow})=>{commitRestoreNow();return true;},
  });
  const api=h.run(mod=>mod);
  const context={activeChatIdRef:{current:'a'},pageRef:{current:{querySelector:()=>({getBoundingClientRect:()=>({width:700})})}},
    chatCache:{details:{a:main,b:split}},floatingSplitRestoreRef:{current:null},activeChat:main,splitSide:'left',sideAgentSplitWidth:400,
    splitStateSnapshot:id=>({selected:id}),setFloatingConversationPanelOpen:value=>trace.push(['floating',value]),
    setActiveChat:chat=>trace.push(['chat',chat.id]),setChatLoading:value=>trace.push(['loading',value]),
    selectChat:id=>{trace.push(['select',id]);context.activeChatIdRef.current=id;},
    setSideAgentSplitWidth:width=>trace.push(['width',width]),setSplitSideDirect:side=>trace.push(['side',side]),
    restoreSplitState:(id,state)=>trace.push(['restore',id,state?.selected])};
  api.beginFloatingPanelSplit(context,()=>trace.push(['open']),'b',split);
  const snapshot=context.floatingSplitRestoreRef.current;
  assert.equal(snapshot.activeChat,main);assert.equal(snapshot.chatId,'b');assert.ok(trace.some(item=>item[0]==='width'&&item[1]===700));
  api.abandonFloatingPaneHandoff(context,'b');assert.equal(context.floatingSplitRestoreRef.current,snapshot);
  assert.equal(api.restoreFloatingPanelSplit(context),true);
  assert.equal(context.activeChatIdRef.current,'a');assert.equal(context.floatingSplitRestoreRef.current,null);
  assert.ok(trace.some(item=>item[0]==='width'&&item[1]===400));assert.deepEqual(trace.at(-1),['side','left']);
  assert.equal(api.restoreFloatingPanelSplit(context),false);
  context.floatingSplitRestoreRef.current=snapshot;
  api.abandonFloatingPaneHandoff(context,'c');assert.equal(context.floatingSplitRestoreRef.current,null);
  assert.equal(context.activeChatIdRef.current,'a');
});

test('submission keeps capability filters, command activation and attachment-only sends',()=>{
  const settings=harness('./composer-settings.jsx').run(mod=>mod);
  const h=harness('./composer-submission.jsx',{wbcNormalizeContextActivations:settings.wbcNormalizeContextActivations});
  const prepare=h.run(mod=>mod.prepareComposerSubmission);
  const activated=[];
  const options={command:'',slashPool:[{id:'writer',activation:{kind:'skills',id:'writer'}}],attachments:[],
    contextActivationsRef:{current:{mcpServers:['disabled'],skills:[],pluginPacks:[]}},
    mcpAvailable:false,skillsAvailable:true,pluginPacksAvailable:false,contextCatalog:{skills:[{id:'writer',available:true}]},
    mode:'default',agentManagedModels:true,selectedModelId:'ignored',reasoningEffort:'high',
    soulAvailable:false,workspaceAvailable:false,memoryAvailable:false,composerContextAvailable:true,
    remoteAvailable:false,remoteDeviceIdsRef:{current:['disabled']}};
  const payload=prepare('/writer hello',options,value=>activated.push(value));
  assert.equal(payload.message,'hello');assert.equal(payload.command,'writer');assert.equal(payload.model,'');
  assert.equal(payload.reasoningEffort,'');assert.equal(payload.contextActivations.skills[0],'writer');assert.equal(activated.length,1);
  assert.equal(payload.contextActivations.mcpServers.length,0);assert.equal(payload.remoteDeviceIds.length,0);
  assert.equal('workspaceOverride' in payload,false);assert.equal('soulActive' in payload,false);
  assert.equal(prepare('',options,()=>{}),null);
  const attachment={id:'file'};options.attachments=[attachment];
  assert.equal(prepare('',options,()=>{}).attachments[0],attachment);
});
