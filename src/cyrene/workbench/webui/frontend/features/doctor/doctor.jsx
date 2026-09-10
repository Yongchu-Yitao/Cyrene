import { cachedReport, rememberReport, loadReport } from "./reports.mjs"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
import { useWorkbenchI18n } from "../../workbench-i18n.jsx"
import { DoctorRepair } from './repair.jsx'

async function request(path, method = "GET", body) {
  const response = await fetch("/api/doctor/" + path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Doctor request failed");
  return value;
}

export function DoctorPanel({ scope = {}, onClose }) {
  const { lang, t } = useWorkbenchI18n();
  const [report, setReportState] = React.useState(() => cachedReport(scope, lang));
  function setReport(value) { if (value) rememberReport(scope, lang, value); setReportState(value); }
  const [description, setDescription] = React.useState(() => cachedReport(scope, lang)?.user_description || "");
  const descriptionId = React.useId();
  const [plan, setPlan] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const live = React.useRef(true);
  const scanVersion = React.useRef(0);
  const [refreshing, setRefreshing] = React.useState(false);
  React.useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  async function perform(operation) {
    setBusy(true); setError("");
    try { await operation(); } catch (e) { if (live.current) setError(e.message || t("doctor.requestFailed")); }
    finally { if (live.current) setBusy(false); }
  }
  async function scan(force = false) {
    const version = ++scanVersion.current;
    setRefreshing(true); setError("");
    try {
      const result = await loadReport(scope, lang, (path, method, body) => request(path, method, body && scope.auto_repair ? { ...body, description } : body), force);
      if (live.current && version === scanVersion.current) { setReport(result); setPlan(null); }
    } catch (_) { if (live.current && version === scanVersion.current) setError(t("doctor.requestFailed")); }
    finally { if (live.current && version === scanVersion.current) setRefreshing(false); }
  }
  React.useEffect(() => { setReport(cachedReport(scope, lang)); scan(); return () => { scanVersion.current += 1; }; }, [scope.project_id, scope.chat_id, scope.run_id, scope.job_id, scope.incident_id, scope.client_code, scope.auto_repair, lang]);
  React.useEffect(() => {
    if (!report || (report.analysis.status !== "running" && report.failure?.status !== "running")) return;
    let stopped = false;
    let timer;
    async function poll() {
      try { const next = await request("reports/" + report.id); if (!stopped) setReport(next); }
      catch (e) { if (!stopped) setError(t("doctor.requestFailed")); }
      if (!stopped) timer = setTimeout(poll, 1500);
    }
    timer = setTimeout(poll, 1500);
    return () => { stopped = true; clearTimeout(timer); };
  }, [report && report.id, report && report.analysis.status, report?.failure?.status]);
  React.useEffect(() => {
    const id = report?.failure?.fixed_plan_id;
    if (!id) return;
    let stopped = false;
    request('reports/' + id).then(value => { if (!stopped) setPlan(value); }).catch(() => { if (!stopped) setError(t('doctor.requestFailed')); });
    return () => { stopped = true; };
  }, [report?.failure?.fixed_plan_id]);
  const button = (label, action, disabled = busy) => <button type="button" className="wb-btn ghost" disabled={disabled} onClick={action}>{label}</button>;
  function exportReport() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "cyrene-doctor.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const statusLabel = value => ({ passed: t("doctor.passed"), failed: t("doctor.failed"), unknown: t("doctor.unknown"), skipped: t("doctor.skipped"), info: t("doctor.info"), saved: t("doctor.saved"), unchanged: t("doctor.unchanged"), conflict: t("doctor.conflict"), pending: t("doctor.pending"), running: t("doctor.running") })[value] || t("doctor.unknown");
  const attention = report ? report.findings.filter(f => ["failed", "unknown"].includes(f.status)) : [];
  const remaining = report ? report.findings.filter(f => !["failed", "unknown"].includes(f.status)) : [];
  const renderFinding = item => <article key={item.id} className={"wb-doctor-finding is-" + item.status}>
        <span className="wb-doctor-status">{statusLabel(item.status)}</span><strong>{({ passed: "✓", failed: "⚠", unknown: "?", skipped: "—", info: "i" })[item.status]} {item.summary[lang]}</strong>

        {["failed", "unknown"].includes(item.status) && <p>{item.direction[lang]}</p>}
        <details><summary>{t("doctor.details")}</summary><small>{item.id} · {item.code}</small><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(item.evidence, null, 2)}</pre></details>
        {item.actions.map((action, index) => <span key={index}>{action.available === false ? <small>{action.reason}</small> : button(t("doctor.reviewRepair"), () => perform(async () => setPlan(await request("reports/" + report.id + "/repair-plan", "POST", { finding_id: item.id, action_index: index }))))}</span>)}
      </article>;
  return <section className="settings-panel wb-doctor-panel" aria-label={t("doctor.title")}>
    <header className="wb-doctor-head"><div className="wb-section-title"><h3>{t("doctor.title")}</h3><p>{t("doctor.intro")}</p></div>{onClose && <button type="button" className="workbench-icon-btn" aria-label={t("doctor.close")} onClick={onClose}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m6 6 12 12M18 6 6 18" /></svg></button>}</header><div className="wb-doctor-body">
    <div className="wb-doctor-actions">
      {button(t("doctor.recheck"), () => scan(true), busy || refreshing || report?.failure?.status === "running" || (report && report.analysis.status === "running"))}

      {report && (report.analysis.status === "running" || report.failure?.status === "running") && button(t("doctor.stop"), () => perform(async () => setReport(await request("reports/" + report.id + (report.failure?.status === "running" ? "/failure-workflow" : "/analysis"), "DELETE"))), busy || report.failure?.phase === "applying")}
      {report && button(t("doctor.probe"), () => perform(async () => setReport(await request("reports/" + report.id + "/probe", "POST"))))}
      {report && button(t("doctor.export"), exportReport)}
    </div>
    <p className="wb-doctor-usage">{t("doctor.usageHint")}</p>
    {!report && <div className="wb-doctor-initial">
      <h3>{t("doctor.checks")}</h3>
      <p className="wb-doctor-usage">{t("doctor.firstCheck")}</p>
      {["configuration", "plugins", "conversation", "memory"].map(category => <article className="wb-doctor-finding" key={category}>
        <strong>{t("doctor.category." + category)}</strong><p>{t("doctor.category." + category + "Hint")}</p>
      </article>)}
    </div>}
    {error && <div role="alert" className="wb-doctor-finding is-failed"><p>{t("doctor.requestFailed")}</p><p>{t("doctor.offlineHelp")}</p></div>}
    {report && <>
      {report.persistence_unavailable && <p role="alert">{t("doctor.storageFailure")}</p>}
      <div className="wb-doctor-analysis" aria-live="polite">
        <div className="wb-doctor-analysis-head"><h3>{t(report.failure ? "doctor.failureTitle" : "doctor.analysis")}</h3>{report && button(t(report.failure ? "doctor.recheck" : "doctor.analyze"), () => report.failure ? scan(true) : perform(async () => setReport(await request("reports/" + report.id + "/analysis", "POST", { description }))), busy || report.analysis.status === "running" || report.failure?.status === "running")}</div>
      {report.model_probe && <p role="status">{t("doctor.modelConnection")}{statusLabel(report.model_probe.status)}</p>}
      {report.failure && <div className="wb-doctor-failure-progress">
        <p>{t('doctor.failureStatus.' + report.failure.status)}{report.failure.status === 'running' && ' · ' + t('doctor.failurePhase.' + report.failure.phase)}</p>
        {report.failure.reason && <p>{['recovery_action_ready', 'restart_required', 'host_transition_failed', 'pending_question_requires_answer', 'model_probe_passed', 'model_probe_failed', 'ambiguous_failure_target', 'failure_target_unavailable', 'candidate_unavailable', 'runtime_not_verified'].includes(report.failure.reason) ? t('doctor.failureReason.' + report.failure.reason) : t("doctor.furtherHelp")}</p>}
      </div>}
        <details open={!report.failure}><summary id={descriptionId + "-label"}>{t("doctor.descriptionLabel")}</summary>
        <textarea id={descriptionId} className="wb-textarea wb-doctor-description" aria-labelledby={descriptionId + "-label"} rows={3} maxLength={4000} value={description} onChange={event => setDescription(event.target.value)} disabled={busy || report.analysis.status === "running"} placeholder={t("doctor.descriptionPlaceholder")} aria-describedby={descriptionId + "-hint"} />
        <small className="wb-doctor-description-hint" id={descriptionId + "-hint"}>{t("doctor.descriptionHint")} <span>{description.length}/4000</span></small>
        </details>
        <p>{({ idle: t("doctor.idle"), running: t("doctor.running"), completed: t("doctor.completed"), cancelled: t("doctor.cancelled"), unavailable: t("doctor.unavailable") })[report.analysis.status]}</p>
        {report.analysis.phase === "retrying" && <p role="status">{t("doctor.recovering", { count: report.analysis.retry_count })}</p>}
        {report.analysis.user_summary && <p style={{ whiteSpace: "pre-wrap" }}>{report.analysis.user_summary}</p>}
        {report.analysis.user_next_steps?.length > 0 && <ul>{report.analysis.user_next_steps.map((step, i) => <li key={i}>{step}</li>)}</ul>}
        {!report.analysis.user_summary && !report.failure && report.analysis.status === 'completed' && <p>{t('doctor.legacyResult')}</p>}
      </div>
      <details className="wb-doctor-technical wb-doctor-finding">
        <summary>{t("doctor.technicalDetails")}</summary>
        <p>{t("doctor.technicalHint")}</p>
        {error && <pre>{error}</pre>}
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ report_id: report.id, scope: report.scope, failure: report.failure, model_probe: report.model_probe }, null, 2)}</pre>
        {report.analysis.summary && <p style={{ whiteSpace: "pre-wrap" }}>{report.analysis.summary}</p>}
        {report.analysis.direction && <p>{report.analysis.direction[lang]}</p>}
        {report.analysis.evidence_ids && <small>{t("doctor.evidence")}{report.analysis.evidence_ids.join(", ")}</small>}
        {report.analysis.next_steps && <ul>{report.analysis.next_steps.map((step, i) => <li key={i}>{step}</li>)}</ul>}
      {(!report.failure || report.failure.repair_id) && <DoctorRepair key={JSON.stringify(report.scope)} report={report} description={description} request={request} onReportChange={setReport} />}
      <h3>{t("doctor.checks")}</h3>
      {attention.map(renderFinding)}
      {remaining.length > 0 && <details className="wb-doctor-other"><summary>{t("doctor.otherChecks", { count: remaining.length })}</summary>{remaining.map(renderFinding)}</details>}
    {plan && <aside className="wb-doctor-repair" aria-label={t("doctor.repairPreview")}>
      <h3>{t("doctor.repairPreview")}</h3>
      <p>{({ restore_plugin: t("doctor.restorePlugin"), reset_tool: t("doctor.resetTool"), retry_memory: t("doctor.retryMemory") })[plan.action.kind]} · {plan.action.target}</p>
      <p>{t("doctor.repairScope")}</p>
      {plan.files.length > 0 && <details><summary>{t("doctor.files")}</summary><pre style={{ whiteSpace: "pre-wrap" }}>{plan.files.join("\n")}</pre></details>}
      <p role="status">{({ planned: t("doctor.planned"), applying: t("doctor.applying"), applied: t("doctor.applied"), failed: t("doctor.repairFailed"), rolled_back: t("doctor.rolledBack") })[plan.status]}{plan.job_status ? " · " + statusLabel(plan.job_status) : ""}</p>
      {plan.error && <p role="alert">{plan.error.message}</p>}
      {plan.status === "planned" && button(t("doctor.applyRepair"), () => perform(async () => setPlan(await request("repairs/" + plan.id + "/apply", "POST"))))}
      {plan.can_rollback && plan.status !== "rolled_back" && button(t("doctor.rollback"), () => perform(async () => setPlan(await request("repairs/" + plan.id + "/rollback", "POST"))))}
      {plan.verification_report_id && button(t("doctor.verification"), () => perform(async () => setReport(await request("reports/" + plan.verification_report_id))))}
    </aside>}
      </details>
    </>}
  </div></section>;
}

let modal = null;
export function preloadDoctor(scope = {}) {
  const language = window.CyreneUI?.i18n?.getLang() || "en";
  if (!cachedReport(scope, language)) loadReport(scope, language, request).catch(() => {});
}
export function openDoctor(scope = {}) {
  if (modal) modal();
  const previous = document.activeElement;
  const container = document.createElement("div"); (document.querySelector(".workbench-shell") || document.body).appendChild(container);
  wbSetBrowserOverlayObscured(1);
  const root = ReactDOM.createRoot(container);
  function close() { root.unmount(); container.remove(); wbSetBrowserOverlayObscured(-1); modal = null; if (previous && previous.focus) previous.focus(); }
  modal = close;
  root.render(<DoctorDialog scope={scope} close={close} />);
}

function DoctorDialog({ scope, close }) {
  const { t } = useWorkbenchI18n();
  return <dialog ref={node => { if (node && !node.open) node.showModal(); }} aria-label={t("doctor.title")} className="wb-doctor-dialog" onClick={e => { if (e.target === e.currentTarget) { const rect = e.currentTarget.getBoundingClientRect(); if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) close(); } }} onCancel={e => { e.preventDefault(); close(); }}><DoctorPanel scope={scope} onClose={close} /></dialog>;
}
