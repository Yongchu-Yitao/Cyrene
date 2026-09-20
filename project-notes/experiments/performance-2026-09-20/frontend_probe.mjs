// Pure projection CPU only: no DOM, React render, browser, or network timing.
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import vm from 'node:vm';
import { performance } from 'node:perf_hooks';
const web = new URL('../../../src/cyrene/workbench/webui/', import.meta.url);
const require = createRequire(new URL('package.json', web));
const { transformSync } = require('esbuild');
const source = readFileSync(new URL('frontend/features/chat/runtime-timeline.jsx', web), 'utf8');
const { code } = transformSync(source, { loader: 'jsx', format: 'cjs' });
const context = { module: { exports: {} }, require: () => ({}) };
context.exports = context.module.exports;
vm.runInNewContext(code, context);
const { wbcApplyTimeline, wbcProjectTranscript } = context.module.exports;
const fastContext = { module: {exports: {}}, require: () => ({}) };
fastContext.exports = fastContext.module.exports;
vm.createContext(fastContext);
vm.runInContext(code, fastContext);
const originalMerge = fastContext.wbcMergeChronologicalMessages;
// Restricted fast path: finite, ordered dates; correlation cases use original.
function fastMerge(base, additions) {
  if (!Array.isArray(base) || !Array.isArray(additions)) return originalMerge(base, additions);
  if (additions.some(x => !x || x.clientRequestId || x.answerToQuestionId)) return originalMerge(base, additions);
  const decorate = list => list.map(x => ({x, at: Date.parse(String(x?.createdAt || x?.created_at || ''))}));
  const a = decorate(base), b = decorate(additions);
  const ordered = list => list.every((x, i) => Number.isFinite(x.at) && (!i || list[i-1].at <= x.at));
  if (!ordered(a) || !ordered(b)) return originalMerge(base, additions);
  const known = new Set(base.map(x => String(x?.id || '')).filter(Boolean));
  const fresh = b.filter(({x}) => { const id = String(x.id || ''); if (id && known.has(id)) return false; if (id) known.add(id); return true; });
  const out = []; let i = 0, j = 0;
  while (i < a.length && j < fresh.length) out.push(a[i].at <= fresh[j].at ? a[i++].x : fresh[j++].x);
  while (i < a.length) out.push(a[i++].x);
  while (j < fresh.length) out.push(fresh[j++].x);
  return out;
}
// Execute the prototype in the same kind of VM realm as the baseline.
vm.runInContext('var originalMerge = wbcMergeChronologicalMessages; wbcMergeChronologicalMessages = ' + fastMerge.toString(), fastContext);
const optimizedMerge = fastContext.wbcMergeChronologicalMessages;
const fastProject = fastContext.module.exports.wbcProjectTranscript;
let semanticChecks = 0;
for (let seed = 0; seed < 200; seed++) {
  const make = (i, shift) => ({id: `id${(i+shift)%13}`, role: 'assistant', createdAt: new Date(1700000000000 + Math.floor((i+shift)/3)*1000).toISOString()});
  const a = Array.from({length: seed%17}, (_,i) => make(i, 0));
  const b = Array.from({length: seed%11}, (_,i) => make(i, seed%7));
  if (seed%4 === 0) b.reverse();
  if (seed%5 === 0 && b[0]) b[0].createdAt = 'invalid';
  if (seed%6 === 0 && b[0]) b[0].clientRequestId = 'request';
  if (JSON.stringify(optimizedMerge(a,b)) !== JSON.stringify(originalMerge(a,b))) throw Error(`merge differs at ${seed}`);
  semanticChecks++;
}
function measure(fn) {
  for (let i = 0; i < 3; i++) fn();
  const samples = [];
  for (let i = 0; i < 10; i++) {
    const start = performance.now(); fn(); samples.push(performance.now() - start);
  }
  samples.sort((a,b) => a-b);
  return { p50_ms: (samples[4] + samples[5]) / 2, p95_ms: samples[9] };
}
const results = [];
for (const count of [10, 100, 1000, 2000]) {
  const durable = Array.from({length: count}, (_, i) => ({id: `old${i}`, role: 'assistant', content: 'x'.repeat(2048), createdAt: '2026-09-19T00:00:00Z'}));
  const live = Array.from({length: count}, (_, i) => ({id: `live${i}`, role: 'assistant', content: 'x', timelineOrder: i, timelineRevision: 1, status: i === count-1 ? 'running' : 'completed', createdAt: '2026-09-20T00:00:00Z'}));
  const runtime = {timeline: {runId: 'probe', revision: 1, messages: live, status: 'running'}};
  const patch = {version: 2, runId: 'probe', revision: 2, status: 'running', updates: [{id: `live${count-1}`, baseRevision: 1, append: {content: 'a'}, set: {timelineRevision: 2}}]};
  if (JSON.stringify(fastProject(durable,runtime)) !== JSON.stringify(wbcProjectTranscript(durable,runtime))) throw Error('projection differs');
  results.push({records_per_array: count, apply_one_delta: measure(() => wbcApplyTimeline(runtime, patch)),
    project_one_live_record_over_history: measure(() => wbcProjectTranscript(durable, {...runtime, timeline: {...runtime.timeline, messages: live.slice(-1)}})),
    project_equal_sized_history_and_run: measure(() => wbcProjectTranscript(durable, runtime)),
    prototype_linear_merge_projection: measure(() => fastProject(durable, runtime)), fixture_projection_equal: true});
}
const report = {node: process.version, samples: 10, warmups: 3, semanticChecks, scope: 'synthetic pure functions, no browser/DOM; prototype only', results};
writeFileSync(new URL('frontend-probe.json', import.meta.url), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
