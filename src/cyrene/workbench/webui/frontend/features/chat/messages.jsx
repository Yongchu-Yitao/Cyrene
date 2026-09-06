import { wbcProjectTranscript } from "./runtime-timeline.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_ICONS, WbcVoice, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcAgentErrorPresentation, wbcAttachmentTypeLabel, wbcCompactNumber, wbcErrorText, wbcFileViewKind, wbcFormatProcessingDuration, wbcFormatTime, wbcRandomThinkingPhrase, wbcRenderMarkdown, wbcRuntimeTimelineMessages, wbcT, wbcToolPresentationKind, wbcToolPresentationText, wbcToolPreviewText } from "../../workbench-chat.jsx"
import { WbcThreadItem, wbcElicitationFields, wbcElicitationInitialValues, wbcPermissionOptionLabel, wbcPermissionQuestionText, wbcQuestionOptionValue, wbcValidateElicitationForm } from "./conversation.jsx"
import { WbcFileVisual, wbcCanOpenExternally, wbcDownloadLink, wbcStartFileDrag, wbcStartFilePointerDrag, wbcUsesFilePointerDrag } from "./file-resources.jsx"
import { useWorkbenchI18n } from "../../workbench-i18n.jsx"

// Workbench chat feature module with explicit ESM dependencies.
var wbcDisclosureListeners = new Set();
function wbcDisclosureValue(id) {
  try { var value = localStorage.getItem("cyrene:disclosure:" + id); return value === null ? null : value === "open"; } catch (_) { return null; }
}
function wbcUseDisclosure(id, children) {
  function read() {
    var saved = wbcDisclosureValue(id);
    if (saved !== null) return saved;
    var inherited = (children || []).some(wbcDisclosureValue);
    if (inherited) { try { localStorage.setItem("cyrene:disclosure:" + id, "open"); } catch (_) {} }
    return inherited;
  }
  var [expanded, setExpanded] = useWbcState(read);
  useWbcEffect(function () {
    function sync() { setExpanded(read()); }
    sync();
    wbcDisclosureListeners.add(sync);
    window.addEventListener("storage", sync);
    return function () { wbcDisclosureListeners.delete(sync); window.removeEventListener("storage", sync); };
  }, [id, (children || []).join("\0")]);
  function change(value) {
    try {
      localStorage.setItem("cyrene:disclosure:" + id, value ? "open" : "closed");
    } catch (_) {}
    setExpanded(value);
    wbcDisclosureListeners.forEach(function (notify) { notify(); });
  }
  return [expanded, change];
}

function wbcLocalizedToolName(toolName) {
  var raw = String(toolName || "").trim();
  if (!raw) return wbcT("workbenchChat.toolFallback", "Tool");
  var i18n = workbenchServices.i18n();
  if (typeof i18n.toolName === "function") {
    return i18n.toolName(raw, typeof i18n.getLang === "function" ? i18n.getLang() : undefined);
  }
  return wbcT("toolName." + raw, raw);
}

function wbcParenthesize(value) {
  var text = String(value || "");
  if (!text) return "";
  return workbenchServices.i18n().getLang() === "zh"
    ? "（" + text + "）"
    : "(" + text + ")";
}

function WbcQuestionPrompt({ pending, onAnswer, busy, trace }) {
  var pq = pending || {};
  var options = Array.isArray(pq.options) ? pq.options : [];
  var kind = String(pq.kind || "");
  var isPermission = kind === "permission.requested"
    || workbenchServices.model().isPermissionQuestionKind(kind);
  var isPlanConfirmation = kind === "plan_confirmation";
  // Permission choices are Agent-owned protocol data. Never fabricate option
  // ids from localized labels when the Agent omitted them.
  var treeOptions = options;
  var customState = useWbcState("");
  var customText = customState[0], setCustomText = customState[1];
  var schema = pq.schema && typeof pq.schema === "object" ? pq.schema : null;
  var schemaFields = wbcElicitationFields(pq);
  var formState = useWbcState(function () { return wbcElicitationInitialValues(schemaFields); });
  var formValues = formState[0], setFormValues = formState[1];
  var formErrorsState = useWbcState({});
  var formErrors = formErrorsState[0], setFormErrors = formErrorsState[1];
  var hasSchemaForm = !isPermission && schemaFields.length > 0;
  var optionSignature = JSON.stringify(treeOptions);
  useWbcEffect(function () {
    if (!pq.id || busy || !onAnswer || !window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var risk = isPermission ? "R3" : "R2";
    var actions = treeOptions.map(function (_opt, index) {
      return {
        action_id: "answer_option_" + index,
        kind: "invoke",
        risk: risk,
        gesture_aliases: ["press"],
        input_schema: {},
      };
    });
    if (pq.allowCustom && !isPermission) {
      actions.push({
        action_id: "answer_custom",
        kind: "set_value",
        risk: "R2",
        gesture_aliases: ["text_input"],
        input_schema: { value: "text<=20000" },
      });
    }
    var handlers = {};
    treeOptions.forEach(function (opt, index) {
      handlers["answer_option_" + index] = function () {
        var resumeMode = isPlanConfirmation && index === 0 ? "auto" : undefined;
        return Promise.resolve(onAnswer(pq.id, wbcQuestionOptionValue(opt), resumeMode)).then(function () {
          return { question_id: String(pq.id), answered: true, option_index: index };
        });
      };
    });
    if (pq.allowCustom && !isPermission) {
      handlers.answer_custom = function (input) {
        var answer = String(input.value || "").trim();
        if (!answer) throw new Error("answer is empty");
        return Promise.resolve(onAnswer(pq.id, answer)).then(function () {
          return { question_id: String(pq.id), answered: true, custom: true };
        });
      };
    }
    return uiSurface.register({
      node_id: "chat_question_" + String(pq.id).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100),
      parent_id: "root",
      scope: "main",
      get_node: function () {
        if (busy) return null;
        return {
          role: isPermission ? "approval" : "question",
          name: String(isPermission
            ? wbcPermissionQuestionText(pq)
            : (pq.text || wbcT("workbenchChat.questionFallback", "Agent needs your confirmation to continue."))),
          value_summary: treeOptions.length + " options",
          state: {
            question_id: String(pq.id),
            question_kind: kind,
            permission: isPermission,
            allow_custom: !!pq.allowCustom && !isPermission,
          },
        };
      },
      actions: actions,
      handlers: handlers,
    });
  }, [pq.id, pq.allowCustom, kind, busy, onAnswer, optionSignature, isPermission, isPlanConfirmation]);
  useWbcEffect(function () {
    setFormValues(wbcElicitationInitialValues(schemaFields));
    setFormErrors({});
  }, [pq.id, JSON.stringify(schema || pq.fields || [])]);
  function submitCustom() {
    var t = String(customText || "").trim();
    if (!t || busy || !onAnswer) return;
    setCustomText("");
    onAnswer(pq.id, t);
  }
  function submitForm(event) {
    if (event) event.preventDefault();
    if (busy || !onAnswer) return;
    var validation = wbcValidateElicitationForm(schemaFields, formValues);
    setFormErrors(validation.errors);
    if (!validation.valid) return;
    onAnswer(pq.id, { __agentForm: true, values: validation.values });
  }
  return (
    <div className="wbc-question-group">
      {trace && trace.length > 0 && <WbcTraceCard trace={trace} />}
      <div className="wbc-question">
        <div className="wbc-question-head">
          <span className="wbc-question-ico">{WBC_ICONS.alert}</span>
          <b>{isPermission ? wbcT("workbenchChat.permissionTitle", "Authorization needed") : wbcT("workbenchChat.questionTitle", "Confirmation needed")}</b>
        </div>
        <p className="wbc-question-text">{isPermission
          ? wbcPermissionQuestionText(pq)
          : (pq.text || wbcT("workbenchChat.questionFallback", "Agent needs your confirmation to continue."))}</p>
        {isPermission ? (
          <div className="wbc-question-options">
            {options.length ? options.map(function (opt, i) {
              return <button key={wbcQuestionOptionValue(opt) || i} type="button" className={"wbc-question-opt" + (i === 0 ? " primary" : "")} disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, wbcQuestionOptionValue(opt)); }}><span>{wbcPermissionOptionLabel(opt, i, options.length)}</span>{opt && opt.description ? <small>{String(opt.description)}</small> : null}</button>;
            }) : <div className="wbc-question-protocol-error" role="alert">{wbcT("workbenchChat.permissionNoOptions", "This Agent did not provide valid permission choices. Restart the Agent or check its protocol compatibility.")}</div>}
          </div>
        ) : hasSchemaForm ? (
          <form className="wbc-agent-schema-form" onSubmit={submitForm} noValidate>
            {schemaFields.map(function (field) {
              var value = Object.prototype.hasOwnProperty.call(formValues, field.name) ? formValues[field.name] : "";
              var error = formErrors[field.name];
              var controlId = "agent-field-" + String(pq.id || "request") + "-" + field.name.replace(/[^a-zA-Z0-9_-]/g, "-");
              return <label className={"wbc-agent-schema-field" + (error ? " invalid" : "")} key={field.name} htmlFor={controlId}>
                <span>{field.title || field.name}{field.required ? <b aria-hidden="true"> *</b> : null}</span>
                {field.description ? <small className="wbc-agent-schema-help">{field.description}</small> : null}
                {field.enumValues.length ? <select id={controlId} value={String(value == null ? "" : value)} disabled={busy} required={field.required} onChange={function (e) { setFormValues({ ...formValues, [field.name]: e.target.value }); }}>
                  {!field.required ? <option value="">{wbcT("workbenchChat.elicitationOptional", "Optional")}</option> : null}
                  {field.enumValues.map(function (option, index) { return <option key={String(option.value) + index} value={String(option.value)}>{option.label}</option>; })}
                </select> : field.type === "boolean" ? <input id={controlId} type="checkbox" checked={value === true} disabled={busy} onChange={function (e) { setFormValues({ ...formValues, [field.name]: e.target.checked }); }} /> : <input id={controlId} type={field.inputType} value={String(value == null ? "" : value)} disabled={busy} required={field.required} min={field.minimum} max={field.maximum} minLength={field.minLength} maxLength={field.maxLength} placeholder={field.placeholder || ""} aria-invalid={error ? "true" : undefined} aria-describedby={error ? controlId + "-error" : undefined} onChange={function (e) { setFormValues({ ...formValues, [field.name]: field.type === "number" || field.type === "integer" ? e.target.value : e.target.value }); }} />}
                {error ? <small id={controlId + "-error"} className="wbc-agent-schema-error" role="alert">{error}</small> : null}
              </label>;
            })}
            <button type="submit" className="wbc-question-opt primary wbc-agent-schema-submit" disabled={busy}>{wbcT("workbenchChat.elicitationSubmit", "Submit to Agent")}</button>
          </form>
        ) : (
          <React.Fragment>
            {isPlanConfirmation && options.length > 0 ? (
              <div className="wbc-question-options">
                <button type="button" className="wbc-question-opt primary" disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, options[0], "auto"); }}>
                  {options[0] || wbcT("workbenchChat.approveAuto", "Confirm and continue in Auto")}
                </button>
                <button type="button" className="wbc-question-opt" disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, options.length ? options[options.length - 1] : "拒绝"); }}>
                  {options.length ? options[options.length - 1] : wbcT("workbenchChat.reject", "Reject")}
                </button>
              </div>
            ) : options.length > 0 && (
              <div className="wbc-question-options">
                {options.map(function (opt, i) {
                  return <button key={i} type="button" className={"wbc-question-opt" + (i === 0 ? " primary" : "")} disabled={busy} onClick={function () { if (!busy && onAnswer) onAnswer(pq.id, opt); }}>{opt}</button>;
                })}
              </div>
            )}
            {pq.allowCustom && (
              <div className="wbc-question-custom">
                <input type="text" value={customText} placeholder={wbcT("workbenchChat.customAnswer", "Or enter a custom reply...")} disabled={busy}
                  onChange={function (e) { setCustomText(e.target.value); }}
                  onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); submitCustom(); } }} />
                <button type="button" className="wbc-question-send" disabled={busy || !String(customText).trim()} onClick={submitCustom}>{WBC_ICONS.send}</button>
              </div>
            )}
          </React.Fragment>
        )}
      </div>
    </div>
  );
}

