import { markdownCardParts } from "./markdown-card-references.mjs";
import { installCardBridge } from "./assistant-card-bridge.mjs";
// Inline card references travel with tool results, never through model-written HTML.
export function cardReferences(message) {
  const entries = message.runtimeActivity?.progress || message.trace || [];
  return entries.flatMap(entry => {
    const p = entry.presentation;
    if (entry.failed || entry.status !== 'completed') return [];
    if (p?.kind === 'schedule_created') {
      if (typeof p.task_id !== 'string' || !p.task_id || typeof p.prompt !== 'string' || !p.prompt.trim()
          || !['once', 'cron', 'interval'].includes(p.schedule_type) || !Number.isFinite(Date.parse(p.next_run))) return [];
      return [{ card_id: 'schedule:' + p.task_id, schedule: p }];
    }
    if (entry.toolCallId && ['schedule_change', 'schedule_agenda', 'knowledge_sources'].includes(p?.kind)) {
      const call = entry.toolCallId;
      if (p.kind === 'knowledge_sources') {
        return (Array.isArray(p.sources) ? p.sources : []).filter(validSource).map(source => ({
          card_id: JSON.stringify([call, source.workspace, source.item_id, source.attachment_id]), source,
        }));
      }
      if (p.kind === 'schedule_change' && typeof p.prompt === 'string'
          && [p.previous, p.next_run].every(at => at == null || Number.isFinite(Date.parse(at)))
          && ['edited', 'paused', 'cancelled', 'resumed', 'rescheduled'].includes(p.change)) {
        return [{ card_id: 'change:' + call, change: p }];
      }
      if (p.kind === 'schedule_agenda' && Array.isArray(p.rows)) {
        const rows = p.rows.filter(row => row && typeof row.title === 'string' && Number.isFinite(Date.parse(row.at)));
        return [{ card_id: 'agenda:' + call, agenda: { ...p, rows } }];
      }
      return [];
    }
    if (p?.kind !== 'assistant_card'
      || p.home_kind || !['card.create', 'card.update'].includes(p.operation) || !/^card_[a-f0-9]{32}$/.test(p.card_id || '')) return [];
    return [{ card_id: p.card_id, revision: p.revision }];
  });
}

function validSource(source) {
  return source && ['workspace', 'item_id', 'attachment_id', 'document_name', 'excerpt']
    .every(key => typeof source[key] === 'string' && source[key].trim())
    && [source.item_id, source.attachment_id].every(id => !/[/\\]/.test(id) && id !== '.' && id !== '..');
}

export function projectInlineCards(messages, chatId) {
  // A newer revision replaces its predecessor inside a turn. Across user
  // turns, the former placement becomes a read-only version of that card.
  const latest = new Map();
  const turns = [];
  let placements = new Map();
  const references = messages.map(message => {
    if (message.role === 'user') placements = new Map();
    turns.push(placements);
    const cards = cardReferences(message);
    for (const card of cards) {
      const previous = latest.get(card.card_id);
      if (previous && (card.revision ?? 0) <= (previous.revision ?? 0)) continue;
      placements.set(card.card_id, card);
      latest.set(card.card_id, card);
    }
    return cards;
  });
  const placed = new Set();
  function display(card) {
    return latest.get(card.card_id) === card ? card : { ...card, snapshot: true };
  }
  const parsed = messages.map(message => message.role === 'assistant' && !message.activityCard && !message.runtimeActivity
    ? markdownCardParts(message.content) : null);
  // An explicit collection can gather cards that already appeared in earlier replies.
  // Reserve those placements before projecting singles and fallback tool receipts.
  const groups = new Map();
  const claimed = new Set();
  const eligible = new Map();
  const groupsInOrder = [];
  parsed.forEach((parts, index) => {
    for (const card of references[index]) {
      const previous = eligible.get(card.card_id);
      if (!previous || (card.revision ?? 0) > (previous.revision ?? 0)) eligible.set(card.card_id, card);
    }
    for (const part of parts || []) {
      if (!part.pages) continue;
      const members = part.pages.flatMap(id => {
        const card = turns[index].get(id) || eligible.get(id);
        return card ? [card] : chatId ? [id] : [];
      });
      groupsInOrder.push({part, members});
    }
  });
  for (const {part, members} of groupsInOrder.reverse()) {
    groups.set(part, members.flatMap(member => {
      if (claimed.has(member)) return [];
      claimed.add(member);
      if (typeof member === 'string') return [{card_id: member}];
      placed.add(member);
      return [display(member)];
    }));
  }
  const bodies = messages.map((message, index) => {
    if (message.role !== 'assistant' || message.activityCard || message.runtimeActivity) return message;
    const parts = parsed[index];
    if (!parts) return message;
    const markdownParts = parts.flatMap(part => {
      if (part.pages) {
        const members = groups.get(part);
        return members.length < 2 ? members : [{...part, pages: members}];
      }
      if (!part.card_id) return [part];
      const card = turns[index].get(part.card_id);
      if (!card && latest.has(part.card_id)) return [];
      if (!card && !chatId) return [{ text: '::card[' + part.card_id + ']\n' }];
      const key = card || part.card_id;
      if (claimed.has(key) || placed.has(key)) return [];
      placed.add(key);
      return [card ? display(card) : part];
    });
    return { ...message, markdownParts };
  });
  return bodies.flatMap((message, index) => {
    const cards = references[index].filter(card => turns[index].get(card.card_id) === card && !placed.has(card));
    return [message, ...cards.map(card => ({
      id: 'inline:' + card.card_id + (latest.get(card.card_id) === card ? '' : ':' + card.revision),
      role: 'assistant', assistantCard: display(card), createdAt: message.createdAt, runId: message.runId,
    }))];
  });
}

