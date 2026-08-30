import { useWbcEffect, useWbcRef, useWbcState, wbcErrorText, wbcT } from "../../workbench-chat.jsx"

function goalApi(chatId, suffix, options) {
  var path = "/api/workbench/chats/" + encodeURIComponent(String(chatId || "")) + "/goal" + (suffix || "");
  var config = Object.assign({ method: "GET" }, options || {});
  if (config.body && typeof config.body !== "string") {
    config.headers = Object.assign({ "Content-Type": "application/json" }, config.headers || {});
    config.body = JSON.stringify(config.body);
  }
  return fetch(path, config).then(async function (response) {
    var payload = await response.json().catch(function () { return {}; });
    if (!response.ok || payload.ok === false) {
      var error = new Error(payload.error || response.statusText || "Goal operation failed");
      error.code = payload.code || "goal_operation_failed";
      throw error;
    }
    return payload;
  });
}

function goalLines(value) {
  return (Array.isArray(value) ? value : []).map(String).filter(Boolean).join("\n");
}

function goalArray(value) {
  return String(value || "").split("\n").map(function (item) { return item.trim(); }).filter(Boolean);
}

function goalDraft(goal) {
  return {
    objective: String(goal && goal.objective || ""),
    acceptanceCriteria: goalLines(goal && goal.acceptanceCriteria),
    constraints: goalLines(goal && goal.constraints),
    outOfScope: goalLines(goal && goal.outOfScope),
    durationMinutes: Math.max(5, Math.round(Number(goal && goal.durationSeconds || 7200) / 60)),
  };
}

function goalPayload(draft) {
  return {
    objective: String(draft.objective || "").trim(),
    acceptanceCriteria: goalArray(draft.acceptanceCriteria),
    constraints: goalArray(draft.constraints),
    outOfScope: goalArray(draft.outOfScope),
    durationSeconds: Math.max(300, Math.round(Number(draft.durationMinutes || 5) * 60)),
  };
}

function changedGoalPayload(goal, draft) {
  var next = goalPayload(draft);
  var changed = {};
  ["objective", "acceptanceCriteria", "constraints", "outOfScope"].forEach(function (key) {
    var current = key === "objective" ? String(goal && goal[key] || "") : (Array.isArray(goal && goal[key]) ? goal[key].map(String) : []);
    if (JSON.stringify(current) !== JSON.stringify(next[key])) changed[key] = next[key];
  });
  if (Number(goal && goal.durationSeconds || 0) !== next.durationSeconds) changed.durationSeconds = next.durationSeconds;
  return changed;
}

function goalStatusLabel(status) {
  var values = {
    negotiating: ["Discussing", "协商中"],
    proposed: ["Awaiting confirmation", "等待确认"],
    active: ["Working", "执行中"],
    reviewing: ["Independent review", "独立审查中"],
    reflecting: ["Deep reflection", "深度反思中"],
    waiting_user: ["Waiting for your answer", "等待你的回答"],
    paused: ["Paused", "已暂停"],
    blocked: ["Needs attention", "需要处理"],
  };
  var pair = values[String(status || "")] || [String(status || "Goal"), String(status || "目标")];
  return wbcT("goal.status." + status, pair[0]);
}

function GoalFields({ draft, setDraft, disabled, prefix }) {
  function update(key, value) { setDraft(Object.assign({}, draft, { [key]: value })); }
  return (
    <div className="wbc-goal-fields">
      <label className="wbc-goal-field wbc-goal-field-objective">
        <span>{wbcT("goal.objective", "Objective")}</span>
        <textarea value={draft.objective} disabled={disabled} rows="3" onChange={function (event) { update("objective", event.target.value); }} />
      </label>
      <label className="wbc-goal-field wbc-goal-field-criteria">
        <span>{wbcT("goal.acceptanceCriteria", "Acceptance criteria")}</span>
        <textarea value={draft.acceptanceCriteria} disabled={disabled} rows="5" placeholder={wbcT("goal.onePerLine", "One measurable criterion per line")} onChange={function (event) { update("acceptanceCriteria", event.target.value); }} />
      </label>
      <div className="wbc-goal-field-grid">
        <label className="wbc-goal-field wbc-goal-field-compact">
          <span>{wbcT("goal.constraints", "Constraints")}</span>
          <textarea value={draft.constraints} disabled={disabled} rows="3" placeholder={wbcT("goal.optionalOnePerLine", "Optional, one per line")} onChange={function (event) { update("constraints", event.target.value); }} />
        </label>
        <label className="wbc-goal-field wbc-goal-field-compact">
          <span>{wbcT("goal.outOfScope", "Out of scope")}</span>
          <textarea value={draft.outOfScope} disabled={disabled} rows="3" placeholder={wbcT("goal.optionalOnePerLine", "Optional, one per line")} onChange={function (event) { update("outOfScope", event.target.value); }} />
        </label>
      </div>
      <label className="wbc-goal-duration">
        <span>{wbcT("goal.activeDuration", "Active duration")}</span>
        <span className="wbc-goal-duration-input">
          <input type="number" min="5" max="10080" step="5" value={draft.durationMinutes} disabled={disabled} aria-label={prefix + " duration"} onChange={function (event) { update("durationMinutes", event.target.value); }} />
          <small>{wbcT("goal.minutes", "minutes")}</small>
        </span>
      </label>
    </div>
  );
}

