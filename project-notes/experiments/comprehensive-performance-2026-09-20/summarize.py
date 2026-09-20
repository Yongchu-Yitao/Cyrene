"""Validate all artifacts and produce the expanded experimental report."""
import hashlib
import json
from pathlib import Path
import statistics
from xml.etree import ElementTree

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
read=lambda name:json.loads((HERE/name).read_text())
median=statistics.median
suite=read('suite-status.json')
assert all(r['exit_code']==0 for r in suite['runs'])
assert not suite['changed_sources']
changed=[p for p,h in suite['source_before'].items() if hashlib.sha256((REPO/p).read_bytes()).hexdigest()!=h]
followup=read('changed-source-tests-status.json')
assert followup['exit_code']==0
followup_cases=list(ElementTree.parse(HERE/'changed-source-tests.xml').iter('testcase'))
assert not any(c.find('failure') is not None or c.find('error') is not None for c in followup_cases)
cases=list(ElementTree.parse(HERE/'pytest-full.xml').iter('testcase'))
assert len(cases)==3160
assert not any(c.find('failure') is not None or c.find('error') is not None for c in cases)
skips=[c for c in cases if c.find('skipped') is not None]
assert len(skips)==5
for filename,count in [('frontend-tests.log',172),('electron-tests.log',133)]:
    log=(HERE/filename).read_text()
    assert f'pass {count}' in log and 'fail 0' in log

feature_rows=[]
for run in suite['runs']:
    name=run['scenario'];report=read(name+'.json');assert report['quality']['preserved']
    if 'cases' in report:
        for c in report['cases']:
            feature_rows.append(f"| {c['id']} | {c['primary_metric']} | {c['primary_ms']:.2f} | 通过 |")
    else:
        feature_rows.append(f"| {name} | 三 worker × 两轮总时间的三次中位数 | {median(s['wall_ms'] for s in report['samples']):.2f} | 通过 |")
matrices=[read(f) for f in ['agent-matrix.json','agent-matrix-2.json','agent-matrix-3.json']]
assert all(s['checks_passed'] for matrix in matrices for s in matrix)
agent_rows=[]
for i in range(5):
    samples=[m[i] for m in matrices];s=samples[0]
    agent_rows.append(f"| {s['workers']} | {s['turns']} | {s['steps']} | {s['chunks']} | {median(x['wall_ms'] for x in samples):.2f} | {median(median(t['wall_ms'] for t in x['per_turn']) for x in samples):.2f} | {median(x['loop_lag_p95_ms'] for x in samples):.2f} |")
for repetition in (2,3):
    assert read(f'agent-matrix-{repetition}-shutdown.json')['thread_names_after_asyncio_shutdown']==['MainThread']
browser=read('browser-results.json')
assert browser['streaming']['checks']
browser_rows=[]
for r in browser['results']:
    assert all(s['checks'] for s in r['samples'])
    browser_rows.append(f"| {r['history']} | {r['active']} | {r['projectionMs']:.2f} | {r['mountCommitMs']:.2f} | {r['mountLayoutMs']:.2f} | {r['updateCommitMs']:.2f} | {r['updateLayoutMs']:.2f} |")
long=[m[3] for m in matrices]
first=median(median(t['wall_ms'] for t in s['per_turn'][:5]) for s in long)
last=median(median(t['wall_ms'] for t in s['per_turn'][-5:]) for s in long)
largest=browser['results'][-1]
turn_count=sum(s['workers']*s['turns'] for m in matrices for s in m)
delta_count=sum(s['workers']*s['turns']*s['chunks'] for m in matrices for s in m)
session_count=sum(s['workers'] for m in matrices for s in m)