export function initialCardValues(card) {
  const defaults = {};
  for (const block of card.spec.blocks) {
    if (block.type === 'form') for (const field of block.fields) {
      if (Object.hasOwn(field, 'default')) defaults[field.field_id] = field.default;
      else if (field.type === 'boolean') defaults[field.field_id] = false;
    }
    if (block.type === 'list') for (const item of block.items) {
      if (item.field_id) defaults[item.field_id] = false;
    }
    if (block.selection?.mode === 'multiple') defaults[block.selection.field_id] = [];
  }
  return { ...defaults, ...card.input_state.draft };
}

export function toggleCardSelection(current, id, mode) {
  if (mode === 'single') return id;
  const values = Array.isArray(current) ? current : [];
  return values.includes(id) ? values.filter(value => value !== id) : [...values, id];
}

export function cardEvent(card, kind, values, action, eventId) {
  return { event_id: eventId, base_revision: card.revision, base_input_revision: card.input_revision,
    kind, values, ...(action ? { action_id: action.action_id } : {}) };
}

export function cardDeliveryNotice(delivery) {
  if (delivery?.status === 'pending') return '正在发送…';
  if (delivery?.status === 'failed') return '提交已保存，但消息发送失败。请重试发送。';
  return '';
}

export function timelineStatusTone(status) {
  const text = String(status || '').trim().toLowerCase();
  if (/失败|出错|错误|受阻|阻塞|\b(failed|failure|error|blocked)\b/.test(text)) return 'error';
  if (/暂停|取消|停止|\b(paused|cancelled|canceled|stopped)\b/.test(text)) return 'paused';
  if (/待|等待|未完成|未开始|\b(pending|waiting|queued|todo|incomplete)\b|not (done|completed?|started)/.test(text)) return 'pending';
  if (/进行中|处理中|正在|执行中|\b(running|processing|active|in.progress)\b/.test(text)) return 'running';
  if (/已完成|已通过|已交付|已验证|^完成$|\b(done|completed?|succeeded|success|passed|delivered|verified)\b/.test(text)) return 'complete';
  return 'neutral';
}

// The sandbox cannot load workbench assets, so embed the same 24px chevron path.
const disclosureStyles = `
  details > summary { list-style: none; cursor: pointer }
  details > summary::-webkit-details-marker { display: none }
  details > summary:not(:has([data-cyrene-caret], svg))::before,
  [data-cyrene-caret] {
    content: ""; display: inline-block; width: 16px; height: 16px;
    background: currentColor; vertical-align: -0.2em;
    mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='m6 9 6 6 6-6' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E") center / contain no-repeat;
    transform: rotate(-90deg);
  }
  details > summary::before { margin-inline-end: .35em }
  [data-cyrene-caret] { font-size: 0 !important; flex-shrink: 0 }
  details[open] > summary::before, details[open] > summary [data-cyrene-caret],
  [aria-expanded=true] [data-cyrene-caret] { transform: none !important }
`;

export function customCardDocument(html, fieldIds = []) {
  const bridge = `(${installCardBridge.toString()})(${JSON.stringify(fieldIds).replace(/</g, "\\u003c")});`;
  // The first CSP cannot be relaxed by later markup. No same-origin, forms,
  // popups, external scripts or host APIs are granted to generated content.
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><meta name="viewport" content="width=device-width, initial-scale=1"><style>html{font-size:13px;color-scheme:light dark}html:root,html:root body{background:transparent!important}body{display:flow-root;font:13px/1.55 system-ui,sans-serif;color:CanvasText;margin:0;overflow-wrap:anywhere}button,input,select,textarea{font:inherit;max-width:100%}img{max-width:100%}
  ${disclosureStyles}</style><script>
  ${bridge}<\/script></head><body>${html}</body></html>`;
}
