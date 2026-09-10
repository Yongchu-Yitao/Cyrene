# 模型调用故障实验记录

实验时间：2026-09-10 19:21–19:26（Asia/Shanghai）。
目标会话：`wbchat_2f2984cf67`。本次实验未修改或重试原会话，也未执行模型返回的工具调用。

## 结论

16 次真实模型请求全部完成，每个模型 8 次；耗时约 0.18–1.97 秒。未复现原先的 ReadTimeout、ConnectError 或 RemoteProtocolError。
当前接口、凭据和 Cyrene 解析调用链可用；约 1.1 万 token 上下文、工具定义、连接复用和不设输出上限均有成功样本。
这削弱了“固定配置错误”“单纯输入长度导致失败”“必现解析器不兼容”的解释，但不能证明历史故障已消失，也不能证明必定由网络波动引起。
历史证据仍指向传输层故障；服务端停滞/断开与中间网络故障尚无法区分。

## 实验设置

- 读取 Cyrene-dev 现有配置；两个连接均 `use_proxy=false`，全局代理未启用。没有测试未经配置的代理路径。
- MiniMax 使用 `https://api.minimaxi.com/v1/chat/completions`；MiniCPM 使用 `http://100.100.8.2:2242/v1/chat/completions`。
- 原始 HTTPX 组：默认地址选择、强制 IPv4、长合成上下文、不传 `max_tokens` 各一次，每种模型共四次。禁用环境代理；读取超时 180 秒，连接超时 35 秒，总实验请求期限 215 秒。
- Cyrene 组：使用项目的 `complete_model`、`ModelHttpClients`、IPv4 fallback 和流解析器；短请求、长合成上下文、工具调用定义、复用连接短请求各一次。读取超时 180 秒，总请求期限 215 秒。
- 默认短提示为“你好。请只回复‘你好’。”；长上下文由 1800 行中性英文测试文本组成，未重放原始私有上下文。
- HTTPX 组通常限制输出 256 token；Cyrene 组限制 512 token；最后两次 HTTPX 请求省略该参数。均正常 stop/tool_calls 结束，未触发输出长度截断。
- 工具实验只提供 `diagnostic_echo` 的 JSON schema，验证返回与解析，不实际调用工具。
- 实验脚本只保存时间、状态、长度、usage 和协议诊断，不保存密钥或模型正文。

## 结果

| 模型 | 实验 | 总耗时（秒） | 结束原因 | 结果 |
|---|---|---:|---|---|
| minicpm5-2b-q8 | HTTPX / dual / short | 1.113 | stop | 成功 |
| MiniMax-M3 | HTTPX / dual / short | 1.967 | stop | 成功 |
| minicpm5-2b-q8 | HTTPX / ipv4 / short | 0.226 | stop | 成功 |
| MiniMax-M3 | HTTPX / ipv4 / short | 1.588 | stop | 成功 |
| minicpm5-2b-q8 | HTTPX / dual / padded | 0.969 | stop | 成功 |
| MiniMax-M3 | HTTPX / dual / padded | 1.537 | stop | 成功 |
| minicpm5-2b-q8 | HTTPX / dual / uncapped | 0.248 | stop | 成功 |
| MiniMax-M3 | HTTPX / dual / uncapped | 1.097 | stop | 成功 |
| minicpm5-2b-q8 | Cyrene 连接池 / short | 0.869 | stop | 成功 |
| MiniMax-M3 | Cyrene 连接池 / short | 1.224 | stop | 成功 |
| minicpm5-2b-q8 | Cyrene 连接池 / padded | 0.901 | stop | 成功 |
| MiniMax-M3 | Cyrene 连接池 / padded | 1.242 | stop | 成功 |
| minicpm5-2b-q8 | Cyrene 连接池 / tools | 0.660 | tool_calls | 成功 |
| minicpm5-2b-q8 | Cyrene 连接池 / short-repeat | 0.179 | stop | 成功 |
| MiniMax-M3 | Cyrene 连接池 / tools | 1.187 | tool_calls | 成功 |
| MiniMax-M3 | Cyrene 连接池 / short-repeat | 1.280 | stop | 成功 |

