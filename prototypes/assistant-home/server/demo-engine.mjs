import {researchSources,destinations} from './demo-data.mjs';
const clone=x=>JSON.parse(JSON.stringify(x));
export function emptyWorkspace(kind){return {kind,taskId:crypto.randomUUID(),revision:0,status:'idle',title:kind==='research'?'调研工作区':'旅行工作区',prompt:'',messages:[],log:[],surface:null,result:null};}
export function newSession(){return {research:emptyWorkspace('research'),travel:emptyWorkspace('travel')};}
function addMessage(w,role,text){w.messages.push({id:crypto.randomUUID(),role,text,time:Date.now()});w.messages=w.messages.slice(-40);}
function number(text,regex,fallback,min,max){const n=Number(text.match(regex)?.[1]);return Number.isFinite(n)&&n>=min?Math.min(max,n):fallback;}
function plain(text,max=1200){return String(text||'').trim().slice(0,max);}
export function arrange(w){
 if(w.kind!=='travel'||!w.places)return;
 for(let day=1;day<=w.days;day++){let minutes=9*60+30;for(const place of w.places.filter(p=>p.included&&p.day===day)){place.time=String(Math.floor(minutes/60)).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0');minutes+=place.minutes+(w.pace==='relaxed'?60:30);if(minutes>12*60&&minutes<13*60)minutes=13*60;}}
}
export function describeSurface(w){if(w.status==='idle')return null;
 if(w.kind==='research')return {version:1,title:w.result?(w.format==='comparison'?'方案对比':'研究简报'):'资料工作台',blocks:w.result?[{type:w.format==='comparison'?'comparison':'report',id:'result'},{type:'sources',id:'references',compact:true}]:[{type:'sources',id:'evidence'},{type:'requirements',id:'brief'}]};
 return {version:1,title:w.city?.name+' · 旅行工作区',blocks:[{type:'map',id:'places-map'},{type:w.layout==='budget'?'budget':'itinerary',id:'trip-details'},{type:'travel-notes',id:'essentials'}]};
}
function researchResult(w){const selected=w.sources.filter(s=>s.selected);return {title:w.prompt.replace(/[。！]$/,'').slice(0,65),summary:`围绕“${w.prompt}”，整理了 ${selected.length} 份选定资料。${w.requirements?'本次重点：'+w.requirements:'重点比较交互方式、任务状态和实现成本。'}`,findings:selected.map(s=>({title:s.title,text:s.finding||s.summary,source:s.id,url:s.url})),recommendation:w.custom?'先核实选定资料中的事实与结论，再补充缺失信息。此处为整理演示，没有进行实时网页调研。':'采用固定应用外壳与动态工作区。优先复用稳定组件，通过结构化动作连接任务执行；复杂场景再使用隔离的专用视图。',risks:w.focusRisk?['界面与 Agent 状态不一致：服务端维护权威状态，动作携带版本号。','重复执行：以 requestId 去重，提交中禁用重复操作。','生成内容不可验证：保留来源与演示标识，避免把推测当成事实。']:[],format:w.format,revision:w.revision+1};}
export function budgetFor(w){const days=w.days||3, factor=w.city?.name==='京都'?1.65:1,food=Math.round(days*(w.economy?70:120)*factor),stay=Math.round(Math.max(0,days-1)*(w.economy?180:300)*factor),transport=Math.round(days*(w.economy?25:50)*factor),tickets=w.places.filter(p=>p.included).reduce((s,p)=>s+p.cost,0);const total=food+stay+transport+tickets;return {food,stay,transport,tickets,total,remaining:w.budget-total};}
function selectedNames(w){return w.places.filter(p=>p.included&&(!w.dayFilter||p.day===w.dayFilter)).map(p=>p.name);}
export function act(w,type,payload={}){
 const text=plain(payload.text).replace(/([一二两三四五])\s*([天日])/g,(_,n,u)=>(n==='两'?2:'一二三四五'.indexOf(n)+1)+u); if(['generating','running'].includes(w.status)&&!['select-place','filter-day'].includes(type))throw Error('当前任务仍在生成，请完成后再操作。');
 if(type==='reset')return {workspace:emptyWorkspace(w.kind)};
 if(type==='start'){
  if(!text)throw Error('请先描述你想完成的事情。');
  const next=emptyWorkspace(w.kind),revision=w.revision;for(const key of Object.keys(w))delete w[key];Object.assign(w,next,{revision,prompt:text,status:'generating'});addMessage(w,'user',text);
  if(w.kind==='research'){
   w.custom=!/agent|ai|人工智能|智能|助理|界面|交互|mcp|chatgpt/i.test(text);w.sources=w.custom?[]:clone(researchSources);w.requirements='';w.format=/对比|比较|表格/.test(text)?'comparison':'report';w.focusRisk=false;w.title='调研 · '+text.slice(0,22);
  }else{
   const city=Object.values(destinations).find(c=>c.aliases.some(alias=>text.toLowerCase().includes(alias)));
   if(!city)throw Error('当前演示支持杭州、上海、成都和京都。请选择其中一个目的地。');
   w.city=clone(city);w.days=number(text,/(\d+)\s*[天日]/,3,1,5);w.budget=number(text,/(?:预算|控制在|不超过)\s*[¥￥]?\s*(\d+)/,2000,100,100000);w.economy=/省钱|便宜|经济/.test(text);w.pace=/轻松|慢|不赶/.test(text)?'relaxed':'balanced';w.places=clone(city.places).map(p=>({...p,included:p.day<=w.days}));w.layout='itinerary';w.selectedPlaceId=w.places[0].id;w.dayFilter=0;w.title=`${city.name} ${w.days} 日旅行`;w.focusRisk=false;
  }
  arrange(w);w.surface=describeSurface(w);return {run:'prepare'};
 }
 if(w.status==='idle')throw Error('请先生成一个工作区。');
 if(w.kind==='research'){
  if(type==='toggle-source'){const s=w.sources.find(s=>s.id===payload.id);if(!s)throw Error('资料不存在。');s.selected=!s.selected;w.result=null;w.status='review';}
  else if(type==='add-source'){const title=plain(payload.title,120),summary=plain(payload.summary,2000),url=plain(payload.url,500);if(!title||!summary)throw Error('请填写资料名称与摘要。');if(url&&!/^https?:\/\//i.test(url))throw Error('来源链接需以 http:// 或 https:// 开头。');w.sources.push({id:crypto.randomUUID(),title,summary,finding:summary,url,publisher:'你提供的资料',kind:'手动补充',tags:['用户资料'],selected:true});w.result=null;w.status='review';}
  else if(type==='generate-report'){if(!w.sources.some(s=>s.selected))throw Error('请至少选择一份资料，再继续生成。');w.requirements=plain(payload.requirements);w.format=['report','comparison'].includes(payload.format)?payload.format:w.format;w.focusRisk=/风险|限制|安全/.test(w.requirements);w.status='running';addMessage(w,'user',`使用 ${w.sources.filter(s=>s.selected).length} 份资料继续调研。${w.requirements}`);return {run:'report'};}
  else if(type==='chat'){
   if(!text)throw Error('请输入补充要求。');addMessage(w,'user',text);
   if(/重新|重选|回到资料/.test(text)){w.status='review';w.result=null;addMessage(w,'assistant','已回到资料选择。你可以调整来源、补充摘要，再生成新的简报。');}
   else if(w.sources.some(s=>s.selected)){w.format=/表格|对比|比较/.test(text)?'comparison':/简报|报告|正文/.test(text)?'report':w.format;w.requirements=text;w.focusRisk=/风险|限制|安全/.test(text);w.status='running';return {run:'report'};}
   else addMessage(w,'assistant','这个主题尚未内置演示资料。请添加来源链接和摘要，我会基于你提供的内容组装简报。');
  }else if(type==='edit-sources'){w.result=null;w.status='review';}else throw Error('不支持的调研操作。');
 }else{
  if(type==='select-place'){if(!w.places.some(p=>p.id===payload.id))throw Error('地点不存在。');w.selectedPlaceId=payload.id;}
  else if(type==='filter-day'){w.dayFilter=Math.max(0,Math.min(w.days,Number(payload.day)||0));}
  else if(type==='toggle-place'){const p=w.places.find(p=>p.id===payload.id);if(!p)throw Error('地点不存在。');p.included=!p.included;if(p.included&&p.day>w.days)p.day=w.days;addMessage(w,'assistant',`${p.name}已${p.included?'加入':'移出'}行程。地图、每日安排和预算已同步。`);}
  else if(type==='move-place'){const p=w.places.find(p=>p.id===payload.id);if(!p)throw Error('地点不存在。');p.day=Math.max(1,Math.min(w.days,Number(payload.day)||1));}
  else if(type==='settings'){w.days=Math.max(1,Math.min(5,Number(payload.days)||w.days));w.budget=Math.max(100,Math.min(100000,Number(payload.budget)||w.budget));w.places.forEach(p=>{if(p.day>w.days)p.included=false;});w.dayFilter=0;w.title=`${w.city.name} ${w.days} 日旅行`;w.pace=payload.pace==='relaxed'?'relaxed':'balanced';addMessage(w,'assistant',`已更新为 ${w.days} 天、预算 ¥${w.budget}。${w.days>3?'第 4–5 天留作自由活动，可把地点移动过去。':''}`);}
  else if(type==='layout'){w.layout=payload.layout==='budget'?'budget':'itinerary';}
  else if(type==='chat'){
   if(!text)throw Error('请输入补充要求。');addMessage(w,'user',text);
   if(/(?:生成|添加|显示|创建).*卡片/.test(text)) {
    const entry=[['map','地图'],['budget','预算'],['itinerary','行程'],['notes','出行提示']].find(([,label])=>text.includes(label))||(/住宿|美食|交通/.test(text)?['notes','出行提示']:null);
    if(entry){w.extraCardTypes=[...new Set([...(w.extraCardTypes||[]),entry[0]])];addMessage(w,'assistant',`已根据当前旅行任务生成“${entry[1]}”卡片，放在右侧。你可以点图钉把它保留。`);}
    else addMessage(w,'assistant','当前演示可以生成地图、行程、预算和出行提示卡片，暂不提供实时天气或票价卡片。');
    return {};
   }
   const named=w.places.find(p=>text.includes(p.name));const ordinal=text.match(/第([一二三四五六1-6])个/);const idx=ordinal?'一二三四五六'.indexOf(ordinal[1])>=0?'一二三四五六'.indexOf(ordinal[1]):Number(ordinal[1])-1:-1;const target=named||(idx>=0?w.places.filter(p=>p.included)[idx]:w.places.find(p=>p.id===w.selectedPlaceId));
   let changed=[];if(/移除|删除|不去|去掉/.test(text)){if(!target)throw Error('请先选中一个地点。');target.included=false;changed.push(`移除了${target.name}`);}
   if(/加入|加回|恢复/.test(text)&&target){target.included=true;target.day=Math.min(w.days,target.day);changed.push(`加入了${target.name}`);}
   const budget=number(text,/(?:预算|控制在|不超过)\s*[¥￥]?\s*(\d+)/,0,100,100000);if(budget){w.budget=budget;changed.push(`预算设为 ¥${budget}`);}
   if(/省钱|便宜|经济/.test(text)){w.economy=true;w.layout='budget';changed.push('切换到经济预算');}
   const days=number(text,/(\d+)\s*[天日]/,0,1,5);if(days){w.days=days;w.places.forEach(p=>{if(p.day>days)p.included=false;});w.dayFilter=0;changed.push(`调整为 ${days} 天`);}
   if(/轻松|慢一点|休息/.test(text)){w.pace='relaxed';changed.push('增加了每站之间的休息余量');}
   if(/预算|花费|费用/.test(text))w.layout='budget';if(/行程|每天|安排/.test(text)&&!/预算/.test(text))w.layout='itinerary';
   w.title=`${w.city.name} ${w.days} 日旅行`;addMessage(w,'assistant',changed.length?`${changed.join('，')}。界面已更新；费用为演示估算，不含往返目的地的大交通。`:`当前选中${target?'「'+target.name+'」':'整个行程'}，已安排 ${selectedNames(w).length} 个地点。你可以说“移除这个地点”“预算 1500 元”“节奏轻松一点”或“显示预算”。`);
  }else throw Error('不支持的旅行操作。');
 }
 arrange(w);w.surface=describeSurface(w);return {};
}
export function finish(w,run){if(run==='report'){w.result=researchResult(w);w.status='complete';addMessage(w,'assistant',`已基于 ${w.sources.filter(s=>s.selected).length} 份资料生成${w.format==='comparison'?'对比视图':'研究简报'}。${w.focusRisk?'已加入风险与限制。':''}你可以继续通过聊天调整，或下载成果。`);}else{w.status='review';addMessage(w,'assistant',w.kind==='research'?(w.custom?'已生成资料工作台。请补充来源与摘要；本次演示不会虚构检索结果。':`已准备 ${w.sources.length} 份官方资料的预置摘要。选择需要的来源、补充要求，再继续生成简报。`):`已为${w.city.name}生成地图、${w.days}日行程和预算。点击地图上的地点可以选中它；你的后续指令会带上当前选择。`);}w.surface=describeSurface(w);}
export function exportMarkdown(w){if(w.kind==='research'){if(!w.result)throw Error('请先生成简报。');return `# ${w.result.title}\n\n${w.result.summary}\n\n## 主要发现\n\n${w.result.findings.map((f,i)=>`### ${i+1}. ${f.title}\n\n${f.text}\n\n来源：${f.url||'用户提供的摘要'}`).join('\n\n')}\n\n## 建议\n\n${w.result.recommendation}\n\n${w.result.risks.length?'## 风险与限制\n'+w.result.risks.map(r=>'- '+r).join('\n'):''}\n\n说明：演示运行器根据预置或用户提供的资料组装，未调用语言模型或实时检索。`;}
 const b=budgetFor(w);return `# ${w.title}\n\n${w.prompt}\n\n${Array.from({length:w.days},(_,i)=>`## 第 ${i+1} 天\n\n${w.places.filter(p=>p.included&&p.day===i+1).map(p=>`- ${p.time} ${p.name} · ${p.minutes} 分钟\n  ${p.description}`).join('\n')||'自由活动，可添加或移动地点。'}`).join('\n\n')}\n\n## 预算估算\n\n住宿 ¥${b.stay}；餐饮 ¥${b.food}；市内交通 ¥${b.transport}；景点预留 ¥${b.tickets}。合计 ¥${b.total} / 预算 ¥${w.budget}。\n\n不含往返目的地大交通。地点为预置示例，坐标为近似位置；时间与费用是演示估算，未查询实时票价、天气、开放或预约状态。\n\n目的地官方信息：${w.city.official}`;}
