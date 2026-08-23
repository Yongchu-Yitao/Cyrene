import { useWbcEffect, useWbcMemo, useWbcRef, useWbcState, wbcT } from "../../workbench-chat.jsx"
import {
  WBC_CHAT_ORDER_PREFIX,
  wbcBuildChatRailItems,
  wbcFindChatGroup,
  wbcLoadChatOrder,
  wbcMoveChatOrder,
  wbcNormalizeChatOrder,
  wbcOrderChatsByPinned,
} from "./rail-model.jsx"

function wbcDefaultRailOrder(chats, pinnedChatIds) {
  var defaultChats = wbcOrderChatsByPinned(chats, pinnedChatIds)
  return defaultChats.map(function (chat) { return String(chat.id) })
}

function useWbcRailOrdering({ chats, groups, pinnedChatIds, projectId, query, setAnnouncement }) {
  var defaultOrder = wbcDefaultRailOrder(chats, pinnedChatIds)
  var defaultOrderKey = defaultOrder.join("|")
  var [order, setOrder] = useWbcState(function () { return wbcLoadChatOrder(projectId, defaultOrder) })
  var orderRef = useWbcRef(order)
  useWbcEffect(function () { orderRef.current = order }, [order])

  var chatMap = new Map((Array.isArray(chats) ? chats : []).map(function (chat) { return [String(chat.id), chat] }))
  var orderedChats = wbcNormalizeChatOrder(defaultOrder, order).map(function (id) {
    return chatMap.get(id)
  }).filter(Boolean)
  var filtered = useWbcMemo(function () {
    var normalized = String(query || "").trim().toLowerCase()
    return !normalized ? orderedChats : orderedChats.filter(function (chat) {
      var group = wbcFindChatGroup(groups, chat.id)
      return String(chat.title || "").toLowerCase().indexOf(normalized) !== -1
        || String(chat.preview || "").toLowerCase().indexOf(normalized) !== -1
        || String(group && group.title || "").toLowerCase().indexOf(normalized) !== -1
        || String(group && group.summary || "").toLowerCase().indexOf(normalized) !== -1
    })
  }, [orderedChats, query, groups])
  var railItems = useWbcMemo(function () { return wbcBuildChatRailItems(filtered, groups) }, [filtered, groups])
  var pinnedIds = new Set((Array.isArray(pinnedChatIds) ? pinnedChatIds : []).map(function (id) { return String(id || "") }))

  function commitOrder(nextOrder, movedId) {
    var normalized = wbcNormalizeChatOrder(defaultOrder, nextOrder)
    var positionChanged = normalized.join("|") !== (orderRef.current || []).join("|")
    setOrder(normalized)
    try { localStorage.setItem(WBC_CHAT_ORDER_PREFIX + String(projectId || ""), JSON.stringify(normalized)) } catch (e) {}
    var movedChat = chatMap.get(String(movedId || ""))
    if (movedChat && positionChanged) {
      setAnnouncement(wbcT("workbenchChat.chatMoved", "{title} moved to position {position} of {total}.", {
        title: movedChat.title || wbcT("workbenchChat.newChat", "New chat"),
        position: normalized.indexOf(String(movedId)) + 1,
        total: normalized.length,
      }))
    }
  }

  function moveByKeyboard(event, id) {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return false
    var visibleOrder = filtered.map(function (chat) { return String(chat.id) })
    var index = visibleOrder.indexOf(String(id))
    var nextIndex = event.key === "ArrowUp" ? index - 1 : index + 1
    if (index < 0 || nextIndex < 0 || nextIndex >= visibleOrder.length) return false
    event.preventDefault()
    event.stopPropagation()
    var targetId = visibleOrder[nextIndex]
    commitOrder(wbcMoveChatOrder(order, String(id), targetId, event.key === "ArrowUp" ? "before" : "after"), id)
    return true
  }

  return {
    chatMap: chatMap,
    commitOrder: commitOrder,
    defaultOrder: defaultOrder,
    defaultOrderKey: defaultOrderKey,
    filtered: filtered,
    groupItems: railItems.filter(function (item) { return item.kind === "group" }),
    moveByKeyboard: moveByKeyboard,
    order: order,
    orderedChats: orderedChats,
    orderRef: orderRef,
    pinnedItems: railItems.filter(function (item) { return item.kind === "chat" && pinnedIds.has(String(item.chat && item.chat.id || "")) }),
    recentItems: railItems.filter(function (item) { return item.kind === "chat" && !pinnedIds.has(String(item.chat && item.chat.id || "")) }),
    setOrder: setOrder,
  }
}

export { useWbcRailOrdering, wbcDefaultRailOrder }
