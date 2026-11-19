import { InlineCardPreview } from './inline-card-preview.jsx';
import { conversationCardsUrl } from './pinned-card-model.mjs';
import { ScheduleChange, ScheduleAgenda, KnowledgeSource } from './tool-result-cards.jsx';
import { observeCardVisibility } from './card-background-work.mjs';
import { AssistantHomeLayout, homeDisplayTitle } from './assistant-home-layout.jsx';
import { CardWeather } from './card-weather.jsx';
import { WBC_ICONS } from './icons.jsx';
import { useWorkbenchI18n } from '../../workbench-i18n.jsx';
import React, { useEffect, useRef, useState } from 'react';
import { wbcRenderMarkdown } from './runtime-timeline.jsx';
import { CardChart, CardEmbed, CardMap } from './assistant-card-visuals.jsx';
import { cardDeliveryNotice, cardEvent, initialCardValues, timelineStatusTone, toggleCardSelection } from './assistant-card-model.mjs';

const templateLabels = { content: '内容', collection: '集合', data: '数据', input: '交互', map: '地图' };
const asText = value => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);

export async function requestCard(url, options) {
  const response = await fetch(url, options);
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.ok === false) {
    const error = new Error(result.error?.message || (response.status === 404 ? '卡片不存在或卡片插件未启用。' : '卡片暂时无法加载，请重试。'));
    error.status = response.status;
    throw error;
  }
  return result;
}

function CardField({ field, value, disabled, schema, onEdit, onSave }) {
  const id = React.useId();
  // HTML requires a checkbox to be checked. JSON Schema requires its key to exist.
  const required = field.type === 'boolean' ? schema.enum?.includes(true) && !schema.enum.includes(false) : schema.required;
  const common = { id, disabled, required, 'aria-label': field.label };
  let control;
  if (field.type === 'boolean') control = <input {...common} type="checkbox" checked={!!value} onChange={e => onSave(e.target.checked)} />;
  else if (field.type === 'select') control = <select {...common} value={value ?? ''} onChange={e => onSave(e.target.value)}><option value="" disabled>请选择</option>{(field.options || []).map((option, i) => <option key={i} value={option.value}>{option.label}</option>)}</select>;
  else control = <input {...common} type={['number', 'integer'].includes(field.type) ? 'number' : field.type === 'date' ? 'date' : 'text'} value={value ?? ''}
    min={schema.minimum} max={schema.maximum} minLength={schema.minLength} maxLength={schema.maxLength} step={field.type === 'integer' ? 1 : 'any'}
    onChange={e => onEdit(['number', 'integer'].includes(field.type) ? (e.target.value === '' ? undefined : Number(e.target.value)) : e.target.value)}
 />;
  return <label className={'wbc-card-field' + (field.type === 'boolean' ? ' checkbox' : '')} htmlFor={id}><span>{field.label}{field.unit && <small> · {field.unit}</small>}</span>{control}</label>;
}

function CardActionList({ block, card, disabled, onEvent, values }) {
  const [expanded, setExpanded] = useState(!block.collapsed);
  const actions = new Map(card.spec.actions.map(action => [action.action_id, action]));
  const rows = <ul className="wbc-card-action-list">{block.items.map(item => <li key={item.item_id}>
    <div className="wbc-card-action-copy">{card.spec.home_kind === 'results' && <span className="wbc-home-file-icon" aria-hidden="true">{WBC_ICONS.fileText}</span>}<div><strong>{item.label}</strong>{item.detail && <small>{item.detail}</small>}</div></div>
    <div className="wbc-card-row-actions">{item.action_ids.map(id => { const action = actions.get(id); return action && <button key={id} type="button" disabled={disabled} onClick={() => onEvent(action.kind, values, action)}>{action.label}</button>; })}</div>
  </li>)}</ul>;
  return <div>{block.collapsed ? <><button className="wb-btn wbc-card-list-toggle" type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><strong>{block.title || '事项'}</strong><span className="wbc-card-list-toggle-end"><span>{block.items.length} 项</span><span className="wbc-card-disclosure-icon" aria-hidden="true">{WBC_ICONS.chevronDown}</span></span></button>{expanded && (block.items.length ? rows : <p className="wbc-card-description">暂无事项</p>)}</> : block.items.length ? rows : <p className="wbc-card-description">暂无事项</p>}</div>;
}

