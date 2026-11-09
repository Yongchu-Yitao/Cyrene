import { installCardBridge } from "./assistant-card-bridge.mjs";
// Inline card references travel with tool results, never through model-written HTML.
export function cardReferences(message) {
  const entries = message.runtimeActivity?.progress || message.trace || [];
  return entries.flatMap(entry => {
    const p = entry.presentation;
    if (entry.failed || entry.status !== 'completed' || p?.kind !== 'assistant_card'
      || p.home_kind || p.operation !== 'card.create' || !/^card_[a-f0-9]{32}$/.test(p.card_id || '')) return [];
    return [{ card_id: p.card_id, revision: p.revision }];
  });
}

export function projectInlineCards(messages) {
  const seen = new Set();
  return messages.flatMap(message => {
    const cards = cardReferences(message).filter(card => {
      if (seen.has(card.card_id)) return false;
      seen.add(card.card_id);
      return true;
    });
    return [message, ...cards.map(card => ({
      id: 'inline:' + card.card_id, role: 'assistant', assistantCard: card,
      createdAt: message.createdAt, runId: message.runId,
    }))];
  });
}

export function initialCardValues(card) {
  const defaults = {};
  for (const block of card.spec.blocks) {
    if (block.type === 'form') for (const field of block.fields) {
      if (Object.hasOwn(field, 'default')) defaults[field.field_id] = field.default;
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

export function customCardDocument(html, fieldIds = []) {
  const bridge = `(${installCardBridge.toString()})(${JSON.stringify(fieldIds).replace(/</g, "\\u003c")});`;
  // The first CSP cannot be relaxed by later markup. No same-origin, forms,
  // popups, external scripts or host APIs are granted to generated content.
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font:14px/1.6 system-ui,sans-serif;margin:16px;overflow-wrap:anywhere}button,input,select,textarea{font:inherit;max-width:100%}img{max-width:100%}</style><script>
  ${bridge}<\/script></head><body>${html}</body></html>`;
}