function WbcErrorNotice({ message, kind, onRetry }) {
  var isMessageError = kind === "message";
  var isMemoryError = kind === "memory";
  var title = isMemoryError
    ? wbcT("workbenchChat.error.memoryTitle", "Could not generate memory")
    : (isMessageError
      ? wbcT("workbenchChat.error.messageTitle", "Message processing failed")
      : wbcT("workbenchChat.error.title", "Could not load this chat"));
  var failureKind = String(message && (message.failureKind || message.code) || "").trim();
  var agentName = String(message && message.agentId || "").trim();
  var detail = wbcErrorText(message) || wbcT("workbenchChat.error.loadFailed", "Load failed");
  var generic = wbcT("workbenchChat.error.loadFailed", "Load failed");
  var body = detail === generic
    ? (isMemoryError
      ? wbcT("workbenchChat.error.memoryBody", "Project memory could not be generated from this conversation. Try again.")
      : (isMessageError
        ? wbcT("workbenchChat.error.messageBody", "The message was saved but could not be processed. Retry to run it again.")
        : wbcT("workbenchChat.error.body", "The conversation data did not load. Check the local service and try again.")))
    : detail;
  var agentPresentation = isMessageError ? wbcAgentErrorPresentation(detail, failureKind) : null;
  if (isMessageError && failureKind && !agentPresentation) {
    agentPresentation = {
      tone: "runtime",
      title: wbcT("workbenchChat.agentError.failed", "Agent run failed"),
      summary: body,
      hint: wbcT("workbenchChat.error.agentGenericHint", "Open Agent diagnostics for details, then retry."),
    };
  }
  if (agentPresentation) title = agentPresentation.title;
  function copyErrorDetail() {
    var copied = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(detail || body)
      : Promise.reject(new Error("Clipboard unavailable"));
    copied.then(function () {
      workbenchServices.feedback().showToast(wbcT("workbenchChat.error.copied", "Error details copied"), "success");
    }).catch(function () {
      workbenchServices.feedback().showToast(wbcT("workbenchChat.error.copyFailed", "Could not copy error details"), "error");
    });
  }
  return (
    <div className={"workbench-error wbc-error-card" + (agentPresentation ? " is-agent-error is-" + agentPresentation.tone : "")} role="alert">
      <span className="wbc-error-icon">{WBC_ICONS.alert}</span>
      <span className="wbc-error-copy">
        <b>{title}</b>
        {failureKind ? <small className="wbc-error-meta">{[agentName, failureKind].filter(Boolean).join(" · ")}</small> : null}
        {agentPresentation ? <React.Fragment>
          <small className="wbc-error-summary">{agentPresentation.summary}</small>
          <small className="wbc-error-hint">{agentPresentation.hint}</small>
          <pre className="wbc-error-detail" aria-label={wbcT("workbenchChat.error.technicalDetail", "Technical details")}>{detail}</pre>
        </React.Fragment> : <small>{body}</small>}
      </span>
      <span className="wbc-error-actions">
        {agentPresentation ? <button type="button" className="wbc-error-copy-button" onClick={copyErrorDetail}>{WBC_ICONS.copy}<span>{wbcT("workbenchChat.error.copyDetail", "Copy details")}</span></button> : null}
        {onRetry && <button type="button" className="wbc-error-retry" onClick={onRetry}>{wbcT("workbenchChat.error.retry", "Retry")}</button>}
      </span>
    </div>
  );
}

function WbcAgentNotification({ notice }) {
  var item = notice && typeof notice === "object" ? notice : {};
  var category = String(item.category || "transport_warning");
  var message = String(item.message || "").trim();
  var titleByCategory = {
    transport_fallback: wbcT("workbenchChat.notification.transportFallback", "Transport fallback"),
    transport_timeout: wbcT("workbenchChat.notification.transportTimeout", "Transport timed out"),
    tls_certificate: wbcT("workbenchChat.notification.tlsCertificate", "Secure connection warning"),
    transport_warning: wbcT("workbenchChat.notification.transportWarning", "Connection warning"),
  };
  var title = titleByCategory[category] || titleByCategory.transport_warning;
  function copyDetail() {
    var copied = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(message)
      : Promise.reject(new Error("Clipboard unavailable"));
    copied.then(function () {
      workbenchServices.feedback().showToast(wbcT("workbenchChat.notification.copied", "Notification details copied"), "success");
    }).catch(function () {
      workbenchServices.feedback().showToast(wbcT("workbenchChat.error.copyFailed", "Could not copy error details"), "error");
    });
  }
  if (!message) return null;
  return (
    <aside className="wbc-agent-notification is-warning" role="status" aria-live="polite">
      <span className="wbc-agent-notification-icon" aria-hidden="true">{WBC_ICONS.alert}</span>
      <span className="wbc-agent-notification-copy">
        <b>{title}</b>
        <small>{wbcT("workbenchChat.notification.nonTerminal", "The Agent reported a recoverable connection issue. This is not part of its reply.")}</small>
        <code>{message}</code>
      </span>
      <button type="button" className="wbc-agent-notification-action" onClick={copyDetail} aria-label={wbcT("workbenchChat.notification.copy", "Copy notification details")} title={wbcT("workbenchChat.notification.copy", "Copy notification details")}>{WBC_ICONS.copy}</button>
    </aside>
  );
}

