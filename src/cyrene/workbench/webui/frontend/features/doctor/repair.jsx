import { useWorkbenchI18n } from '../../workbench-i18n.jsx';

export function DoctorRepair({ report, description, request, onReportChange }) {
  const { t } = useWorkbenchI18n();
  const [target, setTarget] = React.useState('');
  const [plan, setPlan] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const live = React.useRef(true);
  React.useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  React.useEffect(() => {
    const identifier = report.failure?.repair_id;
    if (!identifier) return;
    let stopped = false;
    request('reports/' + identifier).then(value => { if (!stopped) setPlan(value); }).catch(e => { if (!stopped) setError(e.message); });
    return () => { stopped = true; };
  }, [report.failure?.repair_id, report.failure?.status]);
  React.useEffect(() => {
    if (plan || report.failure || !report.repair_sessions?.length) return;
    let stopped = false;
    request('reports/' + report.repair_sessions[0].id).then(value => {
      if (!stopped) { setPlan(value); setTarget(value.action.target); }
    }).catch(e => { if (!stopped) setError(e.message); });
    return () => { stopped = true; };
  }, [report.id]);
  React.useEffect(() => {
    if (!plan || !['generating', 'applying', 'rolling_back'].includes(plan.status)) return;
    let stopped = false, timer;
    async function poll() {
      try { const value = await request('reports/' + plan.id); if (!stopped) setPlan(value); }
      catch (e) { if (!stopped) setError(e.message); }
      if (!stopped) timer = setTimeout(poll, 1500);
    }
    timer = setTimeout(poll, 1500);
    return () => { stopped = true; clearTimeout(timer); };
  }, [plan?.id, plan?.status]);
  async function perform(operation) {
    setBusy(true); setError('');
    try { const value = await operation(); if (live.current) setPlan(value); }
    catch (e) { if (live.current) setError(e.message || t('doctor.requestFailed')); }
    finally { if (live.current) setBusy(false); }
  }
  const active = busy || ['generating', 'applying', 'rolling_back'].includes(plan?.status);
  const stateKey = { generating: 'generating', planned: 'planned', cancelled: 'generationCancelled',
    interrupted: 'interrupted', applying: 'applying', rolling_back: 'rollingBack', applied: 'applied',
    failed: 'repairFailed', rolled_back: 'rolledBack' }[plan?.status];
  return <section className="wb-doctor-repair" aria-label={t('doctor.generateTitle')}>
    <h3>{t('doctor.generateTitle')}</h3>
    {!report.failure && <><p>{t('doctor.generateHint')}</p>
    <label>{t('doctor.repairTarget')} <select value={target} onChange={e => setTarget(e.target.value)} disabled={active}>
      <option value="">{t('doctor.selectTarget')}</option>
      {(report.plugin_targets || []).map(name => <option key={name} value={name}>{name}</option>)}
    </select></label>
    <p>{t(report.repair_executor?.mode === 'isolated_python' ? 'doctor.isolatedPython' : 'doctor.staticOnly')}</p>
    <button type="button" className="wb-btn" disabled={active || !target || !report.online || report.analysis.status === 'running'}
      onClick={() => perform(() => request('reports/' + report.id + '/generate-repair', 'POST', { target, description }))}>{t('doctor.generateRepair')}</button></>}
    {plan?.status === 'generating' && <button type="button" className="wb-btn ghost" disabled={busy}
      onClick={() => perform(() => request('repairs/' + plan.id + '/generation', 'DELETE'))}>{t('doctor.stopGeneration')}</button>}
    {error && <p role="alert">{error}</p>}
    {plan && <>
      <p role="status">{plan.action.target} · {t('doctor.' + stateKey)}</p>
      {plan.phase && plan.status === 'generating' && <p>{t('doctor.phase.' + plan.phase)}</p>}
      {plan.summary && <p>{plan.summary}</p>}
      {plan.error && <p role="alert">{plan.error.message}</p>}
      {plan.outcome && <p role="status">{t('doctor.outcome.' + plan.outcome.status)}{plan.outcome.basis && ' · ' + t('doctor.basis.' + plan.outcome.basis)}</p>}
      {plan.outcome?.reason && <p>{plan.outcome.reason}</p>}
      {plan.diff && <details open><summary>{t('doctor.patchDiff')}</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{plan.diff}</pre></details>}
      {plan.reproduction && <details><summary>{t('doctor.reproduction')}</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{plan.reproduction}</pre></details>}
      {plan.verification && <details><summary>{t('doctor.verification')}</summary><pre>{JSON.stringify({ before: plan.baseline, candidate: plan.verification, after: plan.outcome?.checks }, null, 2)}</pre></details>}
      {plan.status === 'planned' && <button type="button" className="wb-btn" disabled={active} onClick={() => perform(() =>
        request('repairs/' + plan.id + '/apply', 'POST', { expected_plan_hash: plan.plan_hash }))}>{t('doctor.applyRepair')}</button>}
      {plan.status === 'applied' && <button type="button" className="wb-btn" disabled={active} onClick={() => perform(async () => { const result = await request('repairs/' + plan.id + '/verify', 'POST'); if (onReportChange && live.current) onReportChange(await request('reports/' + report.id)); return result; })}>{t('doctor.verifyRepair')}</button>}
      {plan.can_rollback && <button type="button" className="wb-btn ghost" disabled={active} onClick={() => perform(() =>
        request('repairs/' + plan.id + '/rollback', 'POST'))}>{t('doctor.rollback')}</button>}
    </>}
    {report.repair_sessions?.length > 1 && <details><summary>{t('doctor.repairHistory')}</summary>
      {report.repair_sessions.map(item => <p key={item.id}><button type="button" className="wb-btn ghost" disabled={active}
        onClick={() => perform(() => request('reports/' + item.id))}>{item.action.target} · {item.created_at}</button></p>)}
    </details>}
  </section>;
}
