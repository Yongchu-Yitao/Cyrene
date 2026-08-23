import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_AGENT_CHAT_FLOW_EVENT, WBC_ICONS, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcBuildRailCardDragPreview, wbcErrorText, wbcFileViewKind, wbcFormatTime, wbcHasChatDrag, wbcHasChatRailDrag, wbcHasTaskDrag, wbcHideNativeDragImage, wbcNotifyAgentChatFlow, wbcSetChatDrag, wbcSetChatGroupDrag, wbcSetResourceDrag, wbcSetTaskDrag, wbcT } from "../../workbench-chat.jsx"
import { wbcPermissionOptionLabel, wbcPermissionQuestionText, wbcQuestionOptionValue } from "./conversation.jsx"
import { wbcStartFileDrag } from "./file-resources.jsx"

import { moveChatOrderBlock } from "./behavior.mjs"

// Workbench chat rail and project navigation.
function WbcRenameDialog({ chat, onClose, onRename, entity }) {
  var [draft, setDraft] = useWbcState(chat ? chat.title || "" : "");
  var [saving, setSaving] = useWbcState(false);
  var [error, setError] = useWbcState("");
  var inputRef = useWbcRef(null);
  var originalTitle = String((chat && chat.title) || "");
  var nextTitle = String(draft || "").trim();
  var canSave = !!nextTitle && nextTitle !== originalTitle && !saving;
  var isGroup = entity === "group";
  var isTerminal = entity === "terminal";
  var isTask = entity === "task";

  useWbcEffect(function () {
    setDraft(originalTitle);
    setError("");
    setSaving(false);
    requestAnimationFrame(function () {
      if (inputRef.current) {
        inputRef.current.focus();
        inputRef.current.select();
      }
    });
  }, [chat && chat.id]);

  function close() {
    if (!saving && onClose) onClose();
  }

  function submit(e) {
    if (e) e.preventDefault();
    if (!canSave || !chat || !onRename) return;
    setSaving(true);
    setError("");
    onRename(chat.id, nextTitle).then(function () {
      workbenchServices.feedback().showToast(
        isGroup
          ? wbcT("workbenchChat.groupRenameSuccess", "Chat group renamed")
          : isTerminal
            ? wbcT("terminal.renameSuccess", "Terminal renamed")
            : isTask
              ? wbcT("task.renameSuccess", "Task renamed")
          : wbcT("workbenchChat.renameSuccess", "Chat renamed"),
        "success"
      );
      if (onClose) onClose();
    }).catch(function (err) {
      setError(wbcErrorText(err));
      setSaving(false);
    });
  }

  if (!chat) return null;
  return window.ReactDOM.createPortal(
    <div
      className="wbc-rename-scrim"
      onMouseDown={function (e) { if (e.target === e.currentTarget) close(); }}
      onKeyDown={function (e) { if (e.key === "Escape") close(); }}
    >
      <form
        className="wbc-rename-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wbc-rename-title"
        onSubmit={submit}
      >
        <div className="wbc-rename-head">
          <strong id="wbc-rename-title">{isGroup
            ? wbcT("workbenchChat.groupRename", "Rename group")
            : isTerminal
              ? wbcT("terminal.rename", "Rename terminal")
              : isTask
                ? wbcT("task.rename", "Rename task")
            : wbcT("workbenchChat.rename", "Rename chat")}</strong>
          <button
            type="button"
            className="wbc-rename-close"
            aria-label={wbcT("common.close", "Close")}
            disabled={saving}
            onClick={close}
          >{WBC_ICONS.x}</button>
        </div>
        <div className="wbc-rename-body">
          <label htmlFor="wbc-rename-input">{isGroup
            ? wbcT("workbenchChat.groupTitleLabel", "Group title")
            : isTerminal
              ? wbcT("terminal.titleLabel", "Terminal title")
              : isTask
                ? wbcT("task.titleLabel", "Task title")
            : wbcT("workbenchChat.titleLabel", "Chat title")}</label>
          <input
            id="wbc-rename-input"
            ref={inputRef}
            value={draft}
            maxLength={60}
            disabled={saving}
            onChange={function (e) {
              setDraft(e.target.value);
              if (error) setError("");
            }}
            placeholder={isGroup
              ? wbcT("workbenchChat.groupRenamePlaceholder", "Enter a group title")
              : isTask
                ? wbcT("task.renamePlaceholder", "Enter a task title")
              : wbcT("workbenchChat.renamePlaceholder", "Enter a chat title")}
          />
          <div className="wbc-rename-meta">
            <span className={error ? "is-error" : ""} role={error ? "alert" : undefined}>
              {error || (!nextTitle ? wbcT("workbenchChat.renameRequired", "The title cannot be empty") : "")}
            </span>
            <span>{String(draft || "").length}/60</span>
          </div>
        </div>
        <div className="wbc-rename-foot">
          <button type="button" className="wb-btn" disabled={saving} onClick={close}>
            {wbcT("common.cancel", "Cancel")}
          </button>
          <button type="submit" className="wb-btn primary" data-cyrene-risk="R2" disabled={!canSave}>
            {saving ? wbcT("common.saving", "Saving...") : wbcT("common.save", "Save")}
          </button>
        </div>
      </form>
    </div>,
    document.querySelector(".workbench-shell") || document.body
  );
}


export { WbcRenameDialog }
