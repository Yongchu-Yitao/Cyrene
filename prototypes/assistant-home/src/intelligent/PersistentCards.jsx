import React, {useState} from 'react';
import {Icon, IconButton, Modal} from '../ui';
import {budgetFor} from '../../server/demo-engine.mjs';
import {MapView} from './MapView';
import './persistent-cards.css';
const busy = w => ['generating','running'].includes(w?.status);

function CardContent({card,w,live,api,events,onOpen,full=false}) {
 const action=(name,payload)=>live&&api.dispatch(card.kind,name,payload);
 const selected=w?.places?.find(p=>p.id===w.selectedPlaceId);
 if(card.type==='agenda')return <><small className="resident-disclosure">演示日程 · 11 月 6 日</small><div className="resident-agenda">{events.filter(e=>!e.done).map(e=><button key={e.id} onClick={()=>onOpen('schedule')}><time>{e.time}</time><span>{e.title}</span></button>)}</div></>;
 if(!w)return <p className="resident-empty">内容正在准备中。</p>;
 if(card.type==='map')return <><div className="resident-mini-map"><MapView key={w.taskId} workspace={w} onSelect={id=>action('select-place',{id})}/></div>{selected&&<p className="resident-muted">{selected.name} · Day {selected.day}</p>}</>;
 if(card.type==='itinerary')return <><div className="resident-trip-title"><strong>{w.city.name}</strong><span>{w.days} 天 · {w.pace==='relaxed'?'轻松节奏':'均衡安排'}</span></div><div className="resident-itinerary">{Array.from({length:w.days},(_,i)=>i+1).map(day=><div key={day}><span>DAY {day}</span><p>{w.places.filter(p=>p.included&&p.day===day).map(p=>p.name).join(' → ')||'自由安排'}</p></div>)}</div>{live&&selected&&<button className="resident-link" disabled={api.pending||busy(api.state?.[card.kind])||!api.connected} onClick={()=>action('toggle-place',{id:selected.id})}>{selected.included?'移出':'加入'}：{selected.name}<Icon name={selected.included?'x':'plus'} size={12}/></button>}</>;
 if(card.type==='budget') {const b=budgetFor(w);return <><div className="resident-budget-total"><strong>¥{b.total.toLocaleString()}</strong><span>/ ¥{w.budget.toLocaleString()}</span></div><p className={b.remaining<0?'amber':'green'}>{b.remaining<0?`超出预算 ¥${-b.remaining}`:`剩余弹性 ¥${b.remaining}`}</p><div className="resident-budget-rows">{[['住宿',b.stay],['餐饮',b.food],['市内交通',b.transport],['景点预留',b.tickets]].map(([label,value])=><div key={label}><span>{label}</span><strong>¥{value}</strong></div>)}</div><small className="resident-disclosure">演示估算 · 不含往返大交通</small></>;}
 if(card.type==='notes')return <div className="resident-notes">{[['交通',w.city.transport],['住宿',w.city.stay],['饮食',w.city.food]].map(([title,text])=><div key={title}><strong>{title}</strong><p>{text}</p></div>)}<small className="resident-disclosure">预置提示 · 未查询实时天气及营业状态</small></div>;
 if(card.type==='sources')return <><p className="resident-research-title">{w.prompt}</p><p className="resident-muted">已选择 {w.sources.filter(s=>s.selected).length} 份资料</p><div className="resident-evidence">{w.sources.filter(s=>s.selected).map(s=><div key={s.id}><strong>{s.title}</strong>{full&&<p>{s.summary}</p>}{s.url&&<a href={s.url} target="_blank" rel="noreferrer">查看来源 <Icon name="arrow-up-right" size={11}/></a>}</div>)}</div></>;
 if(card.type==='risks')return <ul className="resident-risks">{w.result?.risks.map(r=><li key={r}>{r}</li>)}</ul>;
 if(card.type==='result')return w.result ? <><p className="resident-research-title">{w.result.title}</p><p className="resident-muted">{w.result.findings.length} 份资料 · {w.format==='comparison'?'方案对比':'研究简报'}</p><p className="resident-result-summary">{w.result.recommendation}</p>{full&&w.result.findings.map(f=><div className="resident-full-finding" key={f.source}><strong>{f.title}</strong><p>{f.text}</p>{f.url&&<a href={f.url} target="_blank" rel="noreferrer">查看来源</a>}</div>)}</>:<p className="resident-empty">等待新成果。</p>;
 return null;
}

