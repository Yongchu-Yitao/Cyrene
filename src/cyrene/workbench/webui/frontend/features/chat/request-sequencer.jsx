import { useWbcRef } from "../../workbench-chat.jsx"

function useWbcChatRequestSequencer() {
  var listSequences = useWbcRef({});
  var hydrationSequences = useWbcRef({});

  function beginHydration(chatId) {
    var id = String(chatId || "");
    var next = Number(hydrationSequences.current[id] || 0) + 1;
    hydrationSequences.current[id] = next;
    return next;
  }

  function isCurrentHydration(chatId, sequence) {
    return Number(hydrationSequences.current[String(chatId || "")] || 0) === sequence;
  }

  function beginList(requestedProjectId) {
    var id = String(requestedProjectId || "");
    var next = Number(listSequences.current[id] || 0) + 1;
    listSequences.current[id] = next;
    return next;
  }

  function isCurrentList(requestedProjectId, sequence) {
    return Number(listSequences.current[String(requestedProjectId || "")] || 0) === sequence;
  }

  return {
    beginHydration: beginHydration,
    isCurrentHydration: isCurrentHydration,
    beginList: beginList,
    isCurrentList: isCurrentList,
  };
}

export { useWbcChatRequestSequencer }
