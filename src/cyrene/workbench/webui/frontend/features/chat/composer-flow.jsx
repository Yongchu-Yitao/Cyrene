import {
  WBC_AGENT_CHAT_FLOW_EVENT,
  useWbcEffect,
  useWbcRef,
  useWbcState,
  wbcAgentChatFlowSnapshot,
} from "../../workbench-chat.jsx"

function useWbcComposerAgentFlow(chatId) {
  var [state, setState] = useWbcState(function () {
    return wbcAgentChatFlowSnapshot(chatId) || { chatId: "", kind: "", expiresAt: 0 }
  })
  var timerRef = useWbcRef(null)

  useWbcEffect(function () {
    function clearTimer() {
      if (!timerRef.current) return
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    function applyFlow(detail) {
      var next = detail && typeof detail === "object" ? detail : null
      if (!next || String(next.chatId || "") !== String(chatId || "")) return
      var expiresAt = Number(next.expiresAt || 0)
      var remaining = Math.max(0, expiresAt - Date.now())
      clearTimer()
      if (!remaining) {
        setState({ chatId: String(chatId || ""), kind: "", expiresAt: 0 })
        return
      }
      setState({ chatId: String(chatId || ""), kind: String(next.kind || ""), expiresAt: expiresAt })
      timerRef.current = window.setTimeout(function () {
        timerRef.current = null
        setState({ chatId: String(chatId || ""), kind: "", expiresAt: 0 })
      }, remaining)
    }
    function onAgentChatFlow(event) {
      applyFlow(event && event.detail)
    }
    applyFlow(wbcAgentChatFlowSnapshot(chatId))
    window.addEventListener(WBC_AGENT_CHAT_FLOW_EVENT, onAgentChatFlow)
    return function () {
      window.removeEventListener(WBC_AGENT_CHAT_FLOW_EVENT, onAgentChatFlow)
      clearTimer()
    }
  }, [chatId])

  return state.chatId === String(chatId || "") ? String(state.kind || "") : ""
}

export { useWbcComposerAgentFlow }
