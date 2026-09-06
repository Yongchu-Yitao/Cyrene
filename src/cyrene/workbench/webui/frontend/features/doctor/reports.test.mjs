import test from 'node:test';
import assert from 'node:assert/strict';
import { cachedReport, rememberReport, loadReport } from './reports.mjs';

test('cached findings stay separated by conversation and language',()=>{
 const scope={chat_id:'cache-test'}; const report={id:'one',analysis:{status:'idle'}};
 rememberReport(scope,'zh',report);
 assert.equal(cachedReport(scope,'zh'),report);
 assert.equal(cachedReport(scope,'en'),null);
 assert.equal(cachedReport({chat_id:'other'},'zh'),null);
});
test('prefetch and open share a request; previous findings survive refresh errors',async()=>{
 const scope={chat_id:'shared'};let resolve;let count=0;
 const request=()=>{count++;return new Promise(r=>{resolve=r;});};
 const first=loadReport(scope,'zh',request);const second=loadReport(scope,'zh',request);
 assert.equal(first,second);assert.equal(count,1);
 const report={id:'shared-report',analysis:{status:'idle'}};resolve(report);await first;
 await assert.rejects(loadReport(scope,'zh',()=>Promise.reject(new Error('offline'))));
 assert.equal(cachedReport(scope,'zh'),report);
});
test('reopening active analysis resumes its report instead of creating another scan',async()=>{
 const scope={chat_id:'active'};const report={id:'active-report',analysis:{status:'running'}};
 rememberReport(scope,'en',report);
 await loadReport(scope,'en',async(path,method)=>{assert.equal(path,'reports/active-report');assert.equal(method,undefined);return report;});
});
