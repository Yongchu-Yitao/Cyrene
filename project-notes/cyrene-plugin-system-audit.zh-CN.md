# Cyrene 插件系统与 Agent 开发工具审计

审计日期：2026-09-08。范围：当前源码、安装包内置开发工具、安装版可编辑开发工具，以及 `wbchat_7248dea29b` 的持久化消息和 ContextTree。只读检查安装版，未修改其插件、设置或对话；本仓库仅新增本报告。

结论：统一插件框架的能力面较完整，但 Agent 创建、修改、验证、安装、实际调用这一闭环不完整。已有八个专用工具，不能把问题归结为“没有插件开发工具”。最先要修复的是工具发现失败的恢复能力、权威协议引导、开发路径衔接和宿主验收。

## 1. 安装版真实案例

对话：`wbchat_7248dea29b`，标题“广州天气查询”。Markdown 对话导出不包含全部中间调用和最后未结束轮次，因此同时读取了运行数据库的 `workbench_chat_messages` 和该对话 ContextTree。数据库均以 `mode=ro` 打开。

- 用户明确要求创建 wttr.in 工具插件，随后强调“无需 UI，只给 agent 用”。
- 更早的天气查询中，agent 调用 `toolbox.describe(name="cyrene_web_fetch")`，得到 `plugin_execution_failed`。接着调用 `toolbox.list`，得到 `plugin_circuit_open`。这是同一 run 内的真实熔断，不是推测工具没安装。
- 开始制作插件时，agent 没有重新发现工具，而是查网页、阅读工作区 `worldforge-cyrene-plugin` 的旧 `plugin.json`、README 和 Python 实现，把旧示例当作当前宿主规范。
- 生成结果采用 `plugin.json / apiVersion: 1 / cyrene.agentAction / activate(context) / register_method`。当前协议要求 `Plugin` 或从 `__init__.py` 导出 `PluginPack`。
- “你用一下试试”之后，agent 用 Bash 启动 Python，构造 FakeContext，直接执行 `plugin.activate(ctx)` 及注册函数；没有经 Cyrene registry、PluginRuntime 或 toolbox 调用天气工具。将该测试说成“与宿主调用完全等价”不成立。
- 用户询问是否激活后，agent 把目录复制到旧 `custom-tools`，尝试写 `.cyrene-tool-index.json`。用户回复 A 后，Write 再次被拦，后续 Bash 写入成功；最后清理临时文件又进入确认等待。该记录说明审批路径也有摩擦，但写入旧索引本身不能让新版 PluginPack 宿主加载该插件。
- 全部持久化消息中的工具调用统计为：Bash 28、Write 8、Read 5、WebSearch 5、toolbox 2。八个插件开发工具调用均为零，天气工具的宿主调用也为零。
- 当前安装包内置、安装版 `plugin_impl` 与工作区的 `cyrene_plugin_development/tools.py` 字节一致。可以排除“当前安装包完全没有新版 authoring 实现”；没有重建历史每次调用的完整 model-facing tools 快照，不能据此断言所有历史轮次都已启用该包。

关键区别：toolbox 熔断只证明最初那个 run 内发现入口不可恢复；后续新 run 理论上可以恢复，agent 却沿用了“toolbox 不可用”和旧协议假设。两者共同造成错误路径，不能将全部后续行为都解释为持续熔断。

## 2. 已具备的能力

| 范围 | 已实现 | 判断 |
|---|---|---|
| 统一协议 | Plugin / PluginPack、typed extension、Application / Session / Run scope | 主干完整 |
| 扩展能力 | 工具、模型 Provider、Context Hook、服务、路由、生命周期、UI/RPC、Workspace contribution | 覆盖面较广 |
| 加载与管理 | 目录扫描、注册、启停、热刷新、失效贡献退役、内置源码播种与保留本地修改 | 已有实质实现 |
| 执行 | Schema 校验、Pre/PostToolUse、并行控制、timeout、结构化错误、熔断 | 已有保护，但网关错误分类需修复 |
| Agent 工具包 | Guide / Scaffold / Validate / Install / Reload / Manager / SourceManager / HookManager | 基础工具齐全，开发闭环有断点 |
| 脚手架 | standalone_tool、tool_pack、model_plugin、context_plugin、application_plugin、ui_plugin、full_pack | 七种类型均有生成和加载测试 |
| 修改 | 现有文件 SHA-256 前置条件、Python 语法检查、系统修改 diff、Hook 管理 | 支持单文件修改，缺乏事务发布 |

