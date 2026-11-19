import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { CardPinButton, requestCard, WbcInlineCard } from './assistant-cards.jsx';
import { conversationCardsUrl } from './pinned-card-model.mjs';
import { curlPage, shufflePages } from './card-page-motion.mjs';
import { WBC_ICONS } from './icons.jsx';
import { useWorkbenchI18n } from '../../workbench-i18n.jsx';

function placementKey(group) {
  return JSON.stringify([group.format, group.pages.map(p => [p.card_id, p.snapshot ? p.revision : null])]);
}

function useGroupPlacement(chatId, group, initiallyPinned) {
  const [pinned, setPinned] = useState(initiallyPinned);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const key = placementKey(group), url = conversationCardsUrl(chatId);
  useEffect(() => {
    const controller = new AbortController();
    let sequence = 0;
    const refresh = async () => {
      const id = ++sequence;
      try {
        const result = await requestCard(url + '?scope=pinned&limit=1', {signal: controller.signal});
        if (!controller.signal.aborted && id === sequence)
          setPinned((result.groups || []).some(row => placementKey(row.group) === key));
      } catch (e) { if (!controller.signal.aborted) setError(e.message); }
    };
    refresh();
    window.addEventListener('cyrene:card-placement-changed', refresh);
    window.addEventListener('focus', refresh);
    return () => {
      controller.abort();
      window.removeEventListener('cyrene:card-placement-changed', refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [key, url]);
  async function toggle() {
    setBusy(true);
    try {
      const result = await requestCard(url + '/groups/pin', {method:'PUT', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({pinned:!pinned, group})});
      setPinned(result.pinned);
      setError('');
      window.dispatchEvent(new CustomEvent('cyrene:card-placement-changed'));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return {pinned, busy, error, toggle};
}

function BookCover({group, open}) {
  return <div className="wbc-folio-board" data-palette={group.cover?.palette} data-motif={group.cover?.motif}>
    <div className="wbc-folio-cover"><div className="wbc-folio-art" aria-hidden="true"><i/><i/><i/></div>
      <strong>{group.title}</strong><p>{group.subtitle}</p><button type="button" onClick={open}>翻开手册　›</button>
    </div>
    <div className="wbc-folio-endpaper" aria-hidden="true"><span>{group.title}</span></div>
  </div>;
}

export function ConversationCardGroup({group, chatId, inPinnedColumn=false}) {
  const {t} = useWorkbenchI18n();
  const isBook = group.format === 'book', count = group.pages.length;
  const [page, setPage] = useState(0), [covered, setCovered] = useState(isBook);
  const [turningCover, setTurningCover] = useState(false), [directory, setDirectory] = useState(false);
  const [titles, setTitles] = useState({});
  const recordTitle = useCallback((id, title) => setTitles(old => old[id] === title ? old : {...old,[id]:title}), []);
  const stage = useRef(null), nodes = useRef([]), motion = useRef(null), previous = useRef(0);
  const coverLink = useRef(null), toc = useRef(null), touch = useRef(null);
  const pin = useGroupPlacement(chatId, group, inPinnedColumn);
  const label = index => titles[group.pages[index].card_id] || ('第 ' + (index+1) + ' 页');

  // Keep expanded content's space on collapse. A new column width gets a fresh measurement.
  useLayoutEffect(() => {
    const el = stage.current;
    const viewport = el.closest('.wbc-thread, .wbc-pinned-card-column');
    const footer = el.parentElement.querySelector('.wbc-folio-footer');
    // The assistant measures its composer locally, outside the pinned column's ancestry.
    const pinnedComposer = viewport?.matches('.wbc-pinned-card-column')
      ? viewport.closest('.wbc-page')?.querySelector('.assistant-chat > .wbc-composer') : null;
    let width = el.clientWidth;
    function measure() {
      if (!el.clientWidth) return;
      if (viewport) el.style.setProperty('--folio-viewport', viewport.clientHeight + 'px');
      if (pinnedComposer?.offsetHeight) el.style.setProperty('--folio-composer', pinnedComposer.offsetHeight + 'px');
      else el.style.removeProperty('--folio-composer');
      el.style.setProperty('--folio-footer', footer.offsetHeight + 'px');
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        el.style.removeProperty('--folio-reserved');
      }
      const height = el.offsetHeight + 'px';
      if (height !== el.style.getPropertyValue('--folio-reserved')) el.style.setProperty('--folio-reserved', height);
    }
    measure();
    const observer = new ResizeObserver(measure);
    for (const element of [el, viewport, footer, pinnedComposer]) {
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [isBook]);

  useLayoutEffect(() => {
    const from = previous.current;
    previous.current = page;
    // While the cover turns, reveal the selected page directly underneath it.
    if (turningCover || from === page || !nodes.current[from] || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const reverse = page < from;
    motion.current = isBook
      ? curlPage(stage.current, nodes.current[reverse?page:from],nodes.current[reverse?from:page],
        reverse,group,label(reverse?page:from),(reverse?page:from)+1)
      : shufflePages(nodes.current,from,page);
    return () => motion.current?.();
  }, [page, turningCover]);
  function jump(index) {
    if (index < 0 || index >= count || turningCover) return;
    setDirectory(false);
    setPage(index);
  }
  const coverAnimation = useRef(null);
  useEffect(() => () => coverAnimation.current?.cancel(), []);
  useLayoutEffect(() => {
    // Keep the final transform until React has applied the board's resting visibility.
    if (!turningCover) {
      coverAnimation.current?.cancel();
      coverAnimation.current = null;
    }
  }, [turningCover]);
  function flipCover(open) {
    if (turningCover) return;
    motion.current?.();
    setDirectory(false);
    const board = stage.current.querySelector('.wbc-folio-board');
    const keyframes = [
      {transform:'translateX(0) rotateY(0deg)'},
      {offset:.56,transform:'translateX(22%) rotateY(-104deg)'},
      {transform:'translateX(0) rotateY(-178deg)'}
    ];
    if (!open) keyframes.reverse().forEach(frame=>{if(frame.offset)frame.offset=1-frame.offset;});
    setCovered(false);
    setTurningCover(true);
    const animation=board.animate(keyframes,{duration:matchMedia('(prefers-reduced-motion: reduce)').matches?0:850,easing:'ease-in-out',fill:'forwards'});
    coverAnimation.current=animation;
    animation.onfinish=()=>{
      setCovered(!open);setTurningCover(false);
      requestAnimationFrame(()=> (open?(inPinnedColumn?toc.current:coverLink.current):board.querySelector('button'))?.focus({preventScroll:true}));
    };
  }
  const pinButton = <CardPinButton pinned={pin.pinned} busy={pin.busy} onClick={pin.toggle} t={t}/>;
  function selectChapter(index) {
    jump(index);
    if (covered) flipCover(true);
    else toc.current.focus();
  }
  return <section className={'wbc-folio '+group.format} aria-label={group.title}
    data-pinned-column={inPinnedColumn || undefined} data-covered={covered} data-cover-moving={turningCover}>
    <div className="wbc-folio-stage" ref={stage}
      onTouchStart={e=>{touch.current=e.target.closest('button,input,label,select,textarea,summary,a,[role="slider"]')?null:e.touches[0];}}
      onTouchEnd={e=>{const p=touch.current;touch.current=null;if(!p||covered)return;
        const dx=e.changedTouches[0].clientX-p.clientX,dy=e.changedTouches[0].clientY-p.clientY;
        if(Math.abs(dx)>65 && Math.abs(dx)>2*Math.abs(dy))jump(page+(dx<0?1:-1));}}>
      {group.pages.map((member,i)=>{
        const rank=(i-page+count)%count, inactive=page!==i||covered||turningCover;
        return <div className="wbc-folio-page" key={member.card_id} ref={el=>{nodes.current[i]=el;}}
          data-rank={Math.min(rank,3)} inert={inactive?'':undefined} aria-hidden={inactive}>
          <WbcInlineCard card={member} chatId={chatId} onPageTitle={recordTitle}/>
        </div>;
      })}
      {isBook && <BookCover group={group} open={()=>flipCover(true)}/>}
    </div>
    <div className="wbc-folio-footer">
      <div className="wbc-folio-tools">
        {inPinnedColumn ? <>{pinButton}<span className="wbc-folio-rule" aria-hidden="true"/></> : <>
          {isBook?WBC_ICONS.book:WBC_ICONS.layers}
          {isBook?<button ref={coverLink} type="button" title="查看封面" disabled={covered||turningCover} onClick={()=>flipCover(false)}>{group.title}</button>:<span>{group.title}</span>}
        </>}
        {(isBook || inPinnedColumn) && <div className="wbc-folio-toc" onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setDirectory(false);}}
          onKeyDown={e=>{if(e.key==='Escape'){setDirectory(false);toc.current.focus();}}}>
          <button ref={toc} type="button" disabled={turningCover || (covered && !inPinnedColumn)} aria-expanded={directory} onClick={()=>setDirectory(v=>!v)}>
            {inPinnedColumn && WBC_ICONS.checklist}目录
          </button>
          {directory && <div className="wbc-folio-menu">
            {inPinnedColumn && isBook && !covered && <button type="button" className="wbc-folio-menu-cover" onClick={()=>flipCover(false)}>{WBC_ICONS.book}<span>查看封面</span></button>}
            {group.pages.map((p,i)=><button type="button" key={p.card_id}
              aria-current={!covered && page===i?'page':undefined} onClick={()=>selectChapter(i)}>
              <span className="wbc-folio-chapter-number">{String(i+1).padStart(2,'0')}</span>
              <span>{label(i)}</span>
            </button>)}</div>}
        </div>}
        {!inPinnedColumn && pinButton}
      </div>
      {inPinnedColumn && covered ? <span className="wbc-folio-count">共 {count} 页</span> : <div className="wbc-folio-pager" onKeyDown={e=>{
        if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();jump(page+(e.key==='ArrowRight'?1:-1));}
      }}>
        <button type="button" aria-label="上一页" disabled={!page||covered||turningCover} onClick={()=>jump(page-1)}>‹</button>
        <span aria-live="polite">{page+1} / {count}</span>
        <button type="button" aria-label="下一页" disabled={page===count-1||covered||turningCover} onClick={()=>jump(page+1)}>›</button>
      </div>}
    </div>
    {pin.error && <small role="alert">{pin.error}</small>}
  </section>;
}