function WbcGoalConfirmationDialog({ chat, onGoalChanged }) {
  var goal = chat && chat.activeGoal;
  var key = goal ? String(goal.id || "") + ":" + String(goal.revision || 1) : "";
  var shouldOpen = !!(goal && goal.status === "proposed" && Number(goal.attempt || 0) === 0);
  var [dismissedKey, setDismissedKey] = useWbcState("");
  var [draft, setDraft] = useWbcState(goalDraft(goal));
  var [saving, setSaving] = useWbcState(false);
  var [error, setError] = useWbcState("");

  useWbcEffect(function () {
    setDraft(goalDraft(goal));
    setSaving(false);
    setError("");
  }, [key]);

  if (!shouldOpen || dismissedKey === key) return null;
  var payload = goalPayload(draft);
  var valid = payload.objective.length >= 3 && payload.acceptanceCriteria.length > 0 && !saving;

  function confirm(event) {
    event.preventDefault();
    if (!valid) return;
    setSaving(true);
    setError("");
    goalApi(chat.id, "/confirm", { method: "POST", body: payload }).then(function (result) {
      if (onGoalChanged) onGoalChanged(result.activeGoal || null);
    }).catch(function (cause) {
      setError(wbcErrorText(cause));
      setSaving(false);
    });
  }

  return window.ReactDOM.createPortal(
    <div className="wbc-goal-confirm-scrim">
      <form className="wbc-goal-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="wbc-goal-confirm-title" onSubmit={confirm}>
        <header>
          <div>
            <span className="wbc-goal-eyebrow">GOAL LOOP</span>
            <h2 id="wbc-goal-confirm-title">{wbcT("goal.confirmTitle", "Confirm the Goal")}</h2>
            <p>{wbcT("goal.confirmHint", "Continuous execution and independent review start only after confirmation.")}</p>
          </div>
          <button type="button" className="wbc-goal-icon-btn" disabled={saving} aria-label={wbcT("common.close", "Close")} onClick={function () { setDismissedKey(key); }}>×</button>
        </header>
        <main>
          <GoalFields draft={draft} setDraft={setDraft} disabled={saving} prefix="goal-confirm" />
          {error ? <div className="wbc-goal-error" role="alert">{error}</div> : null}
        </main>
        <footer>
          <button type="button" className="wb-btn" disabled={saving} onClick={function () {
            setDismissedKey(key);
            window.dispatchEvent(new CustomEvent("workbench:open-goal-tab", { detail: { chatId: String(chat && chat.id || "") } }));
          }}>{wbcT("goal.reviewLater", "Review in Goal tab")}</button>
          <button type="submit" className="wb-btn primary" disabled={!valid}>{saving ? wbcT("goal.starting", "Starting…") : wbcT("goal.confirmStart", "Confirm and start")}</button>
        </footer>
      </form>
    </div>,
    document.querySelector(".workbench-shell") || document.body
  );
}