Python 插件在宿主进程中导入执行，UI iframe 的 sandbox 不等于 Python 插件沙箱。当前适合受信任的本地可编辑插件；不能直接据此宣称具备不受信任第三方插件的进程隔离能力。

## 3. 具体缺陷及优先级

### P1：错误的工具名会熔断整个发现网关

位置：`src/cyrene/core/plugin/core_impl/toolbox.py:174`、`src/cyrene/core/plugin/runtime.py:665`。

describe 中的不存在目标没有转成可纠正的结构化失败，落入通用 handler failure，产生 `retry_scope=new_run, circuit_scope=run_plugin`。因此一个错误名称会让同轮后续 list 也失败。真实对话和最小复现均证实。

建议：对不存在目标、不可发现目标、缺少参数等返回 `different_arguments / circuit_scope=none`，给出使用 list 的恢复方向；目标工具故障应归目标，不应自动关闭发现网关。增加“describe 不存在名称 → list 成功 → describe 正确名称 → invoke”测试。

### P1：权威开发协议没有形成可靠的任务入口

位置：`src/cyrene/plugins/builtin/cyrene_system_prompt/system_prompt.py:26`、`src/cyrene/plugins/builtin/cyrene_plugin_development/tools.py:37`。

系统提示有通用 toolbox 发现流程，Guide 明确禁止 plugin.json，但实际任务没有读取 Guide，转而采用旧工作区示例。开发工具的存在不等于 agent 能稳定选择它。

建议：创建/修改 Cyrene 插件时，明确先读取当前宿主 Guide，再选择脚手架；提供协议版本和安装根的权威返回。对旧 plugin.json 产物给出迁移提示。避免继续向 agent 暴露旧示例而没有废弃标识。该项属于案例揭示的产品流程缺口，不是单凭静态代码即可保证解决的模型行为问题。

### P1：Guide 指定的“脚手架 → SourceManager 编辑”路径不通

位置：`tools.py:765`、`tools.py:1127`。

Scaffold 在 workspace 创建源码；SourceManager 只解析已安装的 plugin_directory 或 @core 下的路径。生成 `workspace/draft/tool.py` 后按指南读 `draft/tool.py`，实际访问的是 `plugin_impl/draft/tool.py`，得到 not found。使用通用 Read/Write 可以继续，但说明专用流程和说明不一致。

建议：明确区分 workspace draft 与 installed source，统一返回可交给后续工具的 source handle；或立即修正 Guide，草稿使用 Read/Write，安装后使用 SourceManager。

### P1：缺少宿主级验收，静态通过不等于可安装可调用

位置：`tools.py:220`、`tools.py:273`、`tools.py:874`。

Validate 主要检查 AST、声明和资源引用，不执行代码。这种默认策略合理，但工具包没有第二层受控加载和真实 Runtime 冒烟测试。`Plugin(... handler=42)` 会静态通过，实际 registry 加载失败。缺失依赖、导入错误、setup/startup 或业务失败也不能靠 AST 验证。

天气插件现有产物经当前 Validate 明确失败：`PluginPack directory requires __init__.py`。它原来的 FakeContext 验证无法替代这个检查。

建议：保留静态 Validate，再提供显式可执行的宿主测试；验收分别报告 source_valid、loaded、enabled、discoverable/direct、invocation_passed、restart_required，不能合成模糊的“完成”。可执行验证也应清楚标注实际副作用边界。

### P2：@core 源码根目录计算错误

位置：`tools.py:1136`、`tools.py:1198`。