export function CardBlock({ block, card, values, disabled, snapshot, onEdit, onChange, onEvent }) {
  const selected = block.selection && values[block.selection.field_id];
  const selectedIds = Array.isArray(selected) ? selected : [selected];
  switch (block.type) {
    case 'weather':
    case 'weather_observation': return <CardWeather block={block} />;
    case 'action_list': return <CardActionList block={block} card={card} values={values} disabled={disabled} onEvent={onEvent} />;
    case 'text': return <div className="markdown" dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(block.text) }} />;
    case 'metrics': return <div className="wbc-card-metrics">{block.items.map((item, i) => <div key={i}><span>{item.label}</span><strong>{asText(item.value)} <small>{item.unit}</small></strong>{item.source && <small>{item.source}</small>}</div>)}</div>;
    case 'fields': return <dl className="wbc-card-fields">{block.items.map((item, i) => <React.Fragment key={i}><dt>{item.label}</dt><dd>{item.value}</dd></React.Fragment>)}</dl>;
    case 'list': return <ul className="wbc-card-list">{block.items.map(item => <li key={item.item_id}><label>{item.field_id && <input type="checkbox" disabled={disabled} checked={!!values[item.field_id]} onChange={e => onChange(item.field_id, e.target.checked)} />}<span><strong>{item.label}</strong>{item.detail && <small>{item.detail}</small>}{(item.owner || item.due_at) && <small>{[item.owner, item.due_at].filter(Boolean).join(' · ')}</small>}</span></label></li>)}</ul>;
    case 'table': return <div className="wbc-card-table"><table><thead><tr>{block.selection && <th>选择</th>}{block.columns.map(column => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{block.rows.map(row => <tr key={row.row_id}>{block.selection && <td><input type={block.selection.mode === 'single' ? 'radio' : 'checkbox'} name={card.card_id + block.block_id} disabled={disabled} checked={selectedIds.includes(row.row_id)} aria-label={'选择 ' + asText(row[block.columns[0]?.key] || row.row_id)} onChange={() => onChange(block.selection.field_id, toggleCardSelection(selected, row.row_id, block.selection.mode))} /></td>}{block.columns.map(column => <td key={column.key}>{formatCell(row[column.key], column)}</td>)}</tr>)}</tbody></table>{!block.rows.length && <p>暂无数据</p>}</div>;
    case 'form': return <div className="wbc-card-form">{block.fields.map(field => <CardField key={field.field_id} field={field} value={values[field.field_id]} disabled={disabled}
      schema={{ ...card.spec.input_schema.properties[field.field_id], required: card.spec.input_schema.required?.includes(field.field_id) }}
      onEdit={value => onEdit(field.field_id, value)} onSave={value => onChange(field.field_id, value)} />)}</div>;
    case 'timeline': return <ol className="wbc-card-timeline">{block.items.map(item => <li key={item.item_id}><time>{item.time}</time><strong>{item.label}</strong>{item.status && <small className={'wbc-timeline-status status-' + timelineStatusTone(item.status)}>{item.status}</small>}</li>)}</ol>;
    case 'files': return <ul className="wbc-card-list">{block.items.map(item => <li key={item.artifact_id}><strong>{item.name}</strong><small>{item.media_type || item.artifact_id}</small></li>)}</ul>;
    case 'chart': return <CardChart block={block} />;
    case 'map': return <CardMap block={block} values={values} disabled={disabled} onChange={onChange} />;
    case 'embed': return <CardEmbed snapshot={snapshot} block={block} card={card} values={values} disabled={disabled} onEvent={onEvent} />;
    default: return <p role="status">暂不支持此内容区块。</p>;
  }
}
function formatCell(value, column) {
  if (typeof value === 'number' && column.format === 'currency') {
    try { return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: column.currency || 'CNY' }).format(value); } catch (_) { /* Show original value for unsupported currency. */ }
  }
  return asText(value);
}