function WbcGoalTab({ chat, onGoalChanged }) {
  var goal = chat && chat.activeGoal;
  var key = goal ? [goal.id, goal.revision, goal.status, goal.durationSeconds].map(String).join(":") : "";
  var [draft, setDraft] = useWbcState(goalDraft(goal));
  var [busy, setBusy] = useWbcState("");
  var [stopBusy, setStopBusy] = useWbcState(false);
  var [error, setError] = useWbcState("");
  var stopRequestedRef = useWbcRef(false);

  useWbcEffect(function () {
    setDraft(goalDraft(goal));
    setBusy("");
    setStopBusy(false);
    setError("");
    stopRequestedRef.current = false;
  }, [key]);

  if (!goal) return null;
  var status = String(goal.status || "");
  var editable = status !== "negotiating";
  var changes = changedGoalPayload(goal, draft);
  var hasChanges = Object.keys(changes).length > 0;
  var payload = goalPayload(draft);
  var valid = payload.objective.length >= 3 && payload.acceptanceCriteria.length > 0;
  var canAcceptCurrent = Number(goal.attempt || 0) > 0 && !!(goal.candidate || goal.review);

  function action(name, method, body) {
    setBusy(name);
    setError("");
    return goalApi(chat.id, name === "update" ? "" : "/" + name, { method: method || "POST", body: body }).then(function (result) {
      if (!stopRequestedRef.current && onGoalChanged) onGoalChanged(result.activeGoal || null);
      return result;
    }).catch(function (cause) {
      setError(wbcErrorText(cause));
      throw cause;
    }).finally(function () { setBusy(""); });
  }

  function save() {
    if (!hasChanges || !valid) return;
    action("update", "PATCH", changes).catch(function () {});
  }

  function confirmRevision() {
    if (!valid) return;
    action("confirm", "POST", payload).catch(function () {});
  }

  function stopGoal() {
    if (stopBusy) return;
    stopRequestedRef.current = true;
    setStopBusy(true);
    setError("");
    goalApi(chat.id, "/abort", { method: "POST" }).then(function (result) {
      if (onGoalChanged) onGoalChanged(result.activeGoal || null);
    }).catch(function (cause) {
      stopRequestedRef.current = false;
      setError(wbcErrorText(cause));
    }).finally(function () { setStopBusy(false); });
  }

  var review = goal.review && typeof goal.review === "object" ? goal.review : null;
  return (
    <div className="workbench-side-stack wbc-goal-tab">
      <section className="wbc-goal-status-card">
        <div>
          <span className={"wbc-goal-status-dot " + status} aria-hidden="true" />
          <strong>{goalStatusLabel(status)}</strong>
        </div>
        <small>{wbcT("goal.attempt", "Attempt {attempt}", { attempt: Number(goal.attempt || 0) })} · {wbcT("goal.revision", "Revision {revision}", { revision: Number(goal.revision || 1) })}</small>
      </section>

      {status === "negotiating" ? (
        <div className="wbc-goal-note">{wbcT("goal.discussingHint", "Continue the discussion in the conversation. The Agent will place a concrete proposal here when it is ready.")}</div>
      ) : (
        <GoalFields draft={draft} setDraft={setDraft} disabled={!editable || !!busy} prefix="goal-tab" />
      )}

      {review && review.verdict === "fail" ? (
        <section className="wbc-goal-review-card">
          <strong>{wbcT("goal.reviewGaps", "Review gaps")}</strong>
          {review.summary ? <p>{review.summary}</p> : null}
          <ul>{(review.criticalGaps || []).map(function (gap, index) { return <li key={index}>{gap}</li>; })}</ul>
        </section>
      ) : null}

      {error ? <div className="wbc-goal-error" role="alert">{error}</div> : null}

      <div className="wbc-goal-actions">
        {status === "proposed" ? (
          <button type="button" className="wb-btn primary" disabled={!valid || !!busy} onClick={confirmRevision}>{busy === "confirm" ? wbcT("goal.starting", "Starting…") : wbcT("goal.confirmStart", "Confirm and start")}</button>
        ) : null}
        {editable && status !== "proposed" ? (
          <button type="button" className="wb-btn" disabled={!hasChanges || !valid || !!busy} onClick={save}>{busy === "update" ? wbcT("common.saving", "Saving…") : wbcT("goal.saveSettings", "Save settings")}</button>
        ) : null}
        {status === "active" || status === "reviewing" || status === "reflecting" || status === "waiting_user" ? (
          <button type="button" className="wb-btn" disabled={!!busy} onClick={function () { action("pause").catch(function () {}); }}>{wbcT("goal.pause", "Pause")}</button>
        ) : null}
        {status === "paused" || status === "blocked" ? (
          <button type="button" className="wb-btn primary" disabled={!!busy || hasChanges} onClick={function () { action("resume").catch(function () {}); }}>{wbcT("goal.resume", "Resume")}</button>
        ) : null}
        {canAcceptCurrent ? (
          <button type="button" className="wb-btn wbc-goal-accept" disabled={!!busy || hasChanges} onClick={function () { action("accept").catch(function () {}); }}>{wbcT("goal.acceptCurrent", "Accept current result")}</button>
        ) : null}
      </div>
      {hasChanges ? <small className="wbc-goal-unsaved">{wbcT("goal.unsavedHint", "Save or confirm these changes before resuming or accepting.")}</small> : null}
      <div className="wbc-goal-stop-bar">
        <button type="button" className="wb-btn danger" disabled={stopBusy} onClick={stopGoal}>{stopBusy ? wbcT("goal.stopping", "Stopping…") : wbcT("goal.stop", "Stop Goal")}</button>
      </div>
    </div>
  );
}

export { WbcGoalConfirmationDialog, WbcGoalTab, goalApi }
