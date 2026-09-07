function indexGroups(groups) {
  const first = new Map(), memberships = new Map(), seen = new Set();
  for (const group of Array.isArray(groups) ? groups : []) {
    if (seen.has(group)) continue;
    seen.add(group);
    for (const id of new Set(Array.isArray(group.chatIds) ? group.chatIds : [])) {
      // The existing indexOf contract matches string IDs, not numeric aliases.
      if (typeof id !== 'string') continue;
      if (!first.has(id)) first.set(id, group);
      if (!memberships.has(id)) memberships.set(id, []);
      memberships.get(id).push(group);
    }
  }
  return { first, memberships };
}

export function wbcBuildChatRailItems(chats, groups) {
  const list = Array.isArray(chats) ? chats : [];
  if (!list.length) return [];
  const { first, memberships } = indexGroups(groups);
  const members = new Map(), rendered = new Set(), items = [];
  list.forEach(function (chat) {
    const id = String(chat && chat.id || '');
    for (const group of memberships.get(id) || []) {
      if (!members.has(group)) members.set(group, []);
      members.get(group).push(chat);
    }
  });
  list.forEach(function (chat) {
    const group = first.get(String(chat && chat.id || ''));
    if (!group) { items.push({ kind: 'chat', chat }); return; }
    if (rendered.has(group.id)) return;
    rendered.add(group.id);
    items.push({ kind: 'group', group, chats: members.get(group) || [] });
  });
  return items;
}

export function partitionRailItems(items, pinnedIds) {
  const groupItems = [], pinnedItems = [], recentItems = [];
  for (const item of items) {
    if (item.kind === 'group') groupItems.push(item);
    else if (item.kind === 'chat') {
      (pinnedIds.has(String(item.chat && item.chat.id || '')) ? pinnedItems : recentItems).push(item);
    }
  }
  return { groupItems, pinnedItems, recentItems };
}