report=f'''# Cyrene 全面性能与正确性扩展测试

日期：2026-09-20。平台：Linux x86_64 / Ryzen 7 9700X。仅新增实验产物，没有修改产品代码。

## 结论

本轮可在当前环境执行的 Python、前端和 Electron 单元／集成测试全部通过；九组确定性性能基准也全部完成且输出校验通过。
真实 AgentSession、多轮历史、并发、流式事件、持久会话重新打开和生产 React 消息组件均补充了实际测量。

主要性能压力仍是长时间线的消息投影与渲染、多会话时的排队，以及终端的大批量持久化／屏幕解析。
这并不证明它们占每个真实用户任务的大部分时间：没有调用真实云端模型，也没有测用户的实际工作负载。

## 功能回归

| 测试入口 | 结果 | 说明 |
|---|---:|---|
| Python 全量 pytest | 3155 通过、5 跳过、0 失败 | 112.57 秒，完整 JUnit 与日志保留 |
| Web UI npm test | 172 通过、0 失败 | 包括架构、聊天、doctor、布局测试 |
| Electron npm test | 133 通过、0 失败 | 桌面、浏览器与自动化相关 Node 测试 |

合计 3,460 项通过，5 项因平台不适用跳过。跳过项为两个 macOS 隔离执行测试、一个 Windows 进程句柄测试和两个 Windows PowerShell 更新脚本测试。
本机通过不等于 Windows/macOS/Android 原生验证。

基准完成后，工作区出现其他并行修改；因此另补跑了 {len(followup_cases)} 项涉及 subagent、remote、i18n 和前端的 Python 回归，全部通过。
该数字与全量回归有重叠，不计入新增唯一测试数。各批次对应各自执行时的源码，不能把首批全量通过解释为所有后来修改都经过完整全量回归。

全量 Python 测试包含上下文树／压缩／隔离、权限、subagent、取消与恢复、流式协议回放、事件去重、慢终端持久化、数据库锁错误、终结落盘和恢复等。
没有用“跳过失败场景”获得全通过；测试自身的平台 skip 保留。

## 现有性能基准

每组重复三次，独立进程和临时数据目录，组间串行，避免测试进程互相竞争 CPU。
feature 组每次使用默认 3 workers × 2 rounds。表中数字单位均为 ms，但不同指标不能直接比较。

| 场景 | 指标 | ms | 输出校验 |
|---|---|---:|---|
{chr(10).join(feature_rows)}

chat 的三个场景分别为 2 会话 × 32 工具、24 会话 × 12 工具、12 会话 × 40 工具，每会话三轮。
chat 是真实 Workbench ChatRun／事件存储／NDJSON 等管线，使用模拟模型／工具执行，不等同于下面的真实 AgentSession 循环。
search 有固定模拟搜索和抓取等待，不能把全部耗时归因于本地 CPU。
所有 ideal cache 指标只是固定输入理论复用，本文不把它们当作真实缓存命中率。

终端基准本轮正常完成：三次，每次六轮，每轮 2 MiB，累计 36 MiB。
每轮落盘字节与回放字节均匹配。一个代表轮次的首段发布 0.034 ms、批量 flush 652.65 ms、屏幕解析 1143.61 ms。
代表轮次不是整组中位数；组总时间包含全部六轮，不能把 11 秒误解为首次显示延迟。
此前 45 秒诊断未完成的问题没有在本轮独立场景 100 秒期限内复现，不能据此确认此前超时根因。

## 真实 agent 扩展矩阵

模型每次固定等待 10 ms，工具返回 8 KiB；使用真实 AgentSession、SQLite 与事件发布，绕过外部模型和实际副作用工具。
每种场景三次独立进程重复。除 40 轮长对话外，每轮有 5 次工具调用和 6 次模型调用；40 轮场景每轮 1 次工具和 2 次模型。
每轮流式回复 100 个分片，最后一行单独验证 5,000 个分片。

| 并发会话 | 每会话轮数 | 每轮工具数 | 每轮分片 | 整组总时间 ms | 单轮中位数 ms | 主循环采样 lag p95 ms |
|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(agent_rows)}

累计 {session_count} 个会话、{turn_count} 轮、{delta_count:,} 个流式分片，全部校验最终内容、模型／工具调用次数、片段顺序和 idle 终态。
所有会话关闭后重新打开，验证最后一轮持久回复一致，且不意外重新调用模型。
这属于正常关闭后恢复验证；异常中断和锁错误由现有功能测试覆盖，没有做断电或 kill -9 的真实故障实验。

40 轮对话的前五轮耗时中位数约 {first:.2f} ms，后五轮约 {last:.2f} ms。
输入估算从约 3,751 tokens 增长至 96,571 tokens；增加耗时与历史增长一致，但未通过单因素拆分证明所有增量都来自上下文读取。
这里使用合成历史，没有验证更长对话的自动压缩之后的稳态性能。

12 会话同时执行时单轮延迟明显上升；这说明本地共享资源和调度会影响响应时间，不意味着并发吞吐量一定变差。
lag 来自每 5 ms 的主 asyncio 循环采样，不是所有 session 工作线程各自事件循环的测量。

该矩阵进程 RSS 历史峰值约 57 MiB，含前面案例遗留的高水位，不能解释为单会话内存或整个 Cyrene/Electron 内存。
会话 close 后存活的 asyncio executor 线程会被复用；第二、第三次实验在 asyncio.run 退出后都只剩 MainThread。
这排除了本次短测中未退出的后台线程，但不是长时间内存泄漏证明。

## 真实浏览器生产组件

在 Codex 内置 Chromium 152 中运行当前生产 WbcTranscript／WbcAssistantMessage、React production、marked、DOMPurify 和聊天 CSS。
数据为固定两段 Markdown，外部宿主服务使用本地桩（i18n、事件订阅、浏览器图标和语音状态），无模型请求。
它是生产消息组件的隔离页面，不是完整 Cyrene Workbench/Electron 端到端启动。

每种规模三次挂载、每次五次真实 wbcApplyTimeline 增量更新。每次核对消息数量及末尾增量内容。
提交时间使用 flushSync 包围组件渲染；布局时间额外强制读取 scrollHeight，包含同步布局成本。

| 历史 | 运行记录 | 单独投影 ms | 挂载提交 ms | 挂载含布局 ms | 增量提交 ms | 增量含布局 ms |
|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(browser_rows)}

最大场景包含 {largest['samples'][-1]['domNodes']:,} 个后代 DOM 元素。
上述单独投影是额外诊断，WbcTranscript 提交内本身还会调用投影；不能把两列相加当作正常一次提交耗时。
布局时间不是屏幕实际显示时间。测量不包含导航、后台 API、完整页面其他面板或桌面进程。

另测试 100 条历史＋一条运行回复，以计划 10 ms 间隔连续应用 100 个分片，结束后所有 token 都保留。
实际整体历时 {browser['streaming']['elapsedMs']:.2f} ms，定时采样最大间隔 {browser['streaming']['maxTickGap']:.2f} ms；
由于每次提交及任务调度也耗时，计划 10 ms 不代表实测 100 tokens/s，更不代表模型 tokens/s。

首轮浏览器探针缺少完整聊天样式，并用双 requestAnimationFrame 近似显示时间，结果出现约 1 秒等待。
该组保存在 browser-preliminary.json，仅作为诊断证据。最终补齐聊天样式并改成同步布局计时后重新执行。
最终报告不使用首轮 frame 等待作为真实 UI 延迟或 FPS；也不把 Node VM 的绝对耗时当成 Chromium 页面耗时。

## 本轮未覆盖

- 真实云端模型首 token、网络／代理波动、真实工具外部服务。
- 不同机器实物 A/B、内存压力／swap、磁盘限速、GPU 推理和移动端热降频。
- 完整 Electron Workbench 的真实任务录制回放、正常前台帧率、启动 RSS、小时级 soak。
- 真实断电、进程强杀后的副作用恢复及跨平台原生测试。

因此可以说“本机现有回归与本次扩展基准全部通过”，不能说“所有平台、所有负载、所有故障都已验证”。

## 后续优先级

1. 优先研究大时间线的消息投影和 React 更新成本，要求保持消息顺序、去重、流式终态和历史访问语义。
2. 对真实长会话记录完整阶段时间，区分上下文准备、外部等待、工具、持久化、前端提交。
3. 终端单独剖析 flush 与 pyte 屏幕解析；不通过丢字节、减少历史或放宽恢复保证换速度。
4. 本轮没有应用任何优化，不为之前已知不等价原型背书。

## 复现和证据

目录：[experiments/comprehensive-performance-2026-09-20](experiments/comprehensive-performance-2026-09-20)。

```bash
# 性能组；全部使用临时配置目录
.venv/bin/python project-notes/experiments/comprehensive-performance-2026-09-20/run_suites.py
# 真实 agent 矩阵，三次分别传空参数、2、3
.venv/bin/python project-notes/experiments/comprehensive-performance-2026-09-20/agent_matrix.py
.venv/bin/python project-notes/experiments/comprehensive-performance-2026-09-20/agent_matrix.py 2
.venv/bin/python project-notes/experiments/comprehensive-performance-2026-09-20/agent_matrix.py 3
# 构建并启动本地组件页面，在浏览器点击 Run comprehensive browser test
node project-notes/experiments/comprehensive-performance-2026-09-20/build_browser.mjs
.venv/bin/python project-notes/experiments/comprehensive-performance-2026-09-20/browser_server.py
# 汇总（还需存在本轮全量测试日志）
.venv/bin/python project-notes/experiments/comprehensive-performance-2026-09-20/summarize.py
```

pytest-full.xml／pytest-full.log、frontend-tests.log、electron-tests.log 保存功能验证；
suite-status.json 保存每组退出状态、时限及生产源码哈希；各场景 JSON 保存原始样本；
agent-matrix*.json 保存逐轮耗时、输入规模、事件计数和恢复检查；browser-results.json 保存最终生产组件样本。
性能组执行前后受监测的生产 .py/.jsx/.mjs 文件哈希一致。此后报告生成时发现以下文件有其他并行修改：
{chr(10).join('- '+p for p in changed)}

补验过程发生变化的文件：{followup['changed_during_test']}。
suite-status.json 与 changed-source-tests-status.json 分别保存每批源码哈希；性能结果属于当时测量版本，不是跨版本 A/B 优化证据。
'''
target=HERE.parents[1]/'comprehensive-performance-test-2026-09-20.zh-CN.md'
target.write_text(report)
(HERE/'verification.json').write_text(json.dumps({'python_pass':3155,'python_skip':5,'frontend_pass':172,'electron_pass':133,
    'performance_groups':9,'all_checks_passed':True,'agent_sessions':session_count,'agent_turns':turn_count,
    'agent_deltas':delta_count,'changed_production_sources':changed},indent=2)+'\n')
print(target)
print('agent turns',turn_count,'deltas',delta_count)
