import { useWbcState } from "../../workbench-chat.jsx"
import { wbcFindChatGroup } from "./rail-model.jsx"

function useWbcRailDropController(groups) {
  var [dragState, setDragState] = useWbcState(null)

  function update(next) {
    setDragState(function (current) {
      if (!current || !next) return next
      var resolved = {
        ...next,
        dragKind: next.dragKind === undefined ? current.dragKind : next.dragKind,
        movingGroupId: next.movingGroupId === undefined ? current.movingGroupId : next.movingGroupId,
        movingIds: next.movingIds === undefined ? current.movingIds : next.movingIds,
        sourceGroupId: next.sourceGroupId === undefined ? current.sourceGroupId : next.sourceGroupId,
      }
      if (
        current.dragKind === resolved.dragKind
        && current.movingId === resolved.movingId
        && current.movingGroupId === resolved.movingGroupId
        && (current.movingIds || []).join("|") === (resolved.movingIds || []).join("|")
        && current.targetId === resolved.targetId
        && current.targetGroupId === resolved.targetGroupId
        && current.sourceGroupId === resolved.sourceGroupId
        && current.edge === resolved.edge
        && current.mode === resolved.mode
      ) return current
      return resolved
    })
  }

  function canGroup(movingId, targetId) {
    var movingGroup = wbcFindChatGroup(groups, movingId)
    var targetGroup = wbcFindChatGroup(groups, targetId)
    return !(movingGroup && targetGroup && movingGroup.id === targetGroup.id)
  }

  function mode(event, movingId, targetId) {
    if (!canGroup(movingId, targetId)) return "reorder"
    if (dragState && dragState.mode === "group" && dragState.movingId === String(movingId) && dragState.targetId === String(targetId)) return "group"
    var rect = event.currentTarget.getBoundingClientRect()
    var ratio = rect.height ? (event.clientY - rect.top) / rect.height : 0
    return ratio >= 0.22 && ratio <= 0.78 ? "group" : "reorder"
  }

  return { dragState: dragState, mode: mode, setDragState: setDragState, update: update }
}

export { useWbcRailDropController }
