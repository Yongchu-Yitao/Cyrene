const persistentCardId = /^card_[a-f0-9]{32}$/;

function groupDirective(line) {
  const match = /^::card\[(book|stack)\]\s+(\{.*\})[ \t]*$/.exec(line);
  if (!match) return null;
  let data;
  try { data = JSON.parse(match[2]); } catch { return null; }
  const {title, subtitle, pages} = data;
  if (typeof title !== 'string' || !title.trim() || !Array.isArray(pages)
      || !pages.every(id => typeof id === 'string' && persistentCardId.test(id))) return null;
  if (match[1] === 'book' && (typeof subtitle !== 'string' || !subtitle.trim())) return null;
  const cover = {};
  if (['forest','ocean','clay','ink'].includes(data.cover?.palette)) cover.palette = data.cover.palette;
  if (['leaves','waves','lines'].includes(data.cover?.motif)) cover.motif = data.cover.motif;
  const ids = [...new Set(pages)];
  return ids.length >= 2 ? {format: match[1], title: title.trim(),
    subtitle: typeof subtitle === 'string' ? subtitle.trim() : '', pages: ids, ...(match[1] === 'book' && Object.keys(cover).length ? {cover} : {})} : null;
}

// Directives occupy a whole, unindented line. Fenced examples remain ordinary Markdown.
export function markdownCardParts(source) {
  if (typeof source !== 'string' || !source.includes('::card[')) return null;
  const parts = [];
  let start = 0, fence = '', rawEnd = '';
  for (const match of source.matchAll(/^([^\r\n]*)(?:\r?\n|$)/gm)) {
    const line = match[1];
    if (rawEnd) {
      if (line.toLowerCase().includes(rawEnd)) rawEnd = '';
      continue;
    }
    if (fence) {
      const closing = /^ {0,3}(`+|~+)[ \t]*$/.exec(line);
      if (closing && closing[1][0] === fence[0] && closing[1].length >= fence.length) fence = '';
      continue;
    }
    const opening = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (opening) { fence = opening[1]; continue; }
    const raw = /^ {0,3}(<!--|<(pre|script|style|textarea)(?:\s|>))/i.exec(line);
    if (raw) {
      const end = raw[2] ? '</' + raw[2].toLowerCase() + '>' : '-->';
      if (!line.toLowerCase().includes(end)) rawEnd = end;
      continue;
    }
    const card = /^::card\[(card_[a-f0-9]{32})\][ \t]*$/.exec(line);
    const group = !card && groupDirective(line);
    if (!card && !group) continue;
    if (start < match.index) parts.push({ text: source.slice(start, match.index) });
    parts.push(group || { card_id: card[1] });
    start = match.index + match[0].length;
  }
  if (!parts.length) return null;
  if (start < source.length) parts.push({ text: source.slice(start) });
  return parts;
}

export function legacyCardReply(runtime) {
  if (runtime?.timeline || !runtime?.text?.includes('::card[')) return null;
  return { id: runtime.replyRenderKey || 'live-card-reply', role: 'assistant',
    content: runtime.text, attachments: runtime.artifacts, timelineVersion: 1,
    status: runtime.streamDone ? 'completed' : 'running' };
}