function WbcHeader({ project, chat, running, finalizing, onRename, onDelete }) {
  var [editing, setEditing] = useWbcState(false);
  var [draft, setDraft] = useWbcState(chat.title || "");
  var [menuOpen, setMenuOpen] = useWbcState(false);
  var inputRef = useWbcRef(null);

  useWbcEffect(function () {
    setDraft(chat.title || "");
    setEditing(false);
    setMenuOpen(false);
  }, [chat.id]);

  useWbcEffect(function () {
    if (editing && inputRef.current) { inputRef.current.focus(); inputRef.current.select(); }
  }, [editing]);

  function commitTitle() {
    var next = String(draft || "").trim();
    setEditing(false);
    if (!next || next === chat.title) { setDraft(chat.title || ""); return; }
    onRename(next).catch(function (err) {
      workbenchServices.feedback().showToast(err.message || String(err), "error");
      setDraft(chat.title || "");
    });
  }

  var statusText = finalizing
    ? wbcT("workbenchChat.status.saving", "Saving")
    : running ? wbcT("workbenchChat.status.replying", "Replying") : wbcT("workbenchChat.status.idle", "Idle");

  return (
    <div className="wbc-header">
      <div className="wbc-header-info">
        <div className="wbc-header-title">
          {editing ? (
            <input
              ref={inputRef}
              className="wbc-title-input"
              value={draft}
              onChange={function (e) { setDraft(e.target.value); }}
              onBlur={commitTitle}
              onKeyDown={function (e) {
                if (e.key === "Enter") commitTitle();
                if (e.key === "Escape") { setDraft(chat.title || ""); setEditing(false); }
              }}
              aria-label={wbcT("workbenchChat.titleLabel", "Chat title")}
            />
          ) : (
            <h1 title={chat.title}>{chat.title || wbcT("workbenchChat.newChat", "New chat")}</h1>
          )}
          {!editing && (
            <button type="button" className="wbc-icon-btn" title={wbcT("workbenchChat.rename", "Rename chat")} onClick={function () { setEditing(true); }}>
              {WBC_ICONS.edit}
            </button>
          )}
        </div>
        <div className="wbc-header-meta">
          <span className={"wbc-status-chip" + (running ? " running" : "")}>{statusText}</span>
          <span>{chat.model || "—"}</span>
          <span>{project.name}</span>
        </div>
      </div>
      <div className="wbc-header-actions">
        <div className="wbc-menu-wrap">
          <button type="button" className="wbc-icon-btn" title={wbcT("workbenchChat.more", "More")} onClick={function () { setMenuOpen(!menuOpen); }}>
            {WBC_ICONS.dots}
          </button>
          {menuOpen && (
            <>
              <div className="wbc-menu-scrim" onClick={function () { setMenuOpen(false); }}></div>
              <div className="wbc-menu">
                <button type="button" onClick={function () { setMenuOpen(false); setEditing(true); }}>{WBC_ICONS.edit}<span>{wbcT("workbenchChat.rename", "Rename chat")}</span></button>
                <button type="button" className="danger" onClick={function () { setMenuOpen(false); onDelete(); }}>{WBC_ICONS.trash}<span>{wbcT("workbenchChat.delete", "Delete chat")}</span></button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function WbcMessageAttachment({ file, onOpenFile }) {
  var [imageFailed, setImageFailed] = useWbcState(false);
  var viewKind = wbcFileViewKind(file);
  if (viewKind === "image" && file.url && !imageFailed) {
    return <WbcInlineImageAttachment file={file} onOpenFile={onOpenFile} setImageFailed={setImageFailed} />;
  }
  if ((viewKind === "video" || viewKind === "audio") && file.url) {
    return <WbcInlineMediaAttachment file={file} onOpenFile={onOpenFile} viewKind={viewKind} />;
  }
  return <WbcGenericAttachment file={file} onOpenFile={onOpenFile} />;
}

function WbcInlineAttachmentActions({ file }) {
  return (
    <span className="wbc-inline-image-actions">
      {wbcCanOpenExternally(file) ? (
        <a
          className="wbc-inline-image-action"
          href={file.url}
          target="_blank"
          rel="noreferrer"
          draggable="false"
          title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}
          aria-label={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}
        >{WBC_ICONS.openExternal}</a>
      ) : null}
      {wbcDownloadLink(file, {
        className: "wbc-inline-image-action",
        draggable: "false",
        "aria-label": wbcT("workbenchChat.download", "Download"),
      })}
    </span>
  );
}

function WbcInlineImageAttachment({ file, onOpenFile, setImageFailed }) {
  var open = function () { if (onOpenFile && file.url) onOpenFile(file); };
  return (
    <div
      className="wbc-inline-image"
      draggable="true"
      onDragStart={function (event) { wbcStartFileDrag(event, file); }}
    >
      <button
        type="button"
        className="wbc-inline-image-preview"
        onClick={open}
        title={wbcT("workbenchChat.viewInSide", "View on the right")}
      >
        <img
          src={file.url}
          alt={file.name || wbcT("workbenchChat.attachmentType.image", "Image")}
          draggable="false"
          onError={function () { setImageFailed(true); }}
        />
      </button>
      <div className="wbc-inline-image-footer">
        <b title={file.name}>{file.name || "image"}</b>
        <WbcInlineAttachmentActions file={file} />
      </div>
    </div>
  );
}

function WbcInlineMediaAttachment({ file, onOpenFile, viewKind }) {
  var open = function () { if (onOpenFile && file.url) onOpenFile(file); };
  return (
    <div
      className={"wbc-inline-media " + viewKind}
      draggable="true"
      onDragStart={function (event) { wbcStartFileDrag(event, file); }}
    >
      {viewKind === "video"
        ? <video src={file.url} controls preload="metadata" playsInline draggable="false" />
        : <audio src={file.url} controls preload="metadata" draggable="false" />}
      <div className="wbc-inline-media-footer">
        <button type="button" onClick={open} title={wbcT("workbenchChat.viewInSide", "View on the right")}>
          {file.name || viewKind}
        </button>
        <WbcInlineAttachmentActions file={file} />
      </div>
    </div>
  );
}

function WbcGenericAttachment({ file, onOpenFile }) {
  var open = function () { if (onOpenFile && file.url) onOpenFile(file); };
  var canOpen = !!(onOpenFile && file.url);
  function startFileDrag(event) {
    wbcStartFileDrag(event, file);
  }
  var content = (
    <>
      <WbcFileVisual file={file} />
      <span className="wbc-attach-file-meta">
        <b title={file.name}>{file.name || "file"}</b>
        <small>{wbcAttachmentTypeLabel(file)}</small>
      </span>
      {canOpen ? (
        <span className="wbc-attach-file-open">
          <span>{wbcT("workbenchChat.openPreview", "Open preview")}</span>
          {WBC_ICONS.chevronsRight}
        </span>
      ) : null}
    </>
  );
  if (!canOpen) return <div className="wbc-attach-file" draggable="true" onDragStart={startFileDrag}>{content}</div>;
  return (
    <button type="button" className="wbc-attach-file" draggable="true" onDragStart={startFileDrag} onClick={open} title={wbcT("workbenchChat.viewInSide", "View on the right")}>
      {content}
    </button>
  );
}

function WbcUserMessage({ msg, onOpenFile, onEditMessage, canEdit, onRetryMessage }) {
  var attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
  var hasInlineImage = attachments.some(function (file) {
    return file && file.url && (file.kind === "image" || String(file.content_type || "").indexOf("image") === 0);
  });
  var bubbleClassName = "wbc-bubble" + (hasInlineImage ? " with-inline-image" : "");
  var [editing, setEditing] = useWbcState(false);
  var [draft, setDraft] = useWbcState(String(msg.content || ""));
  var taRef = useWbcRef(null);

  useWbcEffect(function () {
    if (editing && taRef.current) {
      taRef.current.style.height = "auto";
      taRef.current.style.height = Math.min(taRef.current.scrollHeight, 240) + "px";
      taRef.current.focus();
      taRef.current.setSelectionRange(taRef.current.value.length, taRef.current.value.length);
    }
  }, [editing]);

  function startEdit(e) {
    e.stopPropagation();
    setDraft(String(msg.content || ""));
    setEditing(true);
  }
  function cancelEdit() {
    setEditing(false);
    setDraft(String(msg.content || ""));
  }
  function saveEdit() {
    var text = String(draft || "").trim();
    if (!text || !onEditMessage) { setEditing(false); return; }
    if (text === String(msg.content || "").trim()) { setEditing(false); return; }
    setEditing(false);
    onEditMessage(msg.id, text);
  }
  function onEditKeyDown(event) {
    var sc = workbenchServices.shortcuts();
    if (sc && sc.matches(event, "composer-send")) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return;
      event.preventDefault();
      saveEdit();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancelEdit();
    }
  }

  if (editing) {
    return (
      <div className="wbc-msg user editing">
        <div className={bubbleClassName + " wbc-edit-bubble"}>
          <textarea
            ref={taRef}
            className="wbc-edit-textarea"
            value={draft}
            onChange={function (e) { setDraft(e.target.value); }}
            onKeyDown={onEditKeyDown}
            placeholder={wbcT("workbenchChat.editPlaceholder", "Edit your message...")}
          />
          {attachments.length > 0 && (
            <div className={"wbc-msg-attachments" + (draft.trim() ? " after-copy" : "")}>
              {attachments.map(function (file, i) {
                return <WbcMessageAttachment key={file.id || file.url || i} file={file} onOpenFile={onOpenFile} />;
              })}
            </div>
          )}
          <div className="wbc-edit-actions">
            <button type="button" className="wb-btn ghost" onClick={cancelEdit}>{wbcT("common.cancel", "Cancel")}</button>
            <button type="button" className="wb-btn primary" onClick={saveEdit} disabled={!draft.trim()}>{wbcT("workbenchChat.editSave", "Save & send")}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="wbc-msg user">
      <div className="wbc-msg-row">
        <time>{wbcFormatTime(msg.createdAt)}</time>
        <div className={bubbleClassName}>
          {msg.content ? <p>{msg.content}</p> : null}
          {attachments.length > 0 && (
            <div className={"wbc-msg-attachments" + (msg.content ? " after-copy" : "")}>
              {attachments.map(function (file, i) {
                return <WbcMessageAttachment key={file.id || file.url || i} file={file} onOpenFile={onOpenFile} />;
              })}
            </div>
          )}
        </div>
      </div>
      {((canEdit && onEditMessage) || onRetryMessage) && (
        <div className="wbc-msg-foot wbc-user-foot">
          {canEdit && onEditMessage && (
            <button type="button" className="wbc-msg-action wbc-edit-btn" onClick={startEdit} title={wbcT("workbenchChat.editMessage", "Edit & branch")}>
              {WBC_ICONS.edit}
            </button>
          )}
          {onRetryMessage && (
            <button type="button" data-tour="chat_retry" className="wbc-msg-action" onClick={function () { onRetryMessage(msg.id); }} title={wbcT("workbenchChat.retryUserMessage", "Retry message")}>
              {WBC_ICONS.retry}
            </button>
          )}
        </div>
      )}
    </div>
  );
}


function WbcModelStatusMessage({ msg }) {
  var status = msg && msg.modelStatus && typeof msg.modelStatus === "object"
    ? msg.modelStatus
    : {};
  var model = String(status.model || "").trim();
  if (!model) return null;
  var statusName = String(status.status || "");
  var switched = statusName === "switched";
  var switching = statusName === "switching";
  var recovered = statusName === "recovered";
  var failed = statusName === "failed";
  var retryCount = Math.max(0, Number(status.retryCount || 0));
  var retryLimit = Math.max(0, Number(status.retryLimit || 0));
  var text = switching
    ? wbcT("workbenchChat.modelSwitchingCard", "Switching to {model}", { model: model })
    : switched
      ? wbcT("workbenchChat.modelSwitchedCard", "Switched to {model}", { model: model })
      : recovered
        ? wbcT("workbenchChat.modelRecoveredCard", "Connection restored for {model}", { model: model })
        : failed
          ? wbcT("workbenchChat.modelFailedCard", "Model call failed: {model}", { model: model })
          : (retryCount > 0 && retryLimit > 0
      ? wbcT("workbenchChat.modelRetryCountCard", "Retrying {model} ({count}/{limit})", {
          model: model,
          count: retryCount,
          limit: retryLimit,
        })
      : wbcT("workbenchChat.modelRetryCard", "Retrying {model}", { model: model }));
  var stateClass = failed ? "is-failed"
    : (switched || recovered ? "is-complete" : "is-pending");
  return (
    <div className={"wbc-model-status-message " + stateClass} role="status" aria-live="polite">
      <div className="wbc-model-status-card">
        <span className="wbc-model-status-copy">{text}</span>
      </div>
    </div>
  );
}


// Files the agent produced in this reply — rendered like the reference's
// artifact card, with a 查看 action that opens the side viewer.
function WbcAgentFiles({ files, onOpenFile }) {
  if (!files || !files.length) return null;
  return (
    <div className="wbc-agent-files">
      {files.map(function (file, i) {
        if (["image", "video", "audio"].indexOf(wbcFileViewKind(file)) !== -1 && file.url) {
          return <WbcMessageAttachment key={file.id || file.url || i} file={file} onOpenFile={onOpenFile} />;
        }
        return (
          <div
            className="wbc-agent-file"
            key={file.id || file.url || i}
            draggable={wbcUsesFilePointerDrag() ? undefined : "true"}
            onDragStart={function (event) { wbcStartFileDrag(event, file); }}
            onPointerDown={function (event) { wbcStartFilePointerDrag(event, file); }}
          >
            <span className="wbc-file-icon">{WBC_ICONS.file}</span>
            <span className="wbc-file-meta">
              <b title={file.name}>{file.name || "file"}</b>
              <small>{file.content_type || ""}</small>
            </span>
            <span className="wbc-agent-file-actions">
              <button type="button" className="wb-btn ghost" onClick={function () { onOpenFile && onOpenFile(file); }}>{wbcT("workbenchChat.viewer", "Viewer")}</button>
              {wbcCanOpenExternally(file) ? <a className="wb-btn ghost" href={file.url} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>↗</a> : null}
              {wbcDownloadLink(file)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function WbcMediaReferences({ files, onOpenFile }) {
  var list = Array.isArray(files) ? files : [];
  if (!list.length) return null;
  return (
    <div className="wbc-media-references">
      <div className="wbc-media-references-label">
        {wbcT("workbenchChat.mediaReferences", "References")}
      </div>
      <div className="wbc-media-references-list">
        {list.map(function (file, index) {
          return <WbcMessageAttachment key={file.id || file.url || index} file={file} onOpenFile={onOpenFile} />;
        })}
      </div>
    </div>
  );
}

function WbcLiveAgentArtifacts({ files, onOpenFile }) {
  var list = Array.isArray(files) ? files : [];
  if (!list.length) return null;
  return (
    <div className="wbc-live-agent-artifacts" aria-live="polite">
      <div className="wbc-live-agent-artifacts-head">
        <span className="wbc-live-agent-artifacts-pulse" aria-hidden="true" />
        <b>{wbcT("workbenchChat.liveArtifacts", "Agent output")}</b>
      </div>
      <WbcAgentFiles files={list} onOpenFile={onOpenFile} />
    </div>
  );
}

var WBC_ACTIVITY_GROUP_MIN_ITEMS = 3;

function wbcIsActivityMessage(message) {
  if (!message || typeof message !== "object") return false;
  if (message.runtimeActivity || message.activityCard) return true;
  if (String(message.content || "").trim()) return false;
  var reasoning = String(message.reasoning || "").trim();
  var trace = Array.isArray(message.trace) ? message.trace : [];
  if (reasoning || trace.length > 0) return true;
  var id = String(message.id || "");
  return !!message.intermediate && /^(reasoning|activity)_msg_/.test(id);
}

function wbcActivityMessageView(message) {
  if (!wbcIsActivityMessage(message)) return null;
  var activity = message.runtimeActivity || {
    id: message.id,
    reasoning: message.reasoning || "",
    reasoningActive: !!message.reasoningActive,
    progress: Array.isArray(message.trace) ? message.trace : [],
  };
  var entries = Array.isArray(activity.progress) ? activity.progress : [];
  var live = !!message.runtimeActivity || message.timelineVersion === 1 && message.status === "running";
  var active = !!message.runtimeActivityActive || (message.timelineVersion === 1 && message.status === "running") || (live && entries.some(function (entry) {
    return String(entry && entry.status || "").trim().toLowerCase() === "running";
  }));
  var hasReasoning = !!String(activity.reasoning || "").trim();
  return {
    activity: activity,
    entries: entries,
    live: live,
    active: active,
    hasReplyText: !!message.runtimeActivityHasReplyText,
    // Visibility and liveness are separate: historical reasoning remains
    // readable, while only live entries are allowed to animate.
    visible: message.timelineVersion === 1 || active || entries.length > 0 || hasReasoning,
  };
}

function wbcActivityGroupDurationMs(items, nextMessage, runtime) {
  var list = Array.isArray(items) ? items : [];
  var starts = list.map(function (message) {
    return Date.parse(String(message.startedAt || message.createdAt || message.created_at || ""));
  }).filter(Number.isFinite);
  var ends = list.map(function (message) {
    return Date.parse(String(message.endedAt || message.createdAt || message.created_at || ""));
  }).filter(Number.isFinite);
  if (!starts.length || !ends.length) return null;
  return Math.max(0, Math.max.apply(Math, ends) - Math.min.apply(Math, starts));
}

function wbcGroupConsecutiveActivityMessages(messages, runtime) {
  var source = Array.isArray(messages) ? messages : [];
  var grouped = [];
  var pending = [];

  function flush(nextMessage) {
    if (!pending.length) return;
    if (pending.length < WBC_ACTIVITY_GROUP_MIN_ITEMS) {
      grouped.push.apply(grouped, pending);
      pending = [];
      return;
    }
    var active = pending.some(function (message) {
      var view = wbcActivityMessageView(message);
      return !!(view && view.active);
    });
    grouped.push({
      id: "activity-group:" + String(pending[0].id || "first"),
      role: "assistant",
      activityGroup: true,
      activities: pending,
      active: active,
      durationMs: active ? null : wbcActivityGroupDurationMs(pending, nextMessage, runtime),
    });
    pending = [];
  }

  source.forEach(function (message) {
    if (message.timelineVersion === 1 && !message.activityCard && message.role !== "user"
      && !message.notificationCard && !message.modelStatusCard && !String(message.content || "").trim()
      && !(message.attachments && message.attachments.length)
      && !(message.referenceAttachments && message.referenceAttachments.length)) return;
    var view = wbcActivityMessageView(message);
    if (view) {
      // Tool-free completed placeholders were already visually omitted by the
      // old renderer. Keep omitting them without breaking an otherwise
      // consecutive activity run.
      if (view.visible) pending.push(message);
      return;
    }
    flush(message);
    grouped.push(message);
  });
  flush(null);
  return grouped;
}

// Read-only tool names (list/query/get/find/search/check/read/analyze/snapshot)
// — used to distinguish "read the database" from "updated the database" in labels.
function wbcTraceNormalizeName(raw) {
  return String(raw || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function wbcTraceNameIsRead(raw) {
  var name = wbcTraceNormalizeName(raw);
  return /(^|_)(list|query|get|find|search|check|read|analyze|snapshot)($|_)/.test(name);
}

function wbcTraceActionKind(entry) {
  var raw = String(entry && (entry.text || entry.tool) || "").trim();
  var name = wbcTraceNormalizeName(raw);
  var entryKind = String(entry && entry.kind || "").trim().toLowerCase();
  if (entryKind === "phase1") return "phase1";
  if (entryKind === "phase") return "phase";
  if (entryKind === "subagent") return "subagent";
  if (entryKind === "permission") return "permission";
  if (entryKind === "event") return "event";
  if (entryKind === "steering") return "steering";
  if (/(^|_)(edit|apply_patch|replace|patch)($|_)/.test(name)) return "edit";
  if (/(^|_)(write|create_file|save_file)($|_)/.test(name)) return "write";
  if (/(^|_)(read|read_file|open_file)($|_)/.test(name)) return "read";
  if (/(approve|reject|permission)/.test(name)) return "permission";
  if (/(^|_)(bash|shell|terminal|exec|exec_command|run_command|script)($|_)/.test(name) || /(runscript|startshell|sendshell|listshells|readshell|interruptshell|showshell|deleteshell)/.test(name)) return "command";
  if (/(skill|capability|ability)/.test(name)) return "skill";
  if (/(search|grep|glob|find|query)/.test(name)) return "search";
  if (/(browser|navigate|click|screenshot)/.test(name)) return "browser";
  if (/(git|branch|commit)/.test(name)) return "git";
  if (/(code|symbol|reference|lint|format|index)/.test(name)) return "code";
  if (/(plan|goal|schedule)/.test(name)) return "planning";
  if (/(memor|recall|learnpattern)/.test(name)) return "memory";
  if (/(knowledge|library)/.test(name)) return "knowledge";
  if (/(^|_)(entit)/.test(name)) return "entity";
  if (/(^|_)(pin|map)/.test(name)) return "map";
  if (/(remote|device)/.test(name)) return "remote";
  if (/(desktop|app_ui|appui|cyrene_ui|cyreneui|window|app_use)/.test(name)) return "desktop";
  if (/(attachment|image|artifact|media|send_file)/.test(name)) return "artifact";
  if (/(notification|telegram|wechat|delivery|send_message|broadcast)/.test(name)) return "delivery";
  if (/(settings|lifecycle|control|integration|extension|hook|environment)/.test(name)) return "system";
  return "tool";
}

function wbcTraceActionLabel(entry) {
  var raw = String(entry && (entry.text || entry.tool) || "").trim();
  var normalized = wbcTraceNormalizeName(raw);
  if (normalized === "toolbox_list") return wbcT("workbenchChat.traceAction.listedTools", "Listed available tools");
  if (normalized === "toolbox_describe") return wbcT("workbenchChat.traceAction.inspectedTool", "Inspected tool details");
  if (normalized === "toolbox_invoke") return wbcT("workbenchChat.traceAction.invokedTool", "Invoked a tool");
  var kind = wbcTraceActionKind(entry);
  if (kind === "edit") return wbcT("workbenchChat.traceAction.edited", "Edited files");
  if (kind === "write") return wbcT("workbenchChat.traceAction.wrote", "Wrote files");
  if (kind === "read") return wbcT("workbenchChat.traceAction.read", "Read files");
  if (kind === "command") return wbcT("workbenchChat.traceAction.command", "Ran commands");
  if (kind === "skill") return wbcT("workbenchChat.traceAction.usedSkill", "Used skill tools");
  if (kind === "search") return wbcT("workbenchChat.traceAction.searched", "Searched");
  if (kind === "browser") return wbcT("workbenchChat.traceAction.browsed", "Used the browser");
  if (kind === "git") return wbcT("workbenchChat.traceAction.git", "Used Git");
  if (kind === "code") {
    if (wbcTraceNameIsRead(raw)) return wbcT("workbenchChat.traceAction.codeRead", "Read terminal");
    return wbcT("workbenchChat.traceAction.code", "Operated terminal");
  }
  if (kind === "planning") {
    if (wbcTraceNameIsRead(raw)) return wbcT("workbenchChat.traceAction.planningRead", "Read plans or schedules");
    return wbcT("workbenchChat.traceAction.planning", "Updated plans or schedules");
  }
  if (kind === "memory") return wbcT("workbenchChat.traceAction.memory", "Used memory");
  if (kind === "knowledge") return wbcT("workbenchChat.traceAction.knowledge", "Used knowledge");
  if (kind === "entity") {
    if (wbcTraceNameIsRead(raw)) return wbcT("workbenchChat.traceAction.entityRead", "Read the user database");
    return wbcT("workbenchChat.traceAction.entity", "Updated the user database");
  }
  if (kind === "map") return wbcT("workbenchChat.traceAction.map", "Updated maps");
  if (kind === "remote") return wbcT("workbenchChat.traceAction.remote", "Operated remote devices");
  if (kind === "desktop") return wbcT("workbenchChat.traceAction.desktop", "Operated the desktop app");
  if (kind === "artifact") {
    if (wbcTraceNameIsRead(raw)) return wbcT("workbenchChat.traceAction.artifactRead", "Analyzed media");
    return wbcT("workbenchChat.traceAction.artifact", "Created or handled media");
  }
  if (kind === "delivery") return wbcT("workbenchChat.traceAction.delivery", "Sent messages or notifications");
  if (kind === "system") {
    if (wbcTraceNameIsRead(raw)) return wbcT("workbenchChat.traceAction.systemRead", "Inspected system settings");
    return wbcT("workbenchChat.traceAction.system", "Updated system settings");
  }
  if (kind === "phase1") return String(entry && entry.text || wbcT("workbenchChat.phase1Understood", "Understood the request"));
  if (kind === "phase") return wbcT("workbenchChat.traceAction.phase", "Advanced the execution phase");
  if (kind === "subagent") return wbcT("workbenchChat.traceAction.subagent", "Coordinated subagents");
  if (kind === "permission") return wbcT("workbenchChat.traceAction.permission", "Reviewed permissions");
  if (kind === "event") return wbcT("workbenchChat.traceAction.event", "Handled an agent event");
  if (kind === "steering") return wbcT("workbenchChat.traceAction.steering", "Steering");
  var toolName = wbcLocalizedToolName(raw);
  return wbcT("workbenchChat.traceAction.usedTool", "Used {tool}", { tool: toolName });
}

function wbcTraceActionIcon(entry) {
  var kind = wbcTraceActionKind(entry);
  if (kind === "edit") return WBC_ICONS.edit;
  if (kind === "write") return WBC_ICONS.file;
  if (kind === "read") return WBC_ICONS.fileText;
  if (kind === "command") return WBC_ICONS.slash;
  if (kind === "skill") return WBC_ICONS.spark;
  if (kind === "search") return WBC_ICONS.search;
  if (kind === "browser") return WBC_ICONS.browser;
  if (kind === "git") return WBC_ICONS.fork;
  if (kind === "code") return WBC_ICONS.code;
  if (kind === "planning") return WBC_ICONS.checklist;
  if (kind === "memory") return WBC_ICONS.database;
  if (kind === "knowledge") return WBC_ICONS.book;
  if (kind === "entity") return WBC_ICONS.pin;
  if (kind === "map") return WBC_ICONS.map;
  if (kind === "remote") return WBC_ICONS.device;
  if (kind === "desktop") return WBC_ICONS.windowRestore;
  if (kind === "artifact") return WBC_ICONS.image;
  if (kind === "delivery") return WBC_ICONS.chat;
  if (kind === "system") return WBC_ICONS.bolt;
  if (kind === "phase1") return WBC_ICONS.layers;
  if (kind === "phase") return WBC_ICONS.phase;
  if (kind === "subagent") return WBC_ICONS.subagent;
  if (kind === "permission") return WBC_ICONS.permission;
  if (kind === "event") return WBC_ICONS.eventPulse;
  return WBC_ICONS.tool;
}

function wbcIsToolTraceEntry(entry) {
  return !!(entry && (entry.kind === "tool" || entry.tool));
}

function wbcActivityGroupRunningSummary(messages) {
  var steps = [];
  var thinkingCount = 0;
  (messages || []).forEach(function (message) {
    var view = wbcActivityMessageView(message);
    if (!view || !view.active) return;
    view.entries.forEach(function (entry) {
      if (entry.status === "running") steps.push({
        label: wbcLocalizedToolName(entry.text || "tool"),
        icon: wbcTraceActionIcon(entry),
      });
    });
    if (view.activity.reasoningActive) thinkingCount += 1;
  });
  if (!steps.length) return {
    label: wbcT("workbenchChat.activityGroup.running.thinking", "Thinking"),
    icon: WBC_ICONS.brain,
  };
  var count = steps.length + thinkingCount;
  return {
    label: count > 1
      ? wbcT("workbenchChat.activityGroup.steps", "{step} ({count} active steps)", { step: steps[0].label, count: count })
      : steps[0].label,
    icon: steps[0].icon,
  };
}

function wbcTraceCollapsedSummary(entries, fallback) {
  var traceEntries = Array.isArray(entries) ? entries : [];
  var toolEntries = traceEntries.filter(wbcIsToolTraceEntry);
  if (toolEntries.length) return {
    label: wbcT("workbenchChat.traceSummary", "Executed {count} tool calls", { count: toolEntries.length }),
    icon: toolEntries.length === 1 ? wbcTraceActionIcon(toolEntries[0]) : WBC_ICONS.layers,
  };
  var phase1Entry = traceEntries.find(function (entry) {
    return entry && entry.kind === "phase1";
  });
  if (phase1Entry) return {
    label: String(phase1Entry.text || fallback || wbcT("workbenchChat.phase1Understood", "Understood the request")),
    icon: WBC_ICONS.layers,
  };
  var actions = [];
  var seen = new Set();
  traceEntries.forEach(function (entry) {
    var label = wbcTraceActionLabel(entry);
    var key = wbcTraceActionKind(entry) + "\n" + label;
    if (!label || seen.has(key)) return;
    seen.add(key);
    actions.push({ entry: entry, label: label });
  });
  if (!actions.length) return {
    label: fallback || wbcT("workbenchChat.traceLabel", "Execution"),
    icon: WBC_ICONS.brain,
  };
  var labels = actions.map(function (action) { return action.label; });
  var summaryLabel = labels[0];
  if (labels.length > 1) {
    summaryLabel = labels.slice(0, -1).join(wbcT("workbenchChat.traceAction.listSeparator", ", "))
      + wbcT("workbenchChat.traceAction.conjunction", " and ")
      + labels[labels.length - 1];
  }
  return {
    label: summaryLabel,
    icon: actions.length === 1 ? wbcTraceActionIcon(actions[0].entry) : WBC_ICONS.layers,
  };
}

function wbcNormalizeReasoningText(text) {
  return String(text || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map(function (line) { return line.trimEnd(); })
    .filter(function (line) { return !!line.trim(); })
    .join("\n")
    .trim();
}

function wbcTraceTimelineItems(entries, reasoning) {
  var traceEntries = Array.isArray(entries) ? entries : [];
  var rawReasoning = String(reasoning || "");
  var anchored = [];
  var trailing = [];
  traceEntries.forEach(function (entry, index) {
    // The phase-one row only summarizes the reasoning text and repeats that
    // same content in its preview. Keep it for the collapsed status, but omit
    // it from the expanded timeline where the full reasoning is already shown.
    if (entry && entry.kind === "phase1") return;
    var offset = Number(entry && entry.reasoningOffset);
    if (Number.isFinite(offset) && offset >= 0) anchored.push({ entry: entry, index: index, offset: offset });
    else trailing.push({ entry: entry, index: index });
  });
  anchored.sort(function (left, right) {
    return left.offset === right.offset ? left.index - right.index : left.offset - right.offset;
  });
  var items = [];
  var cursor = 0;
  anchored.forEach(function (anchor) {
    var offset = Math.max(cursor, Math.min(rawReasoning.length, anchor.offset));
    var reasoningText = wbcNormalizeReasoningText(rawReasoning.slice(cursor, offset));
    if (reasoningText) items.push({ kind: "reasoning", text: reasoningText });
    items.push({ kind: "trace", entry: anchor.entry, index: anchor.index });
    cursor = offset;
  });
  var remainingReasoning = wbcNormalizeReasoningText(rawReasoning.slice(cursor));
  if (remainingReasoning) items.push({ kind: "reasoning", text: remainingReasoning });
  trailing.forEach(function (item) {
    items.push({ kind: "trace", entry: item.entry, index: item.index });
  });
  return items;
}

function wbcTraceDedupeKeyForDisclosure(trace) {
  return (trace || []).map(function (entry) { return entry.toolCallId || entry.text; }).join(":");
}

function WbcTraceCard({ trace, live, running, label, reasoning, disclosureId }) {
  useWorkbenchI18n(); var entries = Array.isArray(trace) ? trace : [];
  var [expanded, setExpanded] = wbcUseDisclosure(disclosureId || "trace:" + wbcTraceDedupeKeyForDisclosure(trace));
  // Closed history cards can contain large tool outputs. Mount details on
  // first disclosure, then retain them so closing still animates normally.
  var detailsMountedRef = useWbcRef(expanded);
  if (expanded) detailsMountedRef.current = true;
  var reasoningText = String(reasoning || "");
  if (!entries.length && !reasoningText.trim() && !live && !disclosureId) return null;
  var activityRunning = live && running !== false;
  var hasDetails = entries.length > 0 || !!reasoningText.trim();
  var cardClass = "wbc-trace" + (live ? " live" : "") + (expanded ? " expanded" : "");
  var collapsedSummary = wbcTraceCollapsedSummary(entries, label);
  // Tool calls may keep running in an earlier activity while a newer activity
  // is appended (for example, a permission event arriving during Bash). The
  // entry lifecycle is authoritative for its spinner; `activityRunning` only
  // owns the empty, latest-activity placeholder. Persisted cards pass
  // `live=false`, so a stale saved status can never animate forever.
  var hasRunningEntries = live && entries.some(function (entry) {
    return String(entry && entry.status || "").trim().toLowerCase() === "running";
  });
  var summaryRunning = hasRunningEntries || activityRunning;
  var timelineItems = detailsMountedRef.current ? wbcTraceTimelineItems(entries, reasoningText) : [];
  return (
    <div className={cardClass} aria-busy={summaryRunning ? "true" : undefined}>
      <button
        type="button"
        className="wbc-trace-summary"
        onClick={function (event) {
          if (!hasDetails) return;
          var nextExpanded = !expanded;
          var thread = event.currentTarget.closest(".wbc-thread");
          if (thread) {
            thread.dispatchEvent(new CustomEvent("workbench:trace-disclosure", {
              detail: { anchor: event.currentTarget, expanding: nextExpanded },
            }));
          }
          setExpanded(nextExpanded);
        }}
        aria-expanded={hasDetails ? expanded : undefined}
        disabled={!hasDetails}
      >
        <span className="wbc-trace-summary-items">
          <span className="wbc-trace-summary-item">
            <span className="wbc-trace-summary-icon" aria-hidden="true">{collapsedSummary.icon}</span>
            <b>{collapsedSummary.label}</b>
            {summaryRunning ? <span className="wb-spinner small" aria-hidden="true" /> : null}
            {hasDetails ? <span className="wbc-trace-summary-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span> : null}
          </span>
        </span>
      </button>
      <div className={"wbc-trace-collapse" + (expanded ? " open" : "")} aria-hidden={!expanded}>
        <div className="wbc-trace-collapse-inner">
          <div className="wbc-trace-details">
            <div className="wbc-trace-view">
          {timelineItems.length > 0 && (
            <ul className="wbc-trace-list wbc-trace-timeline" aria-live="polite">
              {timelineItems.map(function (item, timelineIndex) {
                if (item.kind === "reasoning") return (
                  <li className="wbc-thinking-detail wbc-trace-timeline-reasoning" key={"reasoning:" + timelineIndex}>
                    <span className="wbc-trace-entry-icon" aria-hidden="true">{WBC_ICONS.brain}</span>
                    <span className="wbc-thinking-detail-text">{item.text}</span>
                  </li>
                );
                var entry = item.entry || {};
                var i = item.index;
                var entryStatus = String(entry.status || "").trim().toLowerCase();
                var isRunning = live && entryStatus === "running";
                var failed = !!entry.failed || ["failed", "error", "failure", "expired", "cancelled"].indexOf(entryStatus) >= 0;
                var presentationKind = wbcToolPresentationKind(entry);
                var presentationText = wbcToolPresentationText(entry, presentationKind);
                var previewText = wbcToolPreviewText(entry.preview);
                return (
                  <li key={(entry.toolCallId || "trace") + ":" + i} className={(failed ? "failed" : (isRunning ? "active" : "done")) + " presentation-" + presentationKind}>
                    <span className="wbc-trace-mark">{failed ? WBC_ICONS.x : (isRunning ? wbcTraceActionIcon(entry) : WBC_ICONS.check)}</span>
                    {failed ? <span className="wbc-trace-entry-icon" aria-hidden="true">{wbcTraceActionIcon(entry)}</span> : null}
                    <span className="wbc-trace-text">
                      <span className="wbc-trace-label">
                        {(function () {
                          var toolKey = entry.text || entry.tool || "";
                          var isToolEntry = entry.kind === "tool" || !!entry.tool;
                          if (isToolEntry) return wbcLocalizedToolName(toolKey);
                          if (entry.detailKey) return wbcT(entry.detailKey, toolKey, entry.detailParams);
                          return toolKey;
                        })()}
                      </span>
                      {previewText ? <small>{wbcParenthesize(previewText)}</small> : null}
                      {presentationKind !== "generic" ? <em className="wbc-tool-presentation-kind">{wbcT("workbenchChat.toolPresentation." + presentationKind, presentationKind)}</em> : null}
                      {isRunning && Number(entry.progressTotal) > 0 ? (
                        <span className="wbc-transfer-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(Number(entry.progress || 0) * 100)}>
                          <span style={{ width: Math.round(Number(entry.progress || 0) * 100) + "%" }} />
                          <small>{Math.round(Number(entry.progress || 0) * 100)}%</small>
                        </span>
                      ) : null}
                      {presentationText ? <pre className={"wbc-tool-presentation-body " + presentationKind} role={presentationKind === "error" ? "alert" : undefined}>{presentationText}</pre> : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function WbcContinuationIndicator() {
  useWorkbenchI18n();
  return (
    <WbcTraceCard
      trace={[]}
      live={true}
      running={true}
      label={wbcT("workbenchChat.continueProcessing", "Continuing to process")}
      reasoning=""
    />
  );
}

function wbcAssistantUsageTokenCount(usage) {
  if (!usage || typeof usage !== "object") return null;
  function tokenValue(field) {
    var value = usage[field];
    return typeof value === "number" && Number.isFinite(value) && value >= 0
      ? value
      : null;
  }
  var total = tokenValue("total_tokens");
  if (total !== null) return Math.round(total);
  var prompt = tokenValue("prompt_tokens");
  var completion = tokenValue("completion_tokens");
  if (prompt !== null && completion !== null) return Math.round(prompt + completion);
  if (completion !== null) return Math.round(completion);
  var output = tokenValue("output_tokens");
  if (output !== null) return Math.round(output);
  return null;
}

function wbcFormatOutputTokenSpeed(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "";
  if (value < 0.05) return "<0.1 tok/s";
  return String(Math.round(value * 10) / 10) + " tok/s";
}

function WbcAssistantMessageFooter({ copied, msg, onCopy, onRetryMessage, voiceSnapshot }) {
  var BrowserIcon = workbenchServices.browser().Icon;
  var messageVoiceKey = "message:" + String(msg && msg.id || "");
  var messageSpeaking = voiceSnapshot.activeKey === messageVoiceKey;
  var processingDuration = wbcFormatProcessingDuration(msg.processingDurationMs);
  var usageTokenCount = wbcAssistantUsageTokenCount(msg && msg.usage);
  var outputTokenSpeed = wbcFormatOutputTokenSpeed(msg && msg.outputTokensPerSecond);
  return <div className="wbc-msg-foot">
    {voiceSnapshot.status.tts_ready && (
      <button
        type="button"
        className={"wbc-msg-action" + (messageSpeaking ? " is-speaking" : "")}
        onClick={function () { WbcVoice.speak(msg.content, messageVoiceKey); }}
        title={messageSpeaking
          ? wbcT("workbenchChat.voicePlaybackStop", "Stop reading")
          : wbcT("workbenchChat.voicePlayback", "Read aloud")}
        aria-label={messageSpeaking
          ? wbcT("workbenchChat.voicePlaybackStop", "Stop reading")
          : wbcT("workbenchChat.voicePlayback", "Read aloud")}
      >
        {BrowserIcon ? <BrowserIcon name={messageSpeaking ? "muted" : "volume"} size={14} /> : null}
      </button>
    )}
    <button type="button" className="wbc-msg-action" onClick={onCopy} title={wbcT("workbenchChat.copy", "Copy")}>
      {copied ? WBC_ICONS.check : WBC_ICONS.copy}
    </button>
    {onRetryMessage && (
      <button type="button" data-tour="chat_retry" className="wbc-msg-action" onClick={function () { onRetryMessage(msg.id); }} title={wbcT("workbenchChat.regenerate", "Regenerate")}>
        {WBC_ICONS.retry}
      </button>
    )}
    <time>{wbcFormatTime(msg.createdAt)}</time>
    {processingDuration ? <small className="wbc-msg-duration" title={wbcT("workbenchChat.processingDuration", "Total processing time")}>{processingDuration}</small> : null}
    {usageTokenCount !== null ? <small className="wbc-msg-token-usage">{wbcCompactNumber(usageTokenCount)} tokens</small> : null}
    {outputTokenSpeed ? <small className="wbc-msg-output-speed">{outputTokenSpeed}</small> : null}
  </div>;
}

var WbcStreamingStableBlock = React.memo(function WbcStreamingStableBlock({ source }) {
  var html = useWbcMemo(function () {
    return wbcRenderMarkdown(source, { interactive: false });
  }, [source]);
  return html
    ? <div className={"wbc-stream-stable-block" + (/^\s*<p(?:\s|>)/.test(html) ? " paragraph" : "")} dangerouslySetInnerHTML={{ __html: html }} />
    : null;
});

function WbcGoalMilestoneMessage({ msg, chatId }) {
  var milestone = msg.goalMilestone && typeof msg.goalMilestone === "object"
    ? msg.goalMilestone : {};
  var terminalGoal = milestone.status === "completed" || milestone.status === "aborted";
  return (
    <article className={"wbc-goal-milestone " + String(milestone.type || "update")}>
      <span className="wbc-goal-milestone-mark" aria-hidden="true">{WBC_ICONS.phase}</span>
      <div className="wbc-goal-milestone-body">
        <header>
          <small>{wbcT("goal.milestone", "Goal milestone")}</small>
          {Number(milestone.attempt || 0) > 0 ? <span className="wbc-goal-milestone-attempt">{wbcT("goal.attempt", "Attempt {attempt}", { attempt: Number(milestone.attempt) })}</span> : null}
        </header>
        <div className="wbc-goal-milestone-copy">{msg.content}</div>
      </div>
      {!terminalGoal ? <button type="button" className="wbc-goal-milestone-action" onClick={function () {
        window.dispatchEvent(new CustomEvent("workbench:open-goal-tab", { detail: { chatId: String(chatId || "") } }));
      }}><span>{wbcT("goal.openTab", "Open Goal")}</span>{WBC_ICONS.chevronRight}</button> : null}
    </article>
  );
}

function WbcAssistantMessage({ msg, liveRuntime, onOpenFile, onRetryMessage, chatId }) {
  msg = msg || {};
  if (!liveRuntime && msg.timelineVersion === 1 && !msg.activityCard && msg.status === "running") {
    liveRuntime = { text: msg.content, artifacts: msg.attachments || [], streamDone: false };
  }
  var live = !!liveRuntime;
  var sourceText = live ? String(liveRuntime.text || "") : String(msg.content || "");
  var renderedText = wbcUseBufferedLiveText(
    sourceText,
    !live || !!liveRuntime.streamDone
  );
  var [copied, setCopied] = useWbcState(false);
  var [voiceSnapshot, setVoiceSnapshot] = useWbcState({ status: {}, activeKey: "" });
  var referenceAttachments = Array.isArray(msg.referenceAttachments)
    ? msg.referenceAttachments
    : (Array.isArray(msg.reference_attachments) ? msg.reference_attachments : []);
  // A live reply has an immutable Markdown prefix and one active block. Only
  // the active block is reparsed while tokens arrive; completed paragraphs and
  // code fences keep their DOM identity for the rest of the stream.
  var streamingPartsRef = useWbcRef(null);
  var streamingParts = useWbcMemo(function () {
    if (!live) {
      streamingPartsRef.current = null;
      return { source: "", stable: "", stableBlocks: [], active: "" };
    }
    var parts = workbenchServices.markdown().splitStableBlocks(renderedText, streamingPartsRef.current);
    streamingPartsRef.current = parts;
    return parts;
  }, [renderedText, live]);
  var activeBodyHtml = useWbcMemo(function () {
    return streamingParts.active
      ? wbcRenderMarkdown(streamingParts.active, { interactive: false })
      : "";
  }, [streamingParts.active]);
  var bodyHtml = useWbcMemo(function () {
    return live ? "" : wbcRenderMarkdown(renderedText);
  }, [renderedText, live]);
  var bodyRef = useWbcRef(null);
  var activeBodyRef = useWbcRef(null);
  var previousLiveVisibleTextLengthRef = useWbcRef(0);
  useWbcLayoutEffect(function () {
    if (!live || !bodyRef.current) {
      previousLiveVisibleTextLengthRef.current = 0;
      return;
    }
    if (activeBodyRef.current) wbcClearStreamingFades(activeBodyRef.current);
    var visibleTextLength = String(bodyRef.current.textContent || "").length;
    var previousLength = Number(previousLiveVisibleTextLengthRef.current || 0);
    previousLiveVisibleTextLengthRef.current = visibleTextLength;
    var addedVisibleCharacterCount = visibleTextLength - previousLength;
    if (addedVisibleCharacterCount <= 0 || !activeBodyRef.current) return;
    wbcFadeInStreamingTail(activeBodyRef.current, addedVisibleCharacterCount);
  }, [activeBodyHtml, live, renderedText.length]);
  useWbcEffect(function () {
    return WbcVoice.subscribe(setVoiceSnapshot);
  }, []);
  useWbcEffect(function () {
    if (live || !bodyRef.current) return undefined;
    var chartService = window.CyreneUI && window.CyreneUI.chart;
    if (chartService && typeof chartService.mount === "function") {
      chartService.mount(bodyRef.current, {
        messageId: String(msg && msg.id || ""),
        chatId: String(chatId || ""),
      });
    }
    return function () {
      if (chartService && typeof chartService.dispose === "function") chartService.dispose(bodyRef.current);
    };
  }, [bodyHtml, live]);
  async function copyText() {
    try {
      var text = String(msg.content || "");
      if (window.cyrene && typeof window.cyrene.writeClipboardText === "function") {
        window.cyrene.writeClipboardText(text);
      } else {
        await navigator.clipboard.writeText(text);
      }
      setCopied(true);
      setTimeout(function () { setCopied(false); }, 1600);
    } catch (e) {
      console.error("Failed to copy workbench message:", e);
    }
  }
  // Some compact conversation surfaces (split chat, side agents, quick views)
  // reuse this component directly instead of going through the primary
  // timeline renderer. Keep the activity-card compatibility boundary here as
  // well, otherwise a durable reasoning/tool record with empty `content`
  // degrades into a footer-only assistant row on those surfaces.
  var activityView = live ? null : wbcActivityMessageView(msg);
  if (activityView) {
    if (!activityView.visible) return null;
    return (
      <WbcLiveActivityCard
        activity={activityView.activity}
        active={activityView.active}
        hasReplyText={activityView.hasReplyText}
        live={activityView.live}
      />
    );
  }
  if (!live && String(msg && msg.kind || "") === "goal_milestone") {
    return <WbcGoalMilestoneMessage msg={msg} chatId={chatId} />;
  }

  if (
    !sourceText.trim()
    && !(liveRuntime && Array.isArray(liveRuntime.artifacts) && liveRuntime.artifacts.length)
    && !(Array.isArray(msg.attachments) && msg.attachments.length)
    && !referenceAttachments.length
  ) return null;
  return (
    <div className="wbc-msg assistant">
      {!live && msg.trace && msg.trace.length > 0 && <WbcTraceCard trace={msg.trace} />}
      {sourceText ? (live ? (
        <div key="streaming-body" className="wbc-msg-body markdown streaming" ref={bodyRef}>
          {(streamingParts.stableBlocks || []).map(function (block, index) {
            return <WbcStreamingStableBlock key={index} source={block} />;
          })}
          {activeBodyHtml ? <div className="wbc-stream-active" ref={activeBodyRef} dangerouslySetInnerHTML={{ __html: activeBodyHtml }} /> : null}
        </div>
      ) : (
        <div key="body" className="wbc-msg-body markdown" ref={bodyRef} dangerouslySetInnerHTML={{ __html: bodyHtml }} />
      )) : null}
      {live
        ? <WbcLiveAgentArtifacts files={liveRuntime.artifacts} onOpenFile={onOpenFile} />
        : <React.Fragment>
          <WbcMediaReferences files={referenceAttachments} onOpenFile={onOpenFile} />
          <WbcAgentFiles files={msg.attachments} onOpenFile={onOpenFile} />
        </React.Fragment>}
      {!live && <WbcAssistantMessageFooter
        copied={copied} msg={msg} onCopy={copyText}
        onRetryMessage={onRetryMessage} voiceSnapshot={voiceSnapshot}
      />}
    </div>
  );
}

// After this many ms with no new sign of life (reply delta, tool call, phase,
// subagent or intermediate message) the live card switches from a plain elapsed
// counter to an explicit "still working…" reassurance, so a quiet stretch reads
// as "alive but busy" rather than "frozen".
var WBC_HEARTBEAT_STALL_MS = 10000;

// Live "heartbeat" for a running reply: a self-ticking elapsed counter plus a
// random thinking phrase that fades out/in when cycling every ~4 s. Self-contained
// interval so it re-renders only itself (a leaf), never the whole conversation.
// Mounts/unmounts with the runtime, so the timer is torn down the moment the run settles.
function WbcHeartbeat({ startedAt, lastEventAt, finalizing }) {
  var heartbeatI18n = useWorkbenchI18n();
  var heartbeatLang = heartbeatI18n.lang;
  var [now, setNow] = useWbcState(function () { return Date.now(); });
  var [displayText, setDisplayText] = useWbcState(wbcRandomThinkingPhrase);
  var [animClass, setAnimClass] = useWbcState("");

  function handleAnimEnd() {
    if (animClass === "wbc-still-leave") {
      setDisplayText(wbcRandomThinkingPhrase());
      setAnimClass("wbc-still-enter");
    } else if (animClass === "wbc-still-enter") {
      setAnimClass("");
    }
  }

  useWbcEffect(function () {
    if (finalizing) return undefined;
    var timer = setInterval(function () {
      setNow(Date.now());
      setAnimClass("wbc-still-leave");
    }, 4000);
    return function () { clearInterval(timer); };
  }, [finalizing]);
  useWbcEffect(function () {
    setDisplayText(wbcRandomThinkingPhrase());
  }, [heartbeatLang]);
  if (!startedAt) return null;
  if (finalizing) {
    return (
      <div className="wbc-heartbeat finalizing" role="status" aria-live="polite">
        <span className="wbc-heartbeat-check" aria-hidden="true">{WBC_ICONS.check}</span>
        <span>{wbcT("workbenchChat.finalizing", "Reply complete · saving results…")}</span>
      </div>
    );
  }
  var elapsed = Math.max(0, Math.round((now - startedAt) / 1000));
  var stalled = !!lastEventAt && (now - lastEventAt) > WBC_HEARTBEAT_STALL_MS;
  return (
    <div className={"wbc-heartbeat" + (stalled ? " stalled" : "")} aria-live="polite">
      <span className="wbc-heartbeat-pulse" />
      <span className="wbc-heartbeat-elapsed">{wbcT("workbenchChat.elapsed", "Running {s}s", { s: elapsed })}</span>
      <span className={"wbc-heartbeat-still" + (animClass ? " " + animClass : "")} onAnimationEnd={handleAnimEnd}>{displayText}</span>
    </div>
  );
}

function wbcPhase1ReasoningPreview(text) {
  var compact = String(text || "").replace(/\s+/g, " ").trim();
  if (!compact) return "";
  return compact.length > 220 ? compact.slice(0, 217).trimEnd() + "…" : compact;
}

function wbcPhase1ProgressDetail(entries) {
  return (Array.isArray(entries) ? entries : []).map(function (entry) {
    var text = String(entry && (entry.text || entry.tool) || "").trim();
    if (entry && entry.detailKey) {
      text = wbcT(entry.detailKey, text, entry.detailParams || {});
    } else if (entry && (entry.kind === "tool" || entry.tool)) {
      text = wbcLocalizedToolName(text);
    }
    var preview = String(entry && entry.preview || "").trim();
    var status = String(entry && entry.status || "").trim().toLowerCase();
    var failed = !!(entry && entry.failed) || ["failed", "error", "failure", "expired", "cancelled"].indexOf(status) >= 0;
    var mark = failed ? "×" : (status === "running" ? "◌" : "✓");
    return [mark, text, preview ? wbcParenthesize(wbcToolPreviewText(preview)) : ""]
      .filter(Boolean)
      .join(" ");
  }).filter(Boolean).join("\n");
}

function WbcLiveActivityCard({ activity, active, hasReplyText, live }) {
  var item = activity || {};
  var entries = Array.isArray(item.progress) ? item.progress : [];
  var isPhase1 = String(item.llmPhase || "") === "phase1";
  var phase1Running = isPhase1 && active && String(item.llmStatus || "") !== "completed";
  var phase1Preview = isPhase1 ? wbcPhase1ReasoningPreview(item.reasoning) : "";
  var visibleEntries = entries;
  if (isPhase1) {
    visibleEntries = [{
      kind: "phase1",
      text: phase1Running
        ? wbcT("workbenchChat.phase1Understanding", "Understanding the request")
        : wbcT("workbenchChat.phase1Understood", "Understood the request"),
      preview: phase1Preview,
      status: phase1Running ? "running" : "completed",
    }].concat(entries);
  }
  var hasReasoning = !!String(item.reasoning || "").trim();
  var isCodexProvider = String(item.provider || "") === "codex_oauth";
  var phase1Detail = hasReasoning
    ? String(item.reasoning || "")
    : wbcPhase1ProgressDetail(visibleEntries);
  var visibleReasoning = !isCodexProvider && (hasReasoning || isPhase1) ? phase1Detail : "";

  var label = isPhase1
      ? wbcT("workbenchChat.phase1Card", "Execution · Phase 1")
      : hasReasoning
      ? (active
        ? wbcT("workbenchChat.traceIdle", "Thinking...")
        : wbcT("workbenchChat.thinkingProcess", "Thinking process"))
      : wbcT("workbenchChat.traceLabel", "Execution");

  return (
    <WbcTraceCard
      trace={visibleEntries}
      disclosureId={item.id}
      live={!!live}
      running={isPhase1 ? phase1Running : active}
      reasoning={visibleReasoning}
      label={label}
    />
  );
}

function WbcActivityGroup({ group }) {
  var item = group || {};
  var activities = Array.isArray(item.activities) ? item.activities : [];
  var active = !!item.active;
  var [expanded, setExpanded] = wbcUseDisclosure(item.id, activities.map(function (message) { return message.id; }));
  var detailsMountedRef = useWbcRef(expanded);
  if (expanded) detailsMountedRef.current = true;
  var duration = item.durationMs == null ? "" : wbcFormatProcessingDuration(item.durationMs);
  var runningSummary = active ? wbcActivityGroupRunningSummary(activities) : null;
  var summary = active
    ? runningSummary.label
    : (duration
      ? wbcT("workbenchChat.activityGroup.completedDuration", "Processed {duration}", { duration: duration })
      : wbcT("workbenchChat.activityGroup.completed", "Processed"));


  return (
    <div className={"wbc-activity-group" + (expanded ? " expanded" : "") + (active ? " active" : " completed")} aria-busy={active ? "true" : undefined}>
      <button
        type="button"
        className="wbc-activity-group-summary"
        aria-expanded={expanded}
        aria-label={expanded
          ? wbcT("workbenchChat.activityGroup.collapse", "Collapse {count} activity items", { count: activities.length })
          : wbcT("workbenchChat.activityGroup.expand", "Expand {count} activity items", { count: activities.length })}
        onClick={function (event) {
          var nextExpanded = !expanded;
          var thread = event.currentTarget.closest(".wbc-thread");
          if (thread) {
            thread.dispatchEvent(new CustomEvent("workbench:trace-disclosure", {
              detail: { anchor: event.currentTarget, expanding: nextExpanded },
            }));
          }
          setExpanded(nextExpanded);
        }}
      >
        <span className="wbc-activity-group-state" aria-hidden="true">
          {active ? runningSummary.icon : WBC_ICONS.check}
        </span>
        <b>{summary}</b>
        <span className="wbc-activity-group-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
      </button>
      <div className={"wbc-activity-group-collapse" + (expanded ? " open" : "")} aria-hidden={!expanded}>
        <div className="wbc-activity-group-collapse-inner">
          <div className="wbc-activity-group-list">
            {(detailsMountedRef.current ? activities : []).map(function (message, index) {
              var view = wbcActivityMessageView(message);
              if (!view || !view.visible) return null;
              return (
                <WbcLiveActivityCard
                  key={String(message.id || index)}
                  activity={view.activity}
                  active={view.active}
                  hasReplyText={view.hasReplyText}
                  live={view.live}
                />
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// Network deltas and visual frames are separate concerns. Token arrival only
// updates a ref; one animation-frame scheduler publishes the newest buffered
// text at a bounded cadence, so fast providers cannot force React/layout work
// for every token and slow providers remain responsive.
var WBC_LIVE_FRAME_INTERVAL_MS = 48;
var WBC_LIVE_FADE_MAX_CHARACTERS = 96;

function wbcClearStreamingFades(body) {
  if (!body || typeof document === "undefined") return;
  Array.from(body.querySelectorAll(".wbc-stream-fade")).forEach(function (existingFade) {
    var parent = existingFade.parentNode;
    if (!parent) return;
    while (existingFade.firstChild) parent.insertBefore(existingFade.firstChild, existingFade);
    parent.removeChild(existingFade);
    parent.normalize();
  });
}

function wbcFadeInStreamingTail(body, addedCharacterCount) {
  if (!body || typeof document === "undefined") return;
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  var budget = Math.min(
    WBC_LIVE_FADE_MAX_CHARACTERS,
    Math.max(1, Number(addedCharacterCount) || 0)
  );
  var walker = document.createTreeWalker(body, 4);
  var textNodes = [];
  var collectedCharacters = 0;
  var node = walker.lastChild();
  while (node && collectedCharacters < WBC_LIVE_FADE_MAX_CHARACTERS) {
    var nodeText = String(node.nodeValue || "");
    if (nodeText.trim()) {
      textNodes.push(node);
      collectedCharacters += nodeText.length;
    }
    node = walker.previousNode();
  }
  for (var index = 0; index < textNodes.length && budget > 0; index += 1) {
    var textNode = textNodes[index];
    var value = String(textNode.nodeValue || "");
    var take = Math.min(value.length, budget);
    var start = value.length - take;
    var suffix = value.slice(start);
    if (!suffix) continue;
    var fade = document.createElement("span");
    fade.className = "wbc-stream-fade";
    fade.textContent = suffix;
    textNode.nodeValue = value.slice(0, start);
    textNode.parentNode.insertBefore(fade, textNode.nextSibling);
    budget -= take;
  }
}

function wbcUseBufferedLiveText(text, flush) {
  var source = String(text || "");
  var [renderedText, setRenderedText] = useWbcState(source);
  var latestTextRef = useWbcRef(source);
  var renderedTextRef = useWbcRef(source);
  var renderFrameRef = useWbcRef(0);
  var lastRenderAtRef = useWbcRef(0);
  latestTextRef.current = source;

  useWbcEffect(function () {
    if (flush || !source) {
      if (renderFrameRef.current) cancelAnimationFrame(renderFrameRef.current);
      renderFrameRef.current = 0;
      renderedTextRef.current = source;
      setRenderedText(source);
      return;
    }
    if (renderFrameRef.current || renderedTextRef.current === source) return;
    function publishFrame(now) {
      var elapsed = now - Number(lastRenderAtRef.current || 0);
      if (elapsed < WBC_LIVE_FRAME_INTERVAL_MS) {
        renderFrameRef.current = requestAnimationFrame(publishFrame);
        return;
      }
      renderFrameRef.current = 0;
      lastRenderAtRef.current = now;
      var nextText = latestTextRef.current;
      if (renderedTextRef.current !== nextText) {
        renderedTextRef.current = nextText;
        setRenderedText(nextText);
      }
    }
    renderFrameRef.current = requestAnimationFrame(publishFrame);
  }, [source, flush]);

  useWbcEffect(function () {
    return function () {
      if (renderFrameRef.current) cancelAnimationFrame(renderFrameRef.current);
    };
  }, []);
  // A terminal reply is already authoritative. Return it in the same render
  // that observes streamDone instead of waiting for the passive effect below;
  // waiting would paint one stale frame immediately before persistence.
  return flush ? source : renderedText;
}

function WbcLiveMessage({ runtime, onOpenFile }) {
  return <WbcAssistantMessage msg={{ role: "assistant" }} liveRuntime={runtime} onOpenFile={onOpenFile} />;
}

function WbcTranscript({ messages, runtime, onOpenFile, chatId, pendingQuestion, onAnswer }) {
  var timeline = wbcGroupConsecutiveActivityMessages(wbcProjectTranscript(messages || [], runtime), runtime);
  return <React.Fragment>{timeline.map(function (message) {
    if (message.questionPrompt && pendingQuestion && message.questionId === pendingQuestion.id) return <WbcThreadItem key={message.id}><WbcQuestionPrompt pending={pendingQuestion} onAnswer={onAnswer} busy={false} /></WbcThreadItem>;
    if (message.runtimeContinuation) return <WbcThreadItem key={message.id}><WbcContinuationIndicator /></WbcThreadItem>;
    if (message.activityGroup) return <WbcThreadItem key={message.id}><WbcActivityGroup group={message} /></WbcThreadItem>;
    if (message.notificationCard) return <WbcThreadItem key={message.id}><WbcAgentNotification notice={message.notification} /></WbcThreadItem>;
    if (message.modelStatusCard) return <WbcThreadItem key={message.id}><WbcModelStatusMessage msg={message} /></WbcThreadItem>;
    return <WbcThreadItem key={message.id}>{message.role === "user"
      ? <WbcUserMessage msg={message} onOpenFile={onOpenFile} />
      : <WbcAssistantMessage msg={message} onOpenFile={onOpenFile} chatId={chatId} />}</WbcThreadItem>;
  })}</React.Fragment>;
}

function WbcRuntimeTranscript({ runtime, onOpenFile }) {
  if (!runtime) return null;
  if (runtime.timeline) return <WbcTranscript runtime={runtime} onOpenFile={onOpenFile} />;
  var timeline = wbcGroupConsecutiveActivityMessages(
    wbcRuntimeTimelineMessages(runtime, { showReasoningPlaceholder: true }),
    runtime
  );
  return (
    <React.Fragment>
      {timeline.map(function (item) {
        if (item.runtimeHeartbeat) {
          return null;
        }
        if (item.runtimeNotification) {
          return <WbcThreadItem key={item.id}><WbcAgentNotification notice={item.notification} /></WbcThreadItem>;
        }
        if (item.runtimeContinuation) {
          return <WbcThreadItem key={item.id}><WbcContinuationIndicator /></WbcThreadItem>;
        }
        if (item.activityGroup) {
          return <WbcThreadItem key={item.id}><WbcActivityGroup group={item} /></WbcThreadItem>;
        }
        if (item.runtimeActivity) {
          var entries = Array.isArray(item.runtimeActivity.progress) ? item.runtimeActivity.progress : [];
          if (!item.runtimeActivityActive && entries.length === 0 && !String(item.runtimeActivity.reasoning || "").trim()) return null;
          return <WbcThreadItem key={item.id}><WbcLiveActivityCard activity={item.runtimeActivity} active={!!item.runtimeActivityActive} hasReplyText={!!item.runtimeActivityHasReplyText} live={true} /></WbcThreadItem>;
        }
        return null;
      })}
      {(runtime.text || (runtime.artifacts && runtime.artifacts.length))
        ? <WbcThreadItem><WbcLiveMessage runtime={runtime} onOpenFile={onOpenFile} /></WbcThreadItem>
        : null}
    </React.Fragment>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

var WBC_DRAFT_PREFIX = "cyrene-wbc-draft-";
var WBC_ATTACH_PREFIX = "cyrene-wbc-attach-";
var WBC_WORKSPACE_PREFIX = "cyrene-wbc-workspace-";
var WBC_DRAFT_SAVE_DELAY_MS = 300;
var WBC_NATIVE_FIELD_SIZING = typeof CSS !== "undefined"
  && typeof CSS.supports === "function"
  && CSS.supports("field-sizing", "content");

function wbcIsPersistableChatId(id) {
  return !!id;
}

// The optional `ns` prefix isolates a surface's drafts/attachments/workspace
// from the main chat's (the quick-chat window shares localStorage with the main
// window, so it passes a namespace to avoid clobbering an in-progress draft for
// the same chat id). The persistability gate still tests the raw chat id, so a
// brand-new chat (id "") is never stored regardless of namespace.
function wbcLoadDraft(id, ns) {
  if (!wbcIsPersistableChatId(id)) return "";
  try { return localStorage.getItem(WBC_DRAFT_PREFIX + (ns || "") + id) || ""; } catch (e) { return ""; }
}

function wbcSaveDraft(id, text, ns) {
  if (!wbcIsPersistableChatId(id)) return;
  try {
    if (text) localStorage.setItem(WBC_DRAFT_PREFIX + (ns || "") + id, text);
    else localStorage.removeItem(WBC_DRAFT_PREFIX + (ns || "") + id);
  } catch (e) {}
}

function wbcSyncLegacyComposerHeight(textarea, text, compact) {
  if (!textarea) return;
  if (!String(text || "")) {
    textarea.style.height = compact ? "32px" : "44px";
    return;
  }
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
}

function wbcLoadAttachments(id, ns) {
  if (!wbcIsPersistableChatId(id)) return [];
  try {
    var raw = localStorage.getItem(WBC_ATTACH_PREFIX + (ns || "") + id);
    var parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) { return []; }
}

function wbcSaveAttachments(id, list, ns) {
  if (!wbcIsPersistableChatId(id)) return;
  try {
    if (list && list.length) localStorage.setItem(WBC_ATTACH_PREFIX + (ns || "") + id, JSON.stringify(list));
    else localStorage.removeItem(WBC_ATTACH_PREFIX + (ns || "") + id);
  } catch (e) {}
}

function wbcWorkspaceContextKey(chatId, projectId) {
  return String(projectId || "") + ":" + (wbcIsPersistableChatId(chatId) ? String(chatId) : "__new__");
}

function wbcLoadWorkspaceOverride(key, ns) {
  if (!key) return "";
  try { return localStorage.getItem(WBC_WORKSPACE_PREFIX + (ns || "") + key) || ""; } catch (e) { return ""; }
}

function wbcSaveWorkspaceOverride(key, path, ns) {
  if (!key) return;
  try {
    if (path) localStorage.setItem(WBC_WORKSPACE_PREFIX + (ns || "") + key, path);
    else localStorage.removeItem(WBC_WORKSPACE_PREFIX + (ns || "") + key);
  } catch (e) {}
}

export { WbcTranscript, WBC_DRAFT_SAVE_DELAY_MS, WBC_NATIVE_FIELD_SIZING, WbcActivityGroup, WbcAgentNotification, WbcAssistantMessage, WbcContinuationIndicator, WbcErrorNotice, WbcLiveActivityCard, WbcLiveMessage, WbcModelStatusMessage, WbcQuestionPrompt, WbcRuntimeTranscript, WbcUserMessage, wbcGroupConsecutiveActivityMessages, wbcIsActivityMessage, wbcLoadAttachments, wbcLoadDraft, wbcLoadWorkspaceOverride, wbcSaveAttachments, wbcSaveDraft, wbcSaveWorkspaceOverride, wbcSyncLegacyComposerHeight, wbcWorkspaceContextKey }
