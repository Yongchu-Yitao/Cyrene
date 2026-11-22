import { conversationCardsUrl } from './pinned-card-model.mjs';

export async function resolveHomeResult({ action, label, sessionId }, list, files) {
  const target = action.result_target;
  const fileOwner = target?.session_id || action.task_id || sessionId;
  if (target?.card_id) return { cardId: target.card_id, owner: target.session_id || sessionId };

  // Saved home rows predate typed targets. Match the original card by its
  // title within the original context, never by its position in the list.
  const text = [label, action.intent].join(' ');
  if (!target && action.target_context_id) {
    const query = new URLSearchParams({ scope: 'current_task', context_id: action.target_context_id, limit: '100' });
    const matches = [];
    let page;
    do {
      page = await list(conversationCardsUrl(sessionId) + '?' + query);
      matches.push(...page.cards.filter(card => !card.home_kind && card.title && text.includes(card.title)));
      if (page.next_cursor) query.set('cursor', page.next_cursor);
    } while (page.next_cursor);
    if (matches.length === 1) return { cardId: matches[0].card_id, owner: sessionId };
  }
  const path = target?.file_path?.replaceAll('\\', '/');
  const owners = [...new Set([fileOwner, action.task_id].filter(Boolean))];
  for (const owner of owners) {
    let index;
    try {
      index = await files(owner);
    } catch (error) {
      if (error.status === 404 && owner !== owners.at(-1)) continue;
      throw error;
    }
    const matches = matchingFiles(index, path, text);
    if (matches.length === 1) return { file: matches[0] };
    if (matches.length > 1) throw new Error('有多个文件与这条成果匹配，请在原任务中查看。');
  }
  throw new Error('在原任务的文件和卡片中找不到这项成果。');
}

function matchingFiles(index, path, text) {
  const root = index.workspace?.replaceAll('\\', '/').replace(/\/$/, '');
  const relative = root && path?.startsWith(root + '/') ? path.slice(root.length + 1) : path;
  const candidates = index.candidates;
  const exact = candidates.filter(file => {
    const candidate = file.path?.replaceAll('\\', '/');
    return candidate && (path ? candidate === path || candidate === relative : text.includes(candidate));
  });
  const matches = exact.length ? exact : candidates.filter(file =>
    file.name && (path ? file.name === path : text.includes(file.name)));
  return matches;
}