export function PersistentCards({api,events,onOpen,onClose,activeKind,onPin}) {
 const [managing,setManaging]=useState(false),[preview,setPreview]=useState(null);
 const shelf=api.state?.shelf||{pins:[],collapsed:[],dismissed:[]};
 const tasks=activeKind?[api.state?.[activeKind]]:[api.state?.research,api.state?.travel];
 const generated=tasks.flatMap(w=>w?.cards||[]).filter(c=>!shelf.pins.some(p=>p.id===c.id)&&!shelf.dismissed.includes(c.id));
 const act=(name,payload)=>api.dispatch('shelf',name,payload);
 const previewCard=preview&&(shelf.pins.find(c=>c.id===preview.id)||generated.find(c=>c.id===preview.id)||preview);
 const source=card=>card.snapshot||api.state?.[card.kind];
 const isLive=card=>!!card.taskId&&api.state?.[card.kind]?.taskId===card.taskId;
 function renderCard(card,pinned) {
   const w=source(card),live=isLive(card),collapsed=shelf.collapsed.includes(card.id);
   return <section className={`resident-card ${pinned?'user-pinned':'agent-generated'}`} data-card={card.type} data-card-id={card.id} data-origin={pinned?'user':'agent'} aria-label={card.title} key={card.id}>
     <header><button className="resident-card-heading" aria-label={`${collapsed?'展开':'折叠'}${card.title}`} aria-expanded={!collapsed} onClick={()=>act('collapse',{id:card.id})}><Icon name={card.icon} size={15}/><h3>{card.title}</h3><Icon name={collapsed?'chevron-right':'chevron-down'} size={13}/></button><IconButton icon="pin" label={pinned?`取消固定${card.title}`:`固定${card.title}`} className={`icon-button ${pinned?'is-pinned':''}`} disabled={api.pending||busy(api.state?.[card.kind])} onClick={()=>pinned?act('unpin',{id:card.id}):onPin(card)}/><IconButton icon="x" label={`移除卡片${card.title}`} onClick={()=>act('remove',{id:card.id})}/></header>
     {!collapsed&&<div className="resident-card-body"><div className="card-provenance"><span>{pinned?'你固定的':'Agent 生成'}</span><span>{card.kind==='personal'?'个人安排':!live?'已保留 · 原任务':busy(api.state?.[card.kind])?'等待任务更新':pinned&&card.snapshot?.revision!==api.state?.[card.kind]?.revision?'保留上次内容':'与任务同步'}</span></div><CardContent card={card} w={w} live={live} api={api} events={events} onOpen={onOpen}/><div className="resident-card-links"><button onClick={()=>setPreview(card)}>查看内容<Icon name="arrow-up-right" size={12}/></button>{live&&<button onClick={()=>{onOpen(card.kind);if(card.kind==='travel'&&!busy(api.state?.travel))api.dispatch('travel','layout',{layout:card.type==='budget'?'budget':'itinerary'});}}>回到工作区</button>}</div></div>}
   </section>;
 }
 return <aside className="context-panel resident-panel" aria-label="常驻卡片"><header className="resident-header"><div><Icon name="pin" size={18}/><h2>常驻卡片</h2></div><div><IconButton icon="plus" label="管理常驻卡片" aria-expanded={managing} onClick={()=>setManaging(v=>!v)}/><IconButton icon="x" label="收起常驻卡片" onClick={onClose}/></div></header><p className="resident-intro">Agent 随任务生成，你来决定哪些留下。</p>
 {managing&&<div className="resident-picker"><p>点击中间内容旁的图钉，固定到这里。</p><button className="resident-link" onClick={()=>act('pin-agenda')}>固定今日安排<Icon name="plus" size={13}/></button><button className="resident-link" disabled={!shelf.dismissed.length} onClick={()=>act('restore')}>恢复已隐藏的 Agent 卡片</button></div>}
 {api.error&&<p className="resident-error" role="alert">{api.error}<button onClick={api.clearError}>关闭</button></p>}
 <div className="resident-stack"><div className="resident-group-label"><Icon name="pin" size={12}/><span>已固定</span><span>{shelf.pins.length}</span></div>{shelf.pins.map(c=>renderCard(c,true))}{!shelf.pins.length&&<div className="resident-pin-empty">在中间工作区点击「固定到右侧」，内容就会留在这里。</div>}<div className="resident-group-label agent-group-label"><Icon name="sparkles" size={13}/><span>Agent 为任务生成</span><span>{generated.length}</span></div>{generated.map(c=>renderCard(c,false))}{!generated.length&&<p className="resident-empty">{tasks.some(busy)?'正在根据任务准备卡片…':'新任务生成后，相关信息会自动出现在这里。'}</p>}</div>
 <footer className="resident-footer"><Icon name="pin" size={12}/>{shelf.pins.length} 张已固定<span>自动保存</span></footer>
 {previewCard&&<Modal title={previewCard.title} onClose={()=>setPreview(null)} wide><div className="resident-preview"><p className="resident-muted">{isLive(previewCard)?'内容随原任务更新':'保留原任务最后的内容'}</p><CardContent card={previewCard} w={source(previewCard)} live={isLive(previewCard)} api={api} events={events} onOpen={onOpen} full/></div></Modal>}
 </aside>;
}
