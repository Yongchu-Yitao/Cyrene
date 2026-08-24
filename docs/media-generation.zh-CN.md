# 媒体生成

Cyrene 的图片、视频和音乐生成运行在独立的后台媒体子系统中。Agent 只负责提交任务；`MediaDaemon` 在 Agent 回合之外执行生成。一个批次中的所有结果都已作为聊天附件或失败消息写入后，`MediaWakeBridge` 才会以内部系统事件唤醒原会话。

## 执行语义

1. 主 Agent 通过 `media_tools` 中的 `media.generate` 提交 1–8 个任务。
2. `MediaJobManager` 在 SQLite 中原子创建 `media_batches`、`media_jobs` 和一条 `media_wakes` 记录。
3. 独立的 `MediaWorker` 使用带过期时间的租约领取任务；不同任务可并行执行。
4. Provider 输出会先注册为 Cyrene 托管附件，再以稳定消息 ID 写入 Agent session 和 Workbench 聊天。
5. 任务写入聊天成功后才设置 `reported_at`。只有整个批次全部终态并全部 reported，wake 才能从 `watching` 进入 `ready`。
6. `MediaWakeBridge` 等待原聊天不再忙碌，随后派发一次 `system_media_wake`。它不会伪装成用户消息，也不会要求 Agent 轮询任务。

因此，完成顺序固定为：

```text
provider 完成 → 注册附件 → 写入聊天 → 标记 reported → wake ready → 唤醒 Agent
```

工具只支持这一种完成行为：`wake_agent=true` / `attach_then_wake_agent`。不存在同步等待、轮询或终端 watcher 模式。

## 组件

- `MediaJobManager`：持久化批次、任务、重试、租约、结果和 wake 状态。
- `MediaWorker`：解析 Provider、执行生成、下载临时结果、注册托管附件并投影聊天消息。
- `MediaDaemon`：独立管理可动态调整大小的 worker 池。
- `MediaWakeBridge`：在附件可见后，以与 shell wake 相同的忙碌检测和租约结算方式接续 Agent。
- `media_tools`：面向主 Agent 的独立工具包，仅暴露 `media.generate`。

进程重启后，过期的 job/wake 租约可以被重新领取；稳定消息 ID 会防止恢复投影产生重复聊天附件。取消任务会立即写入取消消息；已经提交给远程服务的计算能否真正停止，仍取决于该服务是否提供取消 API。

## 设置

打开“设置 → 媒体生成”统一配置：

- 全局并行数、最大尝试次数、轮询间隔和单个输出下载上限；
- 图片、视频、音乐的默认 Provider；
- Provider 开关、模型名、区域/base URL、API Key；
- ComfyUI MCP server、local/cloud 模式、工具名和每种媒体的 workflow JSON。

API Key 是只写字段：读取设置只返回 `api_key_configured`，留空保存会保留已有密钥，只有显式“清除”才会删除。可移植配置备份不会包含媒体 API Key。Provider base URL 必须使用 HTTPS；只允许 loopback 地址使用 HTTP，并禁止 URL 内嵌凭据。

## Provider 支持

| Provider | 类型 | 默认模型 / 调用链 |
| --- | --- | --- |
| OpenAI GPT Image | 图片生成、参考图编辑、mask 编辑 | `gpt-image-2`；Images API 的 `/images/generations` 与 `/images/edits` |
| Seedream | 图片生成、参考图生成 | `doubao-seedream-5-0-260128`；Ark `/images/generations` |
| Seedance | 文生视频、参考帧视频 | `doubao-seedance-2-0-pro-260128`；创建并轮询 `/contents/generations/tasks` |
| MiniMax | 视频、音乐 | 视频默认 `MiniMax-H3`，音乐默认 `music-3.0`；H3 v2 create/query，音乐 `/v1/music_generation` |
| Google | 图片、视频 | `gemini-3.1-flash-image` 与 `gemini-omni-flash-preview`；使用官方 `google-genai>=2.10.0` SDK，也可显式配置 Veo 模型 |
| ComfyUI MCP | 图片、视频、音乐（取决于 workflow） | 连接已配置的指定 MCP server；本地默认 `run_workflow → job → fetch_outputs` |

模型可用性、价格、区域、限额和审核规则由各服务账户决定。设置中的模型名可覆盖默认值；服务端发布新模型时无需修改 job/wake 架构。

MiniMax 自 2026-08-20 起不再向新用户提供付费音乐生成 API；此前已有付费 API 权限的账户可以继续使用。因而，Cyrene 中的 `music-3.0` 是沿用现有 `/v1/music_generation` 协议的前向兼容配置假设，并不表示每个 MiniMax 账户当前都能使用这个模型。如果账户只开放了文档列出的旧模型（例如 `music-2.6`），请在设置中填写服务端实际开放的精确模型名。

## 参考素材支持

参考素材可以来自当前对话的附件 ID、工作区路径或公开 HTTPS URL，但必须满足所选 Provider 的限制。Cyrene 会在提交可能计费的远端任务前检查媒体类型和 Provider 兼容性。来源于当前对话的附件还会在生成消息中显示为紧凑的“参考素材”区域；输出图片、视频和音频则直接作为可查看或可播放的聊天附件展示。

