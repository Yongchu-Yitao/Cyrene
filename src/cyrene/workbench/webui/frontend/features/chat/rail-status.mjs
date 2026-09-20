var WBC_FAILED_CHAT_STATUSES = new Set(["error", "failed", "failure", "timeout"]);
var WBC_ATTENTION_CHAT_STATUSES = new Set([
  "awaiting_user", "waiting_for_user", "waiting_for_approval", "needs_input",
  "waiting_input", "requires_confirmation", "blocked", "review",
]);
var WBC_COMPLETED_CHAT_STATUSES = new Set(["completed", "complete", "done", "success", "succeeded"]);

function wbcChatRailStatusKind(chat, running) {
  // Resumed work outranks the previous exchange's pending question or final
  // status until the durable conversation summary refreshes.
  if (running) return "running";
  var rawStatus = String(chat.runStatus || chat.status || "").trim().toLowerCase();
  if (chat.failed || chat.error || WBC_FAILED_CHAT_STATUSES.has(rawStatus)) return "failed";
  if (chat.awaitingUser || chat.pendingQuestion || WBC_ATTENTION_CHAT_STATUSES.has(rawStatus)) return "attention";
  return WBC_COMPLETED_CHAT_STATUSES.has(rawStatus) ? "completed" : "";
}

function wbcChatRailIcon(kind, icons) {
  if (kind === "running") return icons.running;
  if (kind === "failed") return icons.errorCircle;
  if (kind === "attention") return icons.alert;
  return kind === "completed" ? icons.check : icons.file;
}

function wbcChatRailVisualState(chat, running, icons, translate) {
  var kind = wbcChatRailStatusKind(chat, running);
  return {
    running: kind === "running",
    tone: kind ? " status-" + kind : "",
    icon: wbcChatRailIcon(kind, icons),
    label: kind === "failed" ? translate("status.failed", "Failed") : kind === "attention" ? translate("workbenchChat.awaitingUser", "Needs input") : "",
  };
}

export { wbcChatRailVisualState }
