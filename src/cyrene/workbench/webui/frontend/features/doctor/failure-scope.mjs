// The clicked error owns the diagnostic scope, even if a later run has started.
export function failureScope(project, chat, error) {
  const value = error && typeof error === 'object' ? error : {};
  const failedLastRun = (['failed', 'error'].includes(chat?.lastRun?.status) || chat?.lastRun?.outcome === 'error' || chat?.lastRun?.terminationReason === 'agent_error') ? chat.lastRun.id : '';
  const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,120}$/.test(value) ? value : '';
  return { project_id: identifier(project?.id), chat_id: identifier(chat?.id),
    incident_id: identifier(value.incidentId || value.incident_id),
    run_id: identifier(value.runId || value.run_id || (value.incidentId || value.incident_id ? '' : failedLastRun)),
    auto_repair: true };
}