源码树下表达式解析到 `src/cyrene/plugins/core_impl`，实际核心工具在 `src/cyrene/core/plugin/core_impl`。`@core/read.py` 因而找不到真实源码。最小复现证实工作区版本的错误；打包/播种布局还应单独覆盖，不应通过固定 parents 层数猜位置。

建议：由宿主或核心包提供权威源码根；打包后若没有可编辑源码，返回明确 unsupported，不能伪装成可编辑目标。

### P2：单文件即时重载，缺少完整修改事务和失败恢复

位置：`tools.py:1261`、`tools.py:1279`、`src/cyrene/core/plugin/registry.py:1586`。

SourceManager 每次写入/删除后立即全目录 reload。跨文件修改会暴露中间不一致状态。加载失败时 registry 会退役旧贡献，源码保留修改后的失败状态，没有自动回滚。Install 虽使用临时 staging 搬运文件，但 reload 失败也不会撤销已安装目录。已有 failure reporting 不能替代恢复机制。

建议：以 pack 为单位 stage 多文件、校验、加载后 commit，并提供恢复最近成功版本；失败时区分本次目标与目录中其他插件的失败。PluginManager 描述明确把 version update/rollback 排除在外，当前八工具中没有等价的闭环能力。

### P2：SourceManager 的重启提示遗漏普通 application pack

位置：`tools.py:1281`、`src/cyrene/plugins/application.py:242`。

SourceManager 只在 @core 修改时将 restart_required 设为 true。普通 application pack 修改后 host 可以已标记 restart_required_packs，SourceManager 却仍返回 false。PluginReload/PluginInstall 有相应状态读取，但源码修改工具没有统一使用它。

建议：统一从宿主读取目标 pack 的运行与重启状态，不要自行推断。

## 4. 更完整的工具包还需要什么

无需为每个动作机械新增工具；可以扩展现有工具协议：

1. 协议和能力自检：当前 API、安装根、插件是否加载/启用/暴露、失败原因、重启要求。
2. 完整草稿工作流：workspace draft、搜索/分页读取、跨文件变更、统一 source handle。
3. 静态与动态验证分层：安装前加载、真实 Runtime 调用、上下文或应用贡献生命周期测试。
4. 发布恢复：多文件原子提交、失败回滚、最近成功版本和差异追踪。
5. 通用依赖/兼容性契约：目前 PluginPack 本身和 authoring 工具没有正式的 Python 依赖锁定、宿主 API 兼容区间、插件间依赖解析流程。已有 Extensions 对特定 runtime/project pack 的安装逻辑，不等于任意用户插件的通用依赖管理。
6. 旧产物迁移：识别 plugin.json/activate(context)，说明不能直接注册，给出迁移到 PluginPack 的路径。

优先顺序：修 toolbox 可恢复性 → 明确 Guide 入口与正确草稿路径 → 建立真实宿主验收 → 补齐事务/回滚和重启状态 → 扩展依赖与版本管理。

## 5. 验证及限制

一次集中执行：

```text
uv run pytest -q /tmp/test_cyrene_plugin_audit_20260908.py \
  tests/test_plugin_authoring_management.py \
  tests/test_plugin_frontend_views.py \
  tests/test_plugin_application.py \
  tests/test_plugin_failure_circuit.py
```

结果：51 passed in 1.39s。其中 46 个现有测试、5 个临时审计复现。临时复现断言的是现存缺陷确实出现，不能把它们通过解释为缺陷已修复。

五个复现：未知工具导致 toolbox 熔断、workspace 草稿无法由 SourceManager 读取、@core 路径错误、非法 handler 静态通过但加载失败、天气产物无法通过当前 PluginPack 校验。

未运行全量测试，未联网测试天气，未启动或重载用户实际插件，未尝试在安装版绕过审批，也未迁移天气插件。系统源码的 user_confirmed 与中央权限审核如何绑定到持久化用户授权，仍需专门的权限审计；本报告不把布尔字段本身直接判定为完整权限绕过。
