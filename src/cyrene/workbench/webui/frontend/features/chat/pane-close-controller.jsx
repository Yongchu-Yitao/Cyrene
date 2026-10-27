import { workbenchServices } from "../../shared/runtime/services.jsx"
import { PluginFrontendService } from "../../platform/plugins.jsx"
import { wbcT } from "./core.jsx"
import { WBC_PROJECT_FILE_DRAFTS, wbcDiscardProjectFileDraft, wbcProjectFileDraftKey } from "./resource-splits.jsx"

function paneDraftKey(card, projectId) {
  if (card.kind === "file" || card.kind === "viewer") return wbcProjectFileDraftKey(card.payload);
  var resource = card.kind === "surface" && card.payload && card.payload.resource;
  return resource && resource.kind === "file" ? wbcProjectFileDraftKey({
    source: "project", projectId: resource.projectId || resource.project_id || projectId, path: resource.path,
  }) : "";
}

function confirmDraftClose() {
  var feedback = workbenchServices.feedback();
  return feedback.confirmModal ? feedback.confirmModal({
    title: wbcT("workbenchChat.editorUnsavedTitle", "Unsaved changes"),
    body: wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?"),
    confirmLabel: wbcT("workbenchChat.editorDiscard", "Discard changes"),
    danger: true,
  }) : window.confirm(wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?"));
}

function cleanupClosedPane(context, card, draftKey) {
  if (draftKey) {
    if (card.kind === "surface") delete WBC_PROJECT_FILE_DRAFTS[draftKey];
    else wbcDiscardProjectFileDraft(card.payload);
  }
  if (card.kind === "surface" && card.meta && card.meta.origin === "agent") {
    var payload = card.payload || {};
    context.surfaceSuppressionRef.current.set(
      String(payload.runId || "") + "\n" + String(payload.resourceKey || ""), true
    );
  }
  var pluginState = card.kind === "plugin-view" && card.payload && card.payload.state || {};
  var closeMethod = String(pluginState.closeMethod || "");
  var sessionId = String(pluginState.session_id || "");
  if (!closeMethod || !sessionId) return Promise.resolve();
  return PluginFrontendService.call(
    String(card.payload.packId || ""), closeMethod, { session_id: sessionId }, context.projectId
  ).catch(function () {});
}

export async function wbcClosePaneCardsWithConfirmation(context, cards, ownerChatId) {
  if (!cards.length) return { closed: false };
  var ids = cards.map(function (card) { return String(card.id); });
  var draftKeys = cards.map(function (card) {
    var key = paneDraftKey(card, context.projectId);
    return key && WBC_PROJECT_FILE_DRAFTS[key] ? key : "";
  });
  // A shared column divider closes the whole column. Confirm every draft
  // before discarding any of them or changing the conversation owner.
  for (var key of draftKeys) {
    if (key && !await confirmDraftClose()) return { closed: false, cancelled: true, card_id: ids[0], card_ids: ids };
  }
  await Promise.all(cards.map(function (card, index) { return cleanupClosedPane(context, card, draftKeys[index]); }));
  context.closePaneCard(ids.length === 1 ? ids[0] : ids, ownerChatId);
  return { closed: true, card_id: ids[0], card_ids: ids, discarded_draft: draftKeys.some(Boolean) };
}
