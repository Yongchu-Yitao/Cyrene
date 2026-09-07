import test from 'node:test'
import assert from 'node:assert/strict'
import { harness } from './hook-harness.test.mjs'

const settle = () => new Promise(resolve => setImmediate(resolve));

test('plugin collection owner preserves event refresh, loading, errors and cleanup', async () => {
  let listener, unsubscribed = false, fail = false;
  const calls = [];
  const tool = {pack_id:'remote',id:'devices',items_method:'list',presentation:'collection'};
  const h = harness('./rail-plugin-collections.jsx', {
    PluginFrontendService:{call:async (...args)=>{calls.push(args);if(fail)throw Error('offline');return {cards:[{id:'one'}]};}},
    wbcErrorText:e=>e.message,
  });
  h.context.window.CyreneUI={events:{subscribe:fn=>{listener=fn;return()=>{unsubscribed=true;};}}};
  const tools=[tool];
  h.run(mod=>mod.useRailPluginCollections('p',tools,'plugin:remote:devices'));
  assert.equal(h.value.pluginCollectionLoading['remote:devices'],true);
  await settle();h.flush();
  assert.equal(h.value.pluginCollections['remote:devices'][0].id,'one');
  assert.equal(calls[0][3],'p');
  fail=true;listener({type:'remote_desktop_cards_changed'});
  await settle();h.flush();
  assert.equal(h.value.pluginCollectionErrors['remote:devices'],'offline');
  assert.equal(h.value.pluginCollections['remote:devices'][0].id,'one');
  assert.equal(h.value.pluginCollectionLoading['remote:devices'],false);
  h.unmount();assert.equal(unsubscribed,true);
});

test('hover preview replacement, cancellation and unmount retain timer ownership', () => {
  const warmed=[];
  const h=harness('../shell/topbar-hover-preview.jsx');
  h.context.window.innerWidth=900;h.context.window.innerHeight=600;
  h.run(mod=>mod.useTopbarHoverPreview(item=>warmed.push(item.id),()=>({color:'red'})));
  const event={currentTarget:{getBoundingClientRect:()=>({left:200,width:100,bottom:40})}};
  h.value.scheduleSessionPreview(event,{kind:'chat',id:'a'},{},false);
  h.value.scheduleSessionPreview(event,{kind:'chat',id:'b'},{},true);
  assert.equal(h.timers.size,1);
  const [id,callback]=[...h.timers.entries()][0];h.timers.delete(id);callback();h.flush();
  assert.equal(h.value.hoverPreview.item.id,'b');assert.equal(h.value.hoverPreview.left,100);
  h.value.closeSessionPreview();h.flush();assert.equal(h.value.hoverPreview,null);
  h.value.scheduleSessionPreview(event,{kind:'chat',id:'c'},{},false);
  h.unmount();assert.equal(h.timers.size,0);assert.deepEqual(warmed,['a','b','c']);
});

test('composer keyboard preserves IME, slash precedence and newline behavior', () => {
  let shortcuts=null;const observed=[];
  const h=harness('./composer-keyboard.jsx',{workbenchServices:{shortcuts:()=>shortcuts}});
  const handler=h.run(mod=>mod.handleComposerKey);
  const options={slashDraftOpen:false,slashItems:[],slashActiveIndex:0,draft:'/',
    submit:()=>observed.push('send'),setToolsOpen:v=>observed.push(['tools',v]),
    setModelOpen:v=>observed.push(['model',v]),setModelPanel:v=>observed.push(['panel',v]),
    chooseSlashCommand:v=>observed.push(v),setSlashActiveIndex:f=>observed.push(f(0)),
    setSlashDismissedDraft:v=>observed.push(v)};
  const event=(key,extras={})=>({key,preventDefault:()=>observed.push('prevent'),...extras});
  handler(event('Enter',{nativeEvent:{isComposing:true}}),options);assert.deepEqual(observed,[]);
  handler(event('Enter',{shiftKey:true}),options);assert.deepEqual(observed,[]);
  handler(event('Enter'),options);assert.deepEqual(observed,['prevent','send']);observed.length=0;
  const item={id:'plan'};handler(event('Enter'),{...options,slashDraftOpen:true,slashItems:[item]});
  assert.deepEqual(observed,['prevent',item]);observed.length=0;
  shortcuts={matches:(_event,name)=>name==='composer-newline'};
  handler(event('Enter'),options);assert.deepEqual(observed,[]);
});

test('pane promotion and restoration keep reduced-motion commits synchronous', () => {
  let commits=0;
  const h=harness('./pane-promotion-transition.jsx');
  h.context.window.matchMedia=()=>({matches:true});
  const pageRef={current:{querySelector:()=>null}};
  h.run(mod=>{
    mod.promoteSplitConversation({sourceId:'b',activeId:'a',pageRef,commitPromotionNow:()=>commits++});
    assert.equal(mod.restoreSplitConversation({snapshot:{chatId:'b',activeChatId:'a'},pageRef,commitRestoreNow:()=>commits++}),true);
  });
  assert.equal(commits,2);
});

test('clipboard files and item fallback upload attachments without intercepting text or busy input', async () => {
  const uploads=[];
  const h=harness('./composer-attachments.jsx', {
    wbcLoadAttachments:()=>[],wbcSaveAttachments:()=>{},
    workbenchServices:{feedback:()=>({showToast:()=>assert.fail('unexpected upload error')})},
  });
  const options={awaitingAnswer:false,canAttachFiles:true,canAttachImages:true,chatId:'chat',
    draftNamespace:'test',previousChatIdRef:{current:'chat'},running:false,setDraft:()=>{},
    model:{uploadFiles:async files=>{uploads.push([...files]);return files.map(file=>({id:file.name}));}}};
  h.run(mod=>mod.useWbcComposerAttachments(options));
  let prevented=0;
  const event=clipboardData=>({clipboardData,preventDefault:()=>prevented++});
  h.value.onPaste(event({files:[],items:[{kind:'string'}]}));
  assert.equal(prevented,0);assert.equal(uploads.length,0);
  const file={name:'photo.png',type:'image/png'};
  h.value.onPaste(event({files:[file],items:[]}));h.flush();
  assert.equal(h.value.uploading,true);
  await settle();h.flush();
  assert.equal(h.value.attachments[0].id,'photo.png');assert.equal(h.value.uploading,false);
  const fallback={name:'report.txt',type:'text/plain'};
  h.value.onPaste(event({files:[],items:[{kind:'file',getAsFile:()=>fallback}]}));
  await settle();h.flush();
  assert.deepEqual(uploads,[[file],[fallback]]);assert.equal(prevented,2);
  options.running=true;h.flush();h.value.onPaste(event({files:[file]}));
  options.running=false;options.awaitingAnswer=true;h.flush();h.value.onPaste(event({files:[file]}));
  assert.equal(prevented,2);assert.equal(uploads.length,2);h.unmount();
});
