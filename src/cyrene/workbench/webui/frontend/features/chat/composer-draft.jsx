import { useWbcEffect, useWbcRef, useWbcState } from "../../workbench-chat.jsx"
import { WBC_DRAFT_SAVE_DELAY_MS, wbcLoadDraft, wbcSaveDraft } from "./messages.jsx"

export function useWbcComposerDraft(chatId, draftNs) {
  var [draft, setDraft] = useWbcState(function () { return wbcLoadDraft(chatId, draftNs); });
  var draftRef = useWbcRef(draft);
  var prevChatIdRef = useWbcRef(chatId);
  var draftSaveTimerRef = useWbcRef(0);
  var pendingDraftSaveRef = useWbcRef(null);

  useWbcEffect(function () { draftRef.current = draft; });
  useWbcEffect(function () {
    if (prevChatIdRef.current !== chatId) return;
    if (draftSaveTimerRef.current) window.clearTimeout(draftSaveTimerRef.current);
    pendingDraftSaveRef.current = { id: chatId, text: draft, ns: draftNs };
    draftSaveTimerRef.current = window.setTimeout(flushPendingDraftSave, WBC_DRAFT_SAVE_DELAY_MS);
  }, [draft, chatId, draftNs]);

  useWbcEffect(function () {
    function flushHiddenDraft() {
      if (document.visibilityState === "hidden") flushPendingDraftSave();
    }
    window.addEventListener("pagehide", flushPendingDraftSave);
    document.addEventListener("visibilitychange", flushHiddenDraft);
    return function () {
      window.removeEventListener("pagehide", flushPendingDraftSave);
      document.removeEventListener("visibilitychange", flushHiddenDraft);
      flushPendingDraftSave();
    };
  }, []);

  function flushPendingDraftSave() {
    if (draftSaveTimerRef.current) {
      window.clearTimeout(draftSaveTimerRef.current);
      draftSaveTimerRef.current = 0;
    }
    var pending = pendingDraftSaveRef.current;
    pendingDraftSaveRef.current = null;
    if (pending) wbcSaveDraft(pending.id, pending.text, pending.ns);
  }

  function persistCurrentDraft() {
    pendingDraftSaveRef.current = {
      id: chatId,
      text: String(draftRef.current || ""),
      ns: draftNs,
    };
    flushPendingDraftSave();
  }

  function switchChat(nextChatId) {
    flushPendingDraftSave();
    wbcSaveDraft(prevChatIdRef.current, draftRef.current, draftNs);
    setDraft(wbcLoadDraft(nextChatId, draftNs));
  }

  return { draft, setDraft, draftRef, prevChatIdRef, persistCurrentDraft, switchChat };
}
