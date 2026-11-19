import React from 'react';
import { useWorkbenchI18n } from '../../workbench-i18n.jsx';
import { WBC_ICONS } from './icons.jsx';

function dateLabel(date, lang) {
  return date.toLocaleDateString(lang, { month: 'short', day: 'numeric', weekday: 'short',
    ...(date.getFullYear() !== new Date().getFullYear() && { year: 'numeric' }) });
}

function clock(date, lang) {
  return date.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

export function ScheduleChange({ change }) {
  const { lang, t } = useWorkbenchI18n();
  const previous = change.previous ? new Date(change.previous) : null;
  const next = change.next_run ? new Date(change.next_run) : null;
  const rescheduled = change.change === 'rescheduled' && previous && next;
  const at = next || previous;
  const sameDay = rescheduled && previous.toDateString() === next.toDateString();
  return <article className="wbc-schedule-receipt wbc-schedule-change">
    <header><h3>{change.prompt}</h3><small data-state={change.change}>{t('schedule.receipt.' + change.change)}</small></header>
    {rescheduled ? <>
      {sameDay && <time dateTime={change.next_run}>{WBC_ICONS.calendar} {dateLabel(next, lang)}</time>}
      <p className="wbc-changed-time"><del>{!sameDay && dateLabel(previous, lang) + ' '}{clock(previous, lang)}</del>
        {' → '}<b>{!sameDay && dateLabel(next, lang) + ' '}{clock(next, lang)}</b></p>
    </> : at && <time dateTime={at.toISOString()}>{WBC_ICONS.calendar}
      {['paused', 'cancelled'].includes(change.change) && t('schedule.receipt.originalTime')}
      {dateLabel(at, lang)} <b>{clock(at, lang)}</b></time>}
  </article>;
}

export function ScheduleAgenda({ agenda }) {
  const { lang, t } = useWorkbenchI18n();
  const groups = new Map();
  for (const row of agenda.rows) {
    const date = new Date(row.at);
    const day = dateLabel(date, lang);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push({ ...row, date });
  }
  const start = new Date(agenda.start), end = new Date(agenda.end);
  const range = agenda.start && agenda.end && Number.isFinite(+start) && Number.isFinite(+end);
  const title = range ? dateLabel(start, lang) + (start.toDateString() === end.toDateString() ? '' : ' – ' + dateLabel(end, lang))
    : t('schedule.receipt.upcoming');
  return <article className="wbc-schedule-receipt wbc-agenda">
    <header><h3>{title}</h3><span>{t('schedule.receipt.count', { count: agenda.rows.length })}</span></header>
    {groups.size === 0 && <p>{t('schedule.receipt.empty')}</p>}
    {Array.from(groups, ([day, rows]) => <section key={day}>
      {day !== title && <h4>{day}</h4>}
      <ul>{rows.map((row, index) => <li key={index}><time dateTime={row.at}>{clock(row.date, lang)}</time>{row.title}</li>)}</ul>
    </section>)}
  </article>;
}

export function KnowledgeSource({ source, onOpenFile }) {
  const { t } = useWorkbenchI18n();
  function openSource() {
    const { workspace, item_id, attachment_id, document_name } = source;
    const url = '/api/workbench/library/items/' + encodeURIComponent(item_id) + '/attachments/'
      + encodeURIComponent(attachment_id) + '/raw?workspace=' + encodeURIComponent(workspace);
    onOpenFile({ id: url, url, name: document_name, content_type: source.content_type,
      sourceKind: 'library', ownerProjectId: workspace, libraryItemId: item_id }, 'right');
  }
  return <button type="button" className="wbc-knowledge-source" disabled={!onOpenFile} onClick={openSource}
    aria-label={t('schedule.receipt.openSource', { name: source.document_name })}>
    <span className="wbc-source-label">{WBC_ICONS.book} {t('schedule.receipt.source')}
      <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor"><rect x="2" y="3" width="16" height="14" rx="2"/><path d="M11 3v14"/></svg>
    </span>
    <strong>{source.document_name}</strong><span className="wbc-source-quote">{source.excerpt}</span>
  </button>;
}