| Provider / 调用路线 | 可用参考素材 | Cyrene 行为与当前限制 |
| --- | --- | --- |
| OpenAI GPT Image | 图片；可选图片 mask | 一张或多张参考图会把请求切换到 `/images/edits`；mask 只能和编辑请求一起使用。 |
| Seedream | 一张或多张图片 | 通过 Seedream 的 `image` 输入进行参考图生成。 |
| Google Gemini 图片 | 一张或多张本地图片 | 图片与提示词一起传给 Gemini 图片模型。 |
| Google Gemini Omni 视频 | 本地图片，或一个本地短视频 | 两种模式不能混用；目前不支持上传音频参考。短视频编辑最多接收一个本地文件，并仍受 Google 模型、地区和 preview 能力限制。 |
| Google Veo 视频 | 首帧图片和可选尾帧图片 | 保留显式的 `first_frame` / `last_frame` 角色。兼容的 Veo 模型还可接收最多三张 asset 参考图，但 asset 模式不能与首尾帧模式混用。 |
| Seedance | 本地/data URL 或公开 HTTPS 图片、公开 HTTPS 视频，以及所选模型支持时的音频 | 图片可以指定为首帧/尾帧。视频参考必须是公开 HTTPS URL；不支持的本地视频或 data URL 会在提交前被拒绝。 |
| MiniMax H3 视频 | 最多 9 张图片、3 个视频、3 段音频，总数最多 12 个文件 | 通用参考素材模式不能与首尾帧模式混用。本地文件会按 API 要求编码，公开 HTTPS 输入直接使用 URL。 |
| ComfyUI MCP | 由 workflow 决定的图片、视频、音频和可选 mask | 每个输入都必须通过 Cyrene 占位符绑定到配置的 workflow JSON。本地文件只能由 local MCP profile 上传；cloud 模式只接受公开 HTTPS 参考素材。 |

具体能力可能随模型版本变化。显式选择 Provider 时，Cyrene 不会悄悄丢弃不兼容的参考素材，而是让任务在验证阶段失败。

## ComfyUI MCP

Cyrene 不打包 Comfy MCP，而是调用用户已在 MCP 设置中启用的 server。官方本地 server 的主链路是 `run_workflow`、`job(action=wait)`、`fetch_outputs`；Comfy Cloud 的工具名可在媒体设置中单独配置。

每种媒体类型的 Workflow 路径都是操作员设置，不能由模型从 `parameters` 注入。它必须指向一个已存在的 JSON 文件（1 byte–10 MiB）。Cyrene 只渲染临时副本，不修改源文件。Workflow 的 JSON 字符串值中可以使用以下占位符：

- 核心字段：`{{prompt}}`、`{{negative_prompt}}`、`{{lyrics}}`、`{{seed}}`、`{{duration}}`、`{{number_of_outputs}}`、`{{aspect_ratio}}`、`{{resolution}}`、`{{size}}`、`{{quality}}`、`{{output_format}}`。
- 自定义参数：`{{parameter.foo}}` 绑定 `parameters` 中的 `foo`。`confirm_spend`、生成超时等操作员控制项不能通过这里注入。
- 有序参考素材：`{{reference_1}}`、`{{reference_2}}`……顺序与标准化后的请求参考素材一致。
- Mask：`{{mask}}` 绑定标准化后的本地 mask 输入。

当一个 JSON 字符串值恰好只有一个 token 时，Cyrene 会保留它的 JSON 类型。例如，请求中的 seed 是数字时，`"seed": "{{seed}}"` 渲染后仍是数字；单独的 `{{parameter.foo}}` 也可以变成布尔值、数组或对象。如果 token 嵌在其他文本里，例如 `"seed={{seed}}"`，结果就是字符串（数组和对象会转成紧凑 JSON 文本）。

```json
{
  "6": {
    "inputs": {
      "text": "{{prompt}}",
      "negative": "{{negative_prompt}}",
      "seed": "{{seed}}",
      "reference": "{{reference_1}}",
      "custom_options": "{{parameter.foo}}"
    }
  }
}
```

请求实际提供的每个输入都必须被 Workflow 使用。未知占位符，或者已提供但没有对应占位符的字段、参考素材或 mask，都会使任务失败，避免创作输入被静默忽略。

`local` 模式会把本地参考文件和 mask 复制到临时 staging 目录，再通过设置中指定的 `upload_file` 工具上传；占位符接收上传后的文件名。公开 HTTPS 参考素材会原样传入。`cloud` 模式目前尚未实现 Comfy Cloud 素材上传协议，因此只接受公开 HTTPS 参考素材，不接受本地文件和 mask。`confirm_spend` 只能由操作员在 Provider 设置中开启，媒体请求本身不能授权可能扣费的 partner/API node。

ComfyUI custom node 可以执行任意代码，因此只应使用可信 workflow 和 node。

官方 Comfy MCP 当前采用 AGPL-3.0-or-later / 商业许可证双许可。Cyrene 这里只做外部 MCP 客户端集成，不复制或捆绑其代码。部署专有托管服务前应自行确认许可证要求。

## 参考资料（核对日期：2026-08-24）

- [OpenAI GPT Image 2](https://developers.openai.com/api/docs/models/gpt-image-2)
- [OpenAI Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
- [BytePlus Seedream image generation](https://docs.byteplus.com/api/docs/ModelArk/1824121)
- [BytePlus Seedance video generation](https://docs.byteplus.com/en/docs/byteplus_las/video_gen_enhanced)
- [Google image generation](https://ai.google.dev/gemini-api/docs/image-generation)
- [Google Gemini Omni video generation](https://ai.google.dev/gemini-api/docs/omni)
- [Google Veo video generation](https://ai.google.dev/gemini-api/docs/video)
- [MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- [MiniMax music generation](https://platform.minimax.io/docs/guides/music-generation)
- [MiniMax music API](https://platform.minimax.io/docs/api-reference/music-generation)
- [Comfy Org Comfy MCP](https://github.com/Comfy-Org/comfy-mcp)
- [Comfy Cloud MCP](https://docs.comfy.org/agent-tools/mcp)
