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
  const candidates = await files(fileOwner);
  const path = target?.file_path;
  const exact = path ? candidates.filter(file => file.path === path) :
    candidates.filter(file => file.path && text.includes(file.path));
  const matches = exact.length ? exact : candidates.filter(file =>
    file.name && (path ? file.name === path : text.includes(file.name)));
  if (matches.length === 1) return { file: matches[0] };
  throw new Error(matches.length ? '有多个文件与这条成果匹配，请在原任务中查看。' : '未找到这条成果对应的卡片或文件。');
}
