# SimpleXNG 延迟与质量记录

> 日期：2026-08-13
> 范围：`WebSearch` 的搜索后端路由与 SimpleXNG 多阶段流水线
> 约束：只允许缩短关键路径，不改变搜索结果、抓取数量、过滤提示词、综合提示词或最终输出质量。

## 本次变更

1. 暂时禁用 WebSearch 对官方 DeepSeek Responses 搜索的候选探测和调用；适配器代码继续保留，但生产路由固定进入 SimpleXNG。
2. 保留原有 SimpleXNG 行为：单查询、最多 15 条去重结果、抓取前 8 条、原 LLM 过滤、原 LLM 综合。
3. 将互不依赖的“网页正文抓取”和“基于 title/URL/snippet 的 LLM 过滤”并行执行。
4. 综合阶段仍等待两者完成，并以原来的来源顺序、网页正文和提示词生成回答。
5. 根据首个真实运行反馈，WebSearch 现在保留原综合答案，并附带已经抓取的来源摘录；工具契约明确要求直接使用结果，不得为已列出的 URL 默认再次调用 WebFetch。

关键路径由：

```text
search + fetch + filter + synthesize
```

变为：

```text
search + max(fetch, filter) + synthesize
```

理论上节省 `min(fetch, filter)`；不减少任何原有工作量，因此不以质量换时间。

## 质量不变契约

离线固定夹具同时运行优化前的串行调度和优化后的并行调度，并要求：

- 最终工具输出逐字节一致；
- 进入综合阶段的 topic、来源对象、来源顺序和正文逐项一致；
- 搜索结果数、抓取数和过滤保留数一致；
- 不访问网络，不读取真实凭据，不调用真实模型。

生产 trace 新增 `simplexng_prepare_sources`，记录：

- `mode`；
- `fetch_ms`；
- `filter_ms`；
- `serial_equivalent_ms`；
- `overlap_saved_ms`；
- `quality_contract=byte_identical`。

## 可重复命令

```bash
uv run python -m cyrene.observability.simplexng_performance_benchmark \
  --repeats 5 \
  --output-dir output/performance
```

输出：

- `output/performance/simplexng-performance.json`
- `output/performance/simplexng-performance.md`

## 本次测量

固定延迟夹具，重复 7 次后取中位数：

| 指标 | 结果 |
|---|---:|
| 优化前串行调度 | 133.801 ms |
| 优化后并行调度 | 93.379 ms |
| 中位数节省 | 40.423 ms |
| 延迟下降 | 30.21% |
| 最终输出逐字节一致 | 是 |
| 综合阶段输入逐项一致 | 是 |
| 质量契约通过 | 是 |

该夹具中的阶段延迟为搜索 10 ms、抓取 60 ms、过滤 40 ms、综合 20 ms，目的是稳定验证编排收益，不代表真实网络服务延迟。真实搜索的节省量由 `overlap_saved_ms` 继续记录，理论上接近抓取和过滤两阶段中较短者。

质量指纹：

- 最终输出 SHA-256：`bac22ce46668f36b19ea079f9cfb3e4aaa46f79fc6e1468e7d63068f0f4f375f`
- 综合输入 SHA-256：`9f46c168e495763529ea4f8b3b3277be3fccf899425ee7bfd8c815bfc4f25360`

### 真实运行反馈与修正

首个切换后的真实 Run（`run_3e43804701c24af18da112ec2b5c34bb`）暴露了离线阶段基准没有覆盖的端到端回归：

- Agent 并发调用 2 次 WebSearch，墙钟受较慢的一次 27.4 秒控制；
- 随后增加一次模型决策、一次 WebFetch（返回 9,715 字符）、再一次模型决策；
- 整个 Run 为 39.8 秒；
- SimpleXNG 实际已经抓取 5 个网页，但只把很短的综合答案返回给 Agent，且旧工具描述称其只返回链接，诱导了重复抓取。

修正后，WebSearch 返回“原综合答案 + 每个相关来源最多 1,500 字符的已抓取摘录”。这是对已获取证据的本地投影，不增加网络或模型调用；目的是省去一次 WebFetch 和围绕它的额外模型回合。是否在真实模型上完全消除重复抓取，继续通过工具调用 trace 验证。

## 未改变的部分

- 没有减少网页抓取数量；
- 没有缩短 HTTP 超时；
- 没有修改搜索语言、安全搜索或排序；
- 没有修改过滤规则和提示词；
- 没有修改综合规则、提示词或上下文截断长度；
- 没有删除原综合答案；只追加已经抓取的来源证据；
- 没有增加缓存，因此不会返回旧结果；
- 没有将 LLM 过滤替换为启发式规则。

这些优化仍可在后续进行，但必须建立新的质量评估标准，不能归入本次“质量严格不变”的改造。
