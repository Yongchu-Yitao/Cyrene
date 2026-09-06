// Session-local, scope-specific snapshots. Never persist diagnostic evidence in browser storage.
const reports = new Map();
const pending = new Map();
export function reportKey(scope, language) {
  return JSON.stringify([language, ...['project_id','chat_id','job_id','incident_id','client_code'].map(k => scope[k] || '')]);
}
export function cachedReport(scope, language) { return reports.get(reportKey(scope, language)) || null; }
export function rememberReport(scope, language, report) {
  const key = reportKey(scope, language);
  reports.delete(key); reports.set(key, report);
  if (reports.size > 12) reports.delete(reports.keys().next().value);
}
export function loadReport(scope, language, request) {
  const key = reportKey(scope, language);
  if (pending.has(key)) return pending.get(key);
  const previous = reports.get(key);
  const operation = previous?.analysis.status === 'running'
    ? request('reports/' + previous.id)
    : request('reports', 'POST', { ...scope, language });
  const task = operation.then(report => { rememberReport(scope, language, report); return report; }).finally(() => pending.delete(key));
  pending.set(key, task);
  return task;
}
