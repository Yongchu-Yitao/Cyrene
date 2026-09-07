import { collectStructure } from './javascript-structure.mjs'

// Aggregate disjoint, own-scope metrics. Do not sum function spans: an outer
// function's span includes its callbacks and would count their lines twice.
export function summarizeStructure(metrics) {
  const groups = {};
  for (const [scope, values] of Object.entries(metrics)) {
    const file = scope.split('::')[0];
    const group = file.startsWith('electron/') ? 'electron'
      : file.includes('/frontend/features/chat/') ? 'chat'
      : file.includes('/frontend/features/shell/') ? 'shell'
      : 'other';
    const row = groups[group] ||= { modules: 0, moduleLines: 0, decisions: 0, hookCalls: 0, fields: 0, setterTargets: 0, subscriptions: 0 };
    if (!scope.includes('::')) { row.modules++; row.moduleLines += values.module_lines || 0; }
    row.decisions += values.decisions || 0;
    row.hookCalls += values.hooks || 0;
    row.fields += values.fields || 0;
    row.setterTargets += values.setter_targets || 0;
    row.subscriptions += values.subscriptions || 0;
  }
  return groups;
}

if (process.argv[1]?.endsWith('/complexity-report.mjs')) {
  console.log(JSON.stringify({
    interpretation: 'Static own-scope totals, not runtime Hook execution or performance. Setter targets are syntactic call names; aliases and dynamic writes may be unrecognized.',
    groups: summarizeStructure(collectStructure()),
  }, null, 2));
}
