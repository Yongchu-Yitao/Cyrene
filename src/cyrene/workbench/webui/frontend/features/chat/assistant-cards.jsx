import { presentHomeSpec } from './home-greeting.mjs';
import { AssistantHomeLayout, homeDisplayTitle } from './assistant-home-layout.jsx';
import { CardWeather } from './card-weather.jsx';
import { WBC_ICONS } from './icons.jsx';
import { useWorkbenchI18n } from '../../workbench-i18n.jsx';
import React, { useEffect, useRef, useState } from 'react';
import { wbcRenderMarkdown } from './runtime-timeline.jsx';
import { CardChart, CardEmbed, CardMap } from './assistant-card-visuals.jsx';
import { cardDeliveryNotice, cardEvent, initialCardValues, timelineStatusTone, toggleCardSelection } from './assistant-card-model.mjs';

const templateLabels = { content: '内容', collection: '集合', data: '数据', input: '交互', map: '地图', custom: '自定义' };
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
  const common = { id, disabled, required: schema.required, 'aria-label': field.label };
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

export function CardBlock({ block, card, values, disabled, onEdit, onChange, onEvent }) {
  const selected = block.selection && values[block.selection.field_id];
  const selectedIds = Array.isArray(selected) ? selected : [selected];
  switch (block.type) {
    case 'weather': return <CardWeather block={block} />;
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
    case 'embed': return <CardEmbed block={block} card={card} values={values} disabled={disabled} onEvent={onEvent} />;
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
      window.dispatchEvent(new CustomEvent('cyrene:card-submitted', { detail: { session_id: current.current.origin.session_id } }));
    } catch (e) { if (mounted.current) setError(e.message); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  return { send, togglePin, retryDelivery };
}

function useAssistantCard(cardId, initialCard) {
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
  const url = '/api/assistant/cards/' + encodeURIComponent(cardId);
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
  useEffect(() => {
    mounted.current = true;
    refresh(true);
    const timer = setInterval(() => { if (!document.hidden) refresh(); }, 3000);
    const update = event => { if (event.detail?.card_id === cardId) refresh(); };
    window.addEventListener('cyrene:card-updated', update);
    return () => { mounted.current = false; clearInterval(timer); clearTimeout(draftTimer.current); window.removeEventListener('cyrene:card-updated', update); };
  }, [cardId]);
  function edit(key, value) {
    dirty.current = { ...dirty.current, [key]: value };
    local.current = { ...local.current, [key]: value }; setValues(local.current);
  }
  function editDraft(key, value) {
    edit(key, value);
    clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      const changed = Object.fromEntries(Object.entries(dirty.current).filter(([, v]) => v !== undefined));
      if (Object.keys(changed).length) sendRef.current('change', changed);
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

export function WbcAssistantCard({ cardId, initialCard = null, displayTime = new Date() }) {
  const { t } = useWorkbenchI18n();
  const { card, values, busy, error, pendingEvent, local, form, refresh, editDraft, change, send, togglePin, retryDelivery } = useAssistantCard(cardId, initialCard);
  if (!card) return renderCardLoading(error, refresh);
  const { disabled, delivery, awaitingDelivery, footerActions, isHome, displayCard } = cardPresentation(card, busy, pendingEvent, displayTime);
  const renderBlock = block => <section className={'wbc-card-block type-' + block.type} key={block.block_id}>
    {block.title && !(block.type === 'action_list' && block.collapsed) && !(isHome && block.type === 'timeline') && <h4>{block.title}</h4>}
    {block.description && <p className="wbc-card-description">{block.description}</p>}
    <CardBlock block={block} card={card} values={values} disabled={disabled || awaitingDelivery} onEdit={editDraft} onChange={change} onEvent={(kind, next, action) => send(kind, kind === 'change' ? next : { ...local.current, ...next }, action)} />
  </section>;
  return <article className={'wbc-assistant-card density-' + card.spec.density + (isHome ? ' wbc-home-section home-' + card.spec.home_kind : '')} data-card-id={cardId} aria-label={isHome ? homeDisplayTitle(displayCard.spec) : card.spec.title} aria-busy={busy}>
    {!isHome && renderCardHeader(card, busy, togglePin, t)}
    <form ref={form} onSubmit={event => event.preventDefault()}>
      {isHome ? <AssistantHomeLayout card={displayCard} now={displayTime} renderBlock={renderBlock} /> : <div className={'wbc-card-blocks layout-' + card.spec.layout}>{card.spec.blocks.map(renderBlock)}</div>}
      {renderCardFooter(card, footerActions, disabled || awaitingDelivery, send, local)}
    </form>
    {renderCardNotices({ error, delivery, pendingEvent, busy, send, retryDelivery })}
  </article>;
}

function renderCardHeader(card, busy, togglePin, t) {
  return <header><div><span className="wbc-card-type">{templateLabels[card.template] || '卡片'}{card.status === 'archived' ? ' · 只读' : ''}</span><h3>{card.spec.title}</h3>{card.spec.subtitle && <p>{card.spec.subtitle}</p>}</div><button className={'wbc-pane-icon-action wbc-card-pin' + (card.placement === 'pinned' ? ' active' : '')} type="button" disabled={busy} aria-pressed={card.placement === 'pinned'} aria-label={t(card.placement === 'pinned' ? 'workbenchChat.cardUnpin' : 'workbenchChat.cardPin')} title={t(card.placement === 'pinned' ? 'workbenchChat.cardUnpin' : 'workbenchChat.cardPin')} onClick={togglePin}><span aria-hidden="true">{WBC_ICONS.pin}</span></button></header>;
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

function cardPresentation(card, busy, pendingEvent, displayTime) {
  const disabled = busy || !!pendingEvent || card.status !== 'active';
  const delivery = card.input_state.submitted?.delivery;
  const awaitingDelivery = delivery?.status === 'pending' || delivery?.status === 'failed';
  const rowActions = new Set(card.spec.blocks.flatMap(block => block.type === 'action_list' ? block.items.flatMap(item => item.action_ids) : []));
  const footerActions = card.spec.actions.filter(action => !rowActions.has(action.action_id));
  const isHome = !!card.spec.home_kind;
  const displayCard = isHome ? { ...card, spec: presentHomeSpec(card.spec, displayTime) } : card;
  return { disabled, delivery, awaitingDelivery, footerActions, isHome, displayCard };
}
