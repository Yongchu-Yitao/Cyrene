// Structured card descriptions are produced by the local demo runner.
const clone = value => JSON.parse(JSON.stringify(value));
const labels = {map:'地图',itinerary:'每日行程',budget:'旅行预算',notes:'出行提示',sources:'研究资料',result:'研究成果',risks:'风险与限制'};
const icons = {map:'world',itinerary:'calendar',budget:'briefcase',notes:'sparkles',sources:'book-2',result:'file-text',risks:'alert-circle'};
export function availableCardTypes(w) {
  if (!w || w.status==='idle') return [];
  if (w.kind==='travel') return ['map','itinerary','budget','notes'];
  return ['sources',...(w.result?['result']:[]),...(w.result?.risks?.length?['risks']:[])];
}
export function cardDescriptor(w,type) {
  if (!availableCardTypes(w).includes(type)) throw Error('当前任务还没有这项内容。');
  return {id:`${w.taskId}:${type}`,taskId:w.taskId,kind:w.kind,type,icon:icons[type],title:w.kind==='travel'?`${w.city.name} · ${labels[type]}`:labels[type]};
}
export function agentCards(w) {
  if (!w || w.status==='idle' || ['generating','running'].includes(w.status)) return [];
  if (w.kind==='research') return (w.result?['result',...(w.result.risks.length?['risks']:[])]:['sources']).map(type=>cardDescriptor(w,type));
  const types=['itinerary'];
  if (/预算|省钱|经济/.test(w.prompt)||w.economy||w.layout==='budget') types.push('budget');
  if (/住宿|美食|吃|交通|提示/.test(w.prompt+' '+w.messages?.findLast(m=>m.role==='user')?.text)) types.push('notes');
  return [...new Set([...types,...(w.extraCardTypes||[])])].filter(type=>availableCardTypes(w).includes(type)).map(type=>cardDescriptor(w,type));
}
function snapshot(w) {const {messages,log,cards,...content}=w;return clone(content);}
export function refreshCards(session) {
  session.shelf ||= {revision:0,pins:[],dismissed:[],collapsed:[]};
  for (const kind of ['research','travel']) {
    const w=session[kind];w.taskId ||= crypto.randomUUID();w.cards=agentCards(w);
  }
  for (const pin of session.shelf.pins) {
    const w=session[pin.kind];
    if(w?.taskId===pin.taskId && !['generating','running'].includes(w.status) && availableCardTypes(w).includes(pin.type) && w.revision!==pin.snapshot?.revision) {
      pin.snapshot=snapshot(w);session.shelf.revision++;
    }
  }
  return session;
}
export function applyCardAction(session,action,payload={}) {
  const shelf=session.shelf;
  if(action==='pin') {
    const w=session[payload.kind];
    if(!w||w.taskId!==payload.taskId)throw Error('任务已切换，请从当前工作区重新固定。');
    if(['generating','running'].includes(w.status))throw Error('请等待内容生成后再固定。');
    const descriptor=cardDescriptor(w,payload.type);
    if(!shelf.pins.some(p=>p.id===descriptor.id)) {
      if(shelf.pins.length>=20)throw Error('最多固定 20 张卡片，请先移除不再需要的内容。');
      shelf.pins.push({...descriptor,snapshot:snapshot(w)});
    }
    shelf.dismissed=shelf.dismissed.filter(id=>id!==descriptor.id);
    shelf.collapsed=shelf.collapsed.filter(id=>id!==descriptor.id);
  } else if(action==='pin-agenda') {
    if(!shelf.pins.some(p=>p.id==='personal:agenda'))shelf.pins.push({id:'personal:agenda',kind:'personal',type:'agenda',title:'今日安排',icon:'calendar'});
  } else if(action==='restore') { shelf.dismissed=[];
  } else {
    const known=[...shelf.pins,...session.research.cards,...session.travel.cards];
    if(!known.some(p=>p.id===payload.id))throw Error('卡片已失效，请刷新后重试。');
    if(action==='remove') {shelf.pins=shelf.pins.filter(p=>p.id!==payload.id);if(!shelf.dismissed.includes(payload.id))shelf.dismissed.push(payload.id);}
    else if(action==='unpin') {shelf.pins=shelf.pins.filter(p=>p.id!==payload.id);}
    else if(action==='collapse') {shelf.collapsed=shelf.collapsed.includes(payload.id)?shelf.collapsed.filter(id=>id!==payload.id):[...shelf.collapsed,payload.id];}
    else throw Error('不支持的卡片操作。');
  }
  shelf.dismissed=shelf.dismissed.slice(-100);shelf.collapsed=shelf.collapsed.slice(-100);
}
