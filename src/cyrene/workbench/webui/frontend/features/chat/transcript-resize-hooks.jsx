import { useWbcRef, useWbcLayoutEffect } from "../../workbench-chat.jsx"
import { protectTranscriptResize } from "./transcript-resize.mjs"

export function useMainTranscriptResize(scrollRef, mainRef, stickRef, chatId) {
  var resizeAvoidanceRef = useWbcRef(null);
  useWbcLayoutEffect(function () {
    var thread = scrollRef.current;
    var page = mainRef.current && mainRef.current.closest(".wbc-page");
    if (!thread || !page) return undefined;
    return protectTranscriptResize(thread, page, function () { return stickRef.current; }, function () { if (resizeAvoidanceRef.current) resizeAvoidanceRef.current(); }, chatId);
  }, [chatId]);
  return resizeAvoidanceRef;
}

export function useSplitTranscriptResize(scrollRef, splitRef, chatId, loading, messageCount, running, streamText) {
  useWbcLayoutEffect(function () {
    var thread = scrollRef.current;
    var page = splitRef.current && splitRef.current.closest(".wbc-page");
    if (!thread || !page) return undefined;
    var sticking = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 48;
    function trackPosition() {
      if (!thread.wbcResizeActive) sticking = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 48;
    }
    thread.addEventListener("scroll", trackPosition, { passive: true });
    var release = protectTranscriptResize(thread, page, function () { return sticking; }, undefined, chatId);
    return function () {
      thread.removeEventListener("scroll", trackPosition);
      release();
    };
  }, [chatId, loading]);

  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messageCount, loading, running, streamText]);


}