Cyrene 长上下文组实际输入：MiniCPM 10,833 token，MiniMax 10,986 token，与历史请求估算的 11,382 token 接近；这是长度对照，不能代替原始上下文的内容对照。

MiniMax 本次多次没有 `[DONE]`，但有 `finish_reason=stop` 或 `tool_calls`。Cyrene 正确将其判定为完整流；因此“没有 `[DONE]`”单独不是这次历史故障的解释。
MiniMax 本次 DNS 返回五个 IPv4 地址，没有 IPv6 地址；强制 IPv4 与默认路径均成功。不能据此倒推故障时的 DNS/地址族状态。
MiniCPM 地址当前经 `utun4` 接口路由；未对隧道类型或故障状态作额外假定。

## 历史故障与本次实验的区别

- 17:09:13：MiniCPM 返回 HTTP 200、278 个数据块后 ReadTimeout；没有 finish_reason，也没有正常流结束事件。
- 17:12:48：MiniMax ConnectError，之前已经尝试过 IPv4 fallback。
- 17:13:05：MiniMax 的后续请求返回 HTTP 200、9 个数据块后 RemoteProtocolError；同样未收到 finish_reason。
- 17:14:55：应用关闭保留未完成检查点，后来造成重试被旧 run 拦截；这是另一个已修复的生命周期问题。

历史来源：`/Users/syw/Library/Application Support/Cyrene-dev/data/logs/cyrene.log.2026-09-10_17`，关键行 1342、1548、1551、1556。

## 尚未验证及下一步

这是短时间窗口中的有限样本，不是稳定性保证。没有重放完整 Agent 上下文、所有工具 schema、原始流长度或当时并发负载，也没有读取模型服务器日志。因此不能排除内容/负载相关的服务端缺陷、长时间连接问题或间歇网络故障。
若再次发生，应对齐同一时刻的客户端请求 ID、目标地址、最后数据块时间、异常 cause，以及 MiniCPM 服务端/网关日志；MiniMax 可通过请求 ID 向提供方定位。不要仅靠增加超时或永久强制 IPv4声称解决根因。

## 复现

在项目根目录，分别执行（密钥从现有加密配置读取，不需要填入命令）：

```sh
CYRENE_USER_DATA_DIR='/Users/syw/Library/Application Support/Cyrene-dev' CYRENE_BASE_DIR='/Users/syw/Library/Application Support/Cyrene-dev' uv run python project-notes/experiments/model-network-2026-09-10/probe.py dual short
CYRENE_USER_DATA_DIR='/Users/syw/Library/Application Support/Cyrene-dev' CYRENE_BASE_DIR='/Users/syw/Library/Application Support/Cyrene-dev' uv run python project-notes/experiments/model-network-2026-09-10/probe.py ipv4 short
CYRENE_USER_DATA_DIR='/Users/syw/Library/Application Support/Cyrene-dev' CYRENE_BASE_DIR='/Users/syw/Library/Application Support/Cyrene-dev' uv run python project-notes/experiments/model-network-2026-09-10/probe.py dual padded
CYRENE_USER_DATA_DIR='/Users/syw/Library/Application Support/Cyrene-dev' CYRENE_BASE_DIR='/Users/syw/Library/Application Support/Cyrene-dev' uv run python project-notes/experiments/model-network-2026-09-10/probe.py dual uncapped
CYRENE_USER_DATA_DIR='/Users/syw/Library/Application Support/Cyrene-dev' CYRENE_BASE_DIR='/Users/syw/Library/Application Support/Cyrene-dev' uv run python project-notes/experiments/model-network-2026-09-10/app_probe.py
```

重跑会产生新的模型用量，并在 JSONL 中追加结果。