function createCardActions({ cardId, url, current, pendingEvent, form, draftTimer, lock, mounted, dirty, accept, setBusy, setError, setPendingEvent }) {
  function canSend(kind, replay) {
    if (lock.current || !current.current || (!replay && (pendingEvent || current.current.status !== 'active'))) return false;
    if (!replay && kind !== 'change' && ['pending', 'failed'].includes(current.current.input_state.submitted?.delivery?.status)) return false;
    if (!replay && kind !== 'change' && form.current && !form.current.reportValidity()) return false;
    return true;
  }
  function acceptReceipt(receipt, kind, inputValues) {
    // Apply the receipt before refetching; a subsequent network failure must
    // not present a successfully saved confirmation as failed.
    const next = { ...current.current, input_revision: receipt.input_revision,
      input_state: { ...current.current.input_state, draft: receipt.values,
        ...(kind !== 'change' ? { submitted: { event_id: receipt.event_id, kind, values: receipt.values, revision: receipt.base_revision, input_revision: receipt.input_revision, delivery: receipt.delivery } } : {}) } };
    for (const [key, value] of Object.entries(inputValues)) if (JSON.stringify(dirty.current[key]) === JSON.stringify(value)) delete dirty.current[key];
    accept(next);
    if (mounted.current) { setPendingEvent(null);  }
    window.dispatchEvent(new CustomEvent('cyrene:card-updated', { detail: { card_id: cardId } }));
    if (kind !== 'change') window.dispatchEvent(new CustomEvent('cyrene:card-submitted', { detail: { session_id: receipt.origin.session_id } }));
  }
  async function handleSendError(e) {
    if (mounted.current) {
      // Keep the exact ID, versions and values after an ambiguous network
      // failure. Retrying must replay that event, never create a new submit.
      if (e.status && e.status < 500) setPendingEvent(null);
      setError(e.status === 409 ? '卡片已更新，请检查最新内容后重新提交。' : (!e.status ? '网络连接中断，输入已保留。恢复连接后请重试。' : e.message));
    }
    if (e.status === 409) { try { accept(await requestCard(url)); } catch (_) { /* Keep the original error. */ } }
  }
  async function send(kind, inputValues, action, replay = null) {
    if (!canSend(kind, replay)) return;
    clearTimeout(draftTimer.current);
    lock.current = true; setBusy(true); setError('');
    const payload = replay || JSON.parse(JSON.stringify(cardEvent(current.current, kind, inputValues, action, crypto.randomUUID())));
    setPendingEvent(payload);
    try {
      const receipt = await requestCard(url + '/events', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      acceptReceipt(receipt, kind, inputValues);
    } catch (e) {
      await handleSendError(e);
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  async function togglePin() {
    if (lock.current || !current.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const placement = await requestCard(url + '/pin', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pinned: current.current.placement !== 'pinned', expected_placement_revision: current.current.placement_revision }),
      });
      // Placement receipts are summaries, not a new content/input snapshot.
      accept({ ...current.current, placement: placement.placement,
        placement_revision: placement.placement_revision, user_unpinned: placement.user_unpinned });
      window.dispatchEvent(new CustomEvent('cyrene:card-placement-changed'));
      window.dispatchEvent(new CustomEvent('cyrene:card-updated', { detail: { card_id: cardId } }));
    } catch (e) {
      if (mounted.current) setError(e.status === 409 ? '固定状态已变化，请查看最新状态后重试。' : e.message);
      if (e.status === 409) { try { accept(await requestCard(url)); } catch (_) {} }
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  async function retryDelivery() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const submitted = current.current.input_state.submitted;
      const delivery = await requestCard(url + '/events/' + encodeURIComponent(submitted.event_id) + '/retry', { method: 'POST' });
      accept({ ...current.current, input_state: { ...current.current.input_state, submitted: { ...submitted, delivery } } });
      window.dispatchEvent(new CustomEvent('cyrene:card-updated', { detail: { card_id: cardId } }));
      window.dispatchEvent(new CustomEvent('cyrene:card-submitted', { detail: { session_id: current.current.origin.session_id } }));
    } catch (e) { if (mounted.current) setError(e.message); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  return { send, togglePin, retryDelivery };
}

function useAssistantCardRefresh({ initialCard, managedRefresh, cardId, busy, mounted, draftTimer, refresh, accept }) {
  const externalCard = useRef(initialCard);
  useEffect(() => {
    mounted.current = true;
    let timer;
    const update = event => { if (!managedRefresh && event.detail?.card_id === cardId) refresh(); };
    const visibility = () => {
      clearInterval(timer);
      if (!managedRefresh && !document.hidden) { refresh(true); timer = setInterval(() => refresh(), 3000); }
    };
    visibility();
    window.addEventListener('cyrene:card-updated', update);
    document.addEventListener('visibilitychange', visibility);
    return () => { mounted.current = false; clearInterval(timer); clearTimeout(draftTimer.current); window.removeEventListener('cyrene:card-updated', update); document.removeEventListener('visibilitychange', visibility); };
  }, [cardId, managedRefresh]);
  useEffect(() => {
    if (managedRefresh && initialCard && !busy && externalCard.current !== initialCard) {
      externalCard.current = initialCard;
      accept(initialCard);
    }
  }, [initialCard, managedRefresh, busy]);
}

export function useAssistantCard(cardId, initialCard, managedRefresh = false, chatId) {
  const [card, setCard] = useState(initialCard);
  const [values, setValues] = useState(() => initialCard ? initialCardValues(initialCard) : {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pendingEvent, setPendingEvent] = useState(null);
  const current = useRef(initialCard);
  const local = useRef(initialCard ? initialCardValues(initialCard) : {});
  const dirty = useRef({});
  const lock = useRef(false);
  const mounted = useRef(false);
  const loading = useRef(false);
  const draftTimer = useRef(null);
  const sendRef = useRef(null);
  const form = useRef(null);
  const url = conversationCardsUrl(chatId) + '/' + encodeURIComponent(cardId);
  function accept(next) {
    if (!mounted.current) return;
    if (current.current?.revision > next.revision || (current.current?.revision === next.revision && current.current?.input_revision > next.input_revision)) return;
    if (current.current?.placement_revision > next.placement_revision) next = { ...next, placement: current.current.placement, placement_revision: current.current.placement_revision, user_unpinned: current.current.user_unpinned };
    if (current.current && JSON.stringify(current.current) === JSON.stringify(next)) return;
    current.current = next; setCard(next);
    const permitted = next.spec.input_schema.properties;
    const pending = Object.fromEntries(Object.entries(dirty.current).filter(([key]) => Object.hasOwn(permitted, key)));
    dirty.current = pending;
    local.current = { ...initialCardValues(next), ...pending }; setValues(local.current);
  }
  async function refresh(showError = false) {
    if (loading.current || lock.current) return;
    loading.current = true;
    try { accept(await requestCard(url)); if (showError && mounted.current) setError(''); }
    catch (e) { if (mounted.current && (showError || !current.current)) setError(e.message); }
    finally { loading.current = false; }
  }
  useAssistantCardRefresh({ initialCard, managedRefresh, cardId, busy, mounted, draftTimer, refresh, accept });
  function edit(key, value) {
    dirty.current = { ...dirty.current, [key]: value };
    local.current = { ...local.current, [key]: value }; setValues(local.current);
  }
  function editDraft(key, value) {
    edit(key, value);
    scheduleDraft();
  }
  function scheduleDraft() {
    clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      const changed = Object.fromEntries(Object.entries(dirty.current).filter(([, v]) => v !== undefined));
      if (!Object.keys(changed).length || !mounted.current) return;
      if (lock.current) { scheduleDraft(); return; }
      sendRef.current('change', changed);
    }, 700);
  }
  const { send, togglePin, retryDelivery } = createCardActions({ cardId, url, current, pendingEvent, form, draftTimer, lock, mounted, dirty, accept, setBusy, setError, setPendingEvent });
  sendRef.current = send;
  function change(key, value) {
    edit(key, value);
    const changed = Object.fromEntries(Object.entries(dirty.current).filter(([field, next]) =>
      next !== undefined && JSON.stringify(current.current?.input_state.draft[field]) !== JSON.stringify(next)));
    if (Object.keys(changed).length) send('change', changed);
    else { delete dirty.current[key]; }
  }
  return { card, values, busy, error, pendingEvent, local, form, refresh, editDraft, change, send, togglePin, retryDelivery };
}

export function useCardAnimations(ref, enabled) {
  useEffect(() => {
    if (enabled && ref.current) return observeCardVisibility(ref.current);
  }, [ref, enabled]);
}

function InlineChecklistHeading({ card, values }) {
  const { t } = useWorkbenchI18n();
  const keys = new Set();
  for (const block of card.spec.blocks) {
    let fields;
    if (block.type === 'form' && block.fields.every(field => field.type === 'boolean')) {
      fields = block.fields.map(field => field.field_id);
    } else if (block.type === 'list' && block.items.every(item => item.field_id)) {
      fields = block.items.map(item => item.field_id);
    } else return null;
    for (const key of fields) keys.add(key);
  }
  if (!keys.size) return null;
  const completed = [...keys].filter(key => values[key] === true).length;
  return <div className="wbc-inline-checklist-heading">
    <strong>{card.spec.title}</strong>
    <small>{t('workbenchChat.checklistCompleted')} {completed}/{keys.size}</small>
  </div>;
}

function HistoricalCard({ cardId, revision, chatId }) {
  const [open, setOpen] = useState(false);
  const [card, setCard] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open || card) return;
    const controller = new AbortController();
    const url = conversationCardsUrl(chatId) + '/' + encodeURIComponent(cardId) + '?revision=' + revision;
    setError('');
    requestCard(url, { signal: controller.signal }).then(result => {
      if (result.revision !== revision) throw new Error('未找到该版本的快照');
      if (!controller.signal.aborted) setCard(result);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [open, card, chatId, cardId, revision]);
  return <details className="wbc-card-snapshot" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>查看当时的版本 · 第 {revision} 版（只读）</summary>
    {open && (card ? <article className="wbc-assistant-card in-prose">
      <strong>{card.spec.title}</strong>
      {card.spec.subtitle && <p>{card.spec.subtitle}</p>}
      <div className={'wbc-card-blocks layout-' + card.spec.layout}>
        {card.spec.blocks.map(block => <section className={'wbc-card-block type-' + block.type} key={block.block_id}>
          {block.title && <h4>{block.title}</h4>}
          {block.description && <p>{block.description}</p>}
          <CardBlock card={card} block={block.collapsed ? { ...block, collapsed: false } : block} values={initialCardValues(card)} disabled snapshot />
        </section>)}
      </div>
    </article> : <p>{error || '正在读取快照…'}</p>)}
  </details>;
}

export function WbcInlineCard({ card, chatId, onOpenFile, inProse, onPageTitle }) {
  if (card.snapshot) return <HistoricalCard key={card.card_id + card.revision} chatId={chatId} cardId={card.card_id} revision={card.revision} />;
  if (card.change) return <ScheduleChange change={card.change} />;
  if (card.agenda) return <ScheduleAgenda agenda={card.agenda} />;
  if (card.source) return <KnowledgeSource source={card.source} onOpenFile={onOpenFile} />;
  return card.schedule ? <ScheduleReceipt schedule={card.schedule} />
    : <WbcAssistantCard key={JSON.stringify([chatId, card.card_id])} reservePreview={!onPageTitle} onPageTitle={onPageTitle} inProse={inProse} chatId={chatId} cardId={card.card_id} revision={card.revision} />;
}

function ScheduleReceipt({ schedule }) {
  const { lang, t } = useWorkbenchI18n();
  const at = new Date(schedule.next_run);
  const once = schedule.schedule_type === 'once';
  const date = at.toLocaleDateString(lang, {
    month: 'short', day: 'numeric', weekday: 'short',
    ...(at.getFullYear() !== new Date().getFullYear() && { year: 'numeric' }),
  });
  const time = at.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  return <article className="wbc-schedule-receipt">
    <header><h3>{schedule.prompt}</h3>
      <small data-recurring={!once}>{t(once ? 'schedule.receipt.once' : 'schedule.receipt.recurring')}</small>
    </header>
    <time dateTime={schedule.next_run}><span aria-hidden="true">{WBC_ICONS.calendar}</span>
      {!once && t('schedule.nextRun')} {date} <b>{time}</b>
    </time>
  </article>;
}

export function WbcAssistantCard({ chatId, cardId, revision, onPageTitle, reservePreview = false, initialCard = null, displayTime = new Date(), managedRefresh = false, homeDisclosure, inProse = false }) {
  const { t } = useWorkbenchI18n();
  const { card, values, busy, error, pendingEvent, local, form, refresh, editDraft, change, send, togglePin, retryDelivery } = useAssistantCard(cardId, initialCard, managedRefresh, chatId);
  const localDisclosure = useState(null);
  const cardElement = useRef(null);
  useEffect(() => {
    if (!managedRefresh && !document.hidden && revision > (card?.revision ?? 0)) refresh();
  }, [cardId, revision, managedRefresh]);
  useCardAnimations(cardElement, !!card);
  useEffect(() => {
    if (card && !card.spec.home_kind) onPageTitle?.(cardId, card.spec.title);
  }, [cardId, card?.spec.title, onPageTitle]);
  if (onPageTitle && card?.spec.home_kind) return null;
  const preview = content => <InlineCardPreview card={card} error={error} retry={() => refresh(true)}>{content}</InlineCardPreview>;
  if (!card) return reservePreview ? preview(null) : renderCardLoading(error, refresh);
  const { disabled, delivery, awaitingDelivery, footerActions, isHome, displayCard } = cardPresentation(card, busy, pendingEvent);
  const renderBlock = (block, inPanel = false) => <section className={'wbc-card-block type-' + block.type} key={block.block_id}>
    {block.title && !inPanel && !(block.type === 'action_list' && block.collapsed) && !(isHome && block.type === 'timeline') && <h4>{block.title}</h4>}
    {block.description && <p className="wbc-card-description">{block.description}</p>}
    <CardBlock block={inPanel && block.collapsed ? { ...block, collapsed: false } : block} card={card} values={values} disabled={(block.type === 'embed' && busy && pendingEvent?.kind === 'change' ? card.status !== 'active' : disabled) || awaitingDelivery} onEdit={editDraft} onChange={change} onEvent={(kind, next, action) => {
      if (kind === 'change') {
        for (const [key, value] of Object.entries(next)) editDraft(key, value);
      } else send(kind, { ...local.current, ...next }, action);
    }} />
  </section>;
  const content = <article ref={cardElement} className={'wbc-assistant-card density-' + card.spec.density + (inProse ? ' in-prose' : '') + (isHome ? ' wbc-home-section home-' + card.spec.home_kind : '')} data-card-id={cardId} aria-label={isHome ? homeDisplayTitle(displayCard.spec) : card.spec.title} aria-busy={busy}>
    {!isHome && !inProse && renderCardHeader(card, busy, togglePin, t)}
    <form ref={form} onSubmit={event => event.preventDefault()}>
      {inProse && !isHome && <InlineChecklistHeading card={card} values={values} />}
      {isHome ? <AssistantHomeLayout card={displayCard} now={displayTime} disclosure={homeDisclosure ?? localDisclosure} renderBlock={renderBlock} /> : <div className={'wbc-card-blocks layout-' + card.spec.layout}>{card.spec.blocks.map(block => renderBlock(block))}</div>}
      {renderCardFooter(card, footerActions, disabled || awaitingDelivery, send, local)}
    </form>
    {renderCardNotices({ error, delivery, pendingEvent, busy, send, retryDelivery })}
  </article>;
  return reservePreview ? preview(content) : content;
}

function renderCardHeader(card, busy, togglePin, t) {
  const isCustom = card.template === 'custom';
  const label = [!isCustom && (templateLabels[card.template] || '卡片'), card.status === 'archived' && '只读'].filter(Boolean).join(' · ');
  return <header className={isCustom ? 'wbc-card-header-custom' : undefined}>
    <div className="wbc-card-heading">
      {label && <span className="wbc-card-type">{label}</span>}
      <h3>{card.spec.title}</h3>
      {card.spec.subtitle && <p>{card.spec.subtitle}</p>}
    </div>
    <CardPinButton pinned={card.placement === 'pinned'} busy={busy} onClick={togglePin} t={t} />
  </header>;
}

function renderCardNotices({ error, delivery, pendingEvent, busy, send, retryDelivery }) {
  return <>
    {error && <p className="wbc-card-error" role="alert">{error}</p>}
    {cardDeliveryNotice(delivery) && <p className="wbc-card-notice" role="status">{cardDeliveryNotice(delivery)}</p>}
    {pendingEvent && !busy && <button type="button" onClick={() => send(pendingEvent.kind, pendingEvent.values, null, pendingEvent)}>重试本次提交</button>}
    {delivery?.status === 'failed' && <button type="button" disabled={busy || !!pendingEvent} onClick={retryDelivery}>重试发送</button>}
  </>;
}

function renderCardLoading(error, refresh) {
  return <section className="wbc-assistant-card" aria-label="助理卡片"><p role={error ? 'alert' : 'status'}>{error || '正在加载卡片…'}</p>{error && <button type="button" onClick={() => refresh(true)}>重试</button>}</section>;
}

function renderCardFooter(card, footerActions, disabled, send, local) {
  return card.template !== 'custom' && footerActions.length > 0 && <footer><div>{footerActions.map(action => <button key={action.action_id} type="button" disabled={disabled} onClick={() => send(action.kind, local.current, action)}>{action.label}</button>)}</div></footer>;
}

function cardPresentation(card, busy, pendingEvent) {
  const disabled = busy || !!pendingEvent || card.status !== 'active';
  const delivery = card.input_state.submitted?.delivery;
  const awaitingDelivery = delivery?.status === 'pending' || delivery?.status === 'failed';
  const rowActions = new Set(card.spec.blocks.flatMap(block => block.type === 'action_list' ? block.items.flatMap(item => item.action_ids) : []));
  const footerActions = card.spec.actions.filter(action => !rowActions.has(action.action_id));
  const isHome = !!card.spec.home_kind;
  const displayCard = card;
  return { disabled, delivery, awaitingDelivery, footerActions, isHome, displayCard };
}

export function CardPinButton({pinned, busy, onClick, t}) {
  const label = t(pinned ? 'workbenchChat.cardUnpin' : 'workbenchChat.cardPin');
  return <button className={'wbc-pane-icon-action wbc-card-pin' + (pinned ? ' active' : '')}
    type="button" disabled={busy} aria-pressed={pinned} aria-label={label} title={label}
    onClick={onClick}><span aria-hidden="true">{WBC_ICONS.pin}</span></button>;
}
