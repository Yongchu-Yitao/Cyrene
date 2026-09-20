"""Check completed paired trials and create a reproducible research readout."""
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
report = json.loads((HERE/"results.json").read_text())
assert len(report["trials"]) == 6
assert report["source_unchanged"]
rows = {}
for key in report["trials"][0]["results"]:
    row = {}
    for mode in ("baseline", "contended"):
        values = [t["results"][key] for t in report["trials"] if t["mode"] == mode]
        row[mode] = {"wall_ms": median(v["wall_ms"] for v in values),
                     "cpu_ms": median(v["cpu_ms"] for v in values),
                     "wall_range_ms": [min(v["wall_ms"] for v in values), max(v["wall_ms"] for v in values)]}
        if key.startswith("agent"):
            row[mode]["prepare_total_ms"] = median(v["prepare_total_ms"] for v in values)
    row["wall_ratio"] = row["contended"]["wall_ms"] / row["baseline"]["wall_ms"]
    rows[key] = row
for key in ("agent_10_tools_wait_0ms", "agent_10_tools_wait_100ms"):
    values = [t["results"][key] for t in report["trials"]]
    assert all(v["checks_passed"] and v["model_calls"] == 11 and v["tool_calls"] == 10 for v in values)
    assert len({v["answer_sha256"] for v in values}) == 1
    assert all(v["event_counts"] == values[0]["event_counts"] for v in values)
(HERE/"summary.json").write_text(json.dumps(rows, indent=2)+"\n")

labels = {
    "calibration": "CPU 校准循环",
    "context_read_project_estimate_1000": "读取／投影／估算 1,000 条 × 2 KiB 上下文",
    "timeline_complete_5000": "5,000 条运行记录，处理一条完成事件",
    "frontend_1000_1000": "前端合并 1,000 条历史＋1,000 条运行记录",
    "agent_10_tools_wait_0ms": "真实 AgentSession：11 次即时模型返回＋10 次工具",
    "agent_10_tools_wait_100ms": "同上，每次模型返回前固定等待 100 ms",
}
table = ["| 场景 | 无人工竞争 ms | 三进程竞争 ms | 倍数 |", "|---|---:|---:|---:|"]
for key, label in labels.items():
    row = rows[key]
    table.append(f"| {label} | {row['baseline']['wall_ms']:.2f} | {row['contended']['wall_ms']:.2f} | {row['wall_ratio']:.2f}× |")

cal = rows["calibration"]
agent = rows["agent_10_tools_wait_0ms"]
wait = rows["agent_10_tools_wait_100ms"]
front = rows["frontend_1000_1000"]
content = f"""# Cyrene 设备算力影响：受控实验

日期：2026-09-20。仅新增隔离实验和报告，未改产品代码。全部数字来自本轮实际执行。

## 结论

降低可获得的 CPU 时间，会同时拖慢 Cyrene 的本地 agent 循环和消息显示计算。
无人工竞争与同核三个竞争进程相比，CPU 校准慢 {cal['wall_ratio']:.2f} 倍；
真实 AgentSession 的十次固定工具循环慢 {agent['wall_ratio']:.2f} 倍；
前端大规模消息投影慢 {front['wall_ratio']:.2f} 倍。
加入固定模型等待后，整个 agent 循环只慢 {wait['wall_ratio']:.2f} 倍。
因此硬件影响大小取决于本地工作与远程等待的比例，不能把局部四倍变慢直接解释为整个产品四倍变慢。

## 方法

- Linux x86_64，AMD Ryzen 7 9700X，Python {report['python']}，Node {report['node']}。
- 被测进程和新建线程绑定逻辑 CPU {report['cpu']}。基线没有人工竞争；干预组增加三个同核忙循环进程。
- 这模拟可用 CPU 份额减少和机器繁忙，不是严格 25% 配额，也不是四倍慢 CPU 的完整硬件模拟。
- 三对独立进程，执行顺序 AB、BA、AB。CPU 校准同时记录墙钟和进程 CPU 时间。
- 每个微基准预热一次，再重复 5／7／15 次；报告为各进程中位数的跨三次中位数。
- AgentSession 每个进程先运行独立暖身会话；正式场景各执行一次，因此每种场景／模式共三次。
- 模型和工具为固定异步插件，工具返回 8 KiB 字符串；运行真实上下文准备、插件调用、工具状态转换、SQLite 存储和 session 事件。
- 对照场景的 11 次模型调用每次 asyncio.sleep(0.1)，总计划等待 1,100 ms。该等待只模拟固定外部等待，不代表真实 API 延迟。
- CYRENE_BASE_DIR 与数据库均放入临时目录；没有真实模型请求、凭据读取或生产数据库访问。
- 前端运行当前生产 JSX 经 esbuild 转换后的纯函数，置于 Node VM；没有 DOM、React commit 或 Electron 页面测量。
- 七个关键生产源码文件在六次测量前后 SHA-256 相同。工作区原本存在其他修改，本报告针对当时工作区。

## 测量结果

{chr(10).join(table)}

CPU 校准的进程 CPU 时间：基线 {cal['baseline']['cpu_ms']:.2f} ms，竞争组 {cal['contended']['cpu_ms']:.2f} ms。
计算任务所消耗的 CPU 时间接近，而墙钟明显拉长，符合调度等待造成的干预效果。

即时模型 agent 的输入准备累计时间：基线 {agent['baseline']['prepare_total_ms']:.2f} ms，竞争组 {agent['contended']['prepare_total_ms']:.2f} ms。
输入准备只是全循环成本的一部分，其余包含事件、Hook、工具编排、序列化和持久化等，不能全部归因为上下文处理。

## 输出检查

全部 12 次正式 agent 运行均验证：11 次模型调用、10 次按 1 至 10 顺序执行的工具、两条流式 delta、相同最终回复、结束时 idle。
跨模式最终回复 SHA-256 与各类 session 事件数量一致。前端验证所有 fixture 消息正文保留、重复投影输出一致。
这些检查排除本实验中因为少做工作而更快；不构成所有产品功能等价或故障恢复验证。

首次烟雾运行的前端断言曾错误地假设投影只含输入消息；生产代码还会生成运行状态记录。
修正为逐条验证输入消息存在且正文一致后，重新开始完整六次正式实验。没有为通过实验改变产品逻辑。

## 如何解释

1. CPU 可用量降低确实拖慢了真实 agent 状态循环，不只是界面动画。
2. 在本次小规模即时工具任务中，正常本地循环仍只有约 {agent['baseline']['wall_ms']:.0f} ms。
   这不能解释任意数秒或数十秒的等待；真实网络、模型推理、插件初始化和实际工具仍需单独计时。
3. 前端 1,000＋1,000 记录的一次纯投影达到约 {front['baseline']['wall_ms']:.0f} ms，竞争时约 {front['contended']['wall_ms']:.0f} ms。
   它是长运行卡顿的明确候选；不是“每个普通聊天 token 都要花这个时间”，也未测实际页面调用频率。
4. 1,000 条历史＋1 条活动记录的纯函数基线很小，说明活动记录规模也是关键变量。
5. 本轮没有改变磁盘、内存容量、GPU 或网络，不能对它们的独立影响给出数值。
   数据库使用默认临时目录所在文件系统；运行时间不代表硬盘断电持久化延迟。
6. 亚毫秒微基准可能在一次调度时间片内完成；其单次中位数不能可靠反映 CPU 竞争。
   因此主要结论采用长任务、校准和多轮 agent 结果，不把短任务“几乎不变”解释为与设备无关。
7. 三次重复用于初步复现，不足以报告稳定 p95/p99 或跨设备速度承诺。
8. 本轮未应用任何优化原型，不涉及之前原型是否全功能等价的问题。

## 复现

在仓库根目录运行：

```bash
.venv/bin/python project-notes/experiments/device-performance-2026-09-20/run.py
.venv/bin/python project-notes/experiments/device-performance-2026-09-20/summarize.py
```

run.py 会短暂在一个逻辑核制造竞争，正常完成或异常时均清理其三个子进程。
原始逐次样本、CPU 时间、事件计数和源码哈希在 [results.json](experiments/device-performance-2026-09-20/results.json)；
汇总在 [summary.json](experiments/device-performance-2026-09-20/summary.json)。
后续应优先测量真实长运行页面的投影耗时与长任务，再决定是否实施保持语义的算法优化。
"""
path = HERE.parents[1]/"device-performance-experiment-2026-09-20.zh-CN.md"
path.write_text(content)
print("\n".join(table))
print(path)
