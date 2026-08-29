# Cyrene 开发记录

> 更新日期：2026-08-29
>
> 本文件记录当前正式架构、已经完成的迁移和继续维护时必须守住的产品合同。
> 历史实施方案不再作为运行架构说明；正式用户与开发者文档位于
> [`docs/`](../docs/)。

## 资料索引

- [设计回归记录](audits/design-regression.md)
- [无障碍审计：Agent 自我设置、能力与权限](audits/cyrene-accessibility-2026-08-19/REPORT.md)
- [Agent 自我界面控制能力审计](audits/cyrene-self-control-2026-08-19/REPORT.md)

## 当前目标

- Cyrene 只有一套插件原生 Agent Runtime、一套 Workbench 和一套 WebUI。
- 除必要 Kernel Tool 外，工具、工具包、上下文、后台任务、Application、Route、
  页面贡献、Channel、Schedule、Proactive、Knowledge 和 SOUL 都由插件拥有。
- 新机制是唯一正式路径，不保留旧 Agent Runtime、旧工具注册表或旧业务后端作为
  Fallback。
- 安装版与源码版使用同一能力和界面，并在升级时自动迁移用户数据和可编辑插件。
- 对话、任务、工具、终端、知识、记忆、日程、媒体、Office、远程控制和渠道等已有
  产品能力保持完整。

## Core 与插件化重构验收

- Host-neutral Runtime 位于 `src/cyrene/core/`，只包含 Agent Session、
  ContextTree、Hook、Plugin Protocol/Scope/Registry 和执行机制。
- Cyrene 产品组装位于 `src/cyrene/plugins/`，标准可编辑功能位于
  `src/cyrene/plugins/builtin/`；Application/Model/Workbench Contribution 不在 Core。
- `src/cyrene/workbench/` 是 Cyrene Host 适配层；业务模块按 Application、Chat、
  Task、Project、Goal、Planning、Artifact、Session、Control、Workspace 与 UI
  领域分包，另含 Core Adapter、持久化、HTTP 组装与唯一 WebUI。根目录不放业务
  Service，也不保留旧路径兼容转发。
- 旧顶层 `src/agent`、`src/route`、`src/webui` 已删除，不存在兼容外壳。
- 验收结果：2,209 项 Python 测试通过（未处理 Thread Warning 视为错误），
  WebUI 27/27、Electron 84/84 通过，Production WebUI Build 和 Wheel Build
  成功，Wheel 中不包含 `agent`/`route`/`webui` 顶层包。

## Agent Runtime

一次用户交互进入同一个连续、可持久恢复的 Run：

```text
用户消息
    → Context Plugin 构建 Context Tree
    → 连续 Agent Run
        → 回复或提问
        → 直接调用可见工具
        → toolbox.list → describe → invoke
        → 创建 Subagent / Inbox 协作
        → 完成、取消或恢复
    → 持久化并发布最终结果
```

- 模型输出、工具调用、运行中指导、等待用户、重试、取消、恢复、压缩与最终投递都
  属于同一个 Run，不再在两套 Agent transcript 间交接。
- Context Tree 是模型上下文的唯一组合入口。稳定 Block 使用稳定 Identity，以便在
  内容不变时复用 Prompt Cache。
- 长对话继续使用统一 Compactor；压缩只改变模型投影，不会删除持久消息历史。
- Workbench Chat 的发送、流式输出、工具轨迹、恢复和取消都连接到同一 Runtime。
- 当前步骤 Token、总用量、上下文占比和输出速度由统一 Usage 事件驱动，重连后可以
  从持久状态恢复。
- 模型、认证、配额和 Provider 错误不会通过跨 Provider 的静默切换掩盖。

## Plugin Runtime

- `Bash`、`Read`、`Write` 和 `toolbox` 是 Agent Kernel 必须直接持有的核心工具。
- 其余内置能力位于 `src/cyrene/plugins/builtin/` 的插件包中；安装版会把可编辑实现
  安装到用户数据目录 `plugin_impl/`，核心工具除外。
- 插件可以贡献工具包、独立工具、Context Hook、后台 Job、Application 生命周期、
  HTTP Route、全局搜索、Workbench 模块、设置页面、Channel 和模型能力。
- 工具包和独立工具都通过 `toolbox.list → describe → invoke` 发现。`toolbox.list`
  同时列出工具包和独立工具，不再保留 `toolbox.search`。
- 用户可以把工具设为“Agent 直接可见”或“Agent 寻找使用”。直接可见工具加入当前
  Tool 列表，其余工具保持按需发现。
- Runtime 按当前插件 `input_schema` 校验调用；对象字段顺序不影响调用，只要条目、
  内容和对应关系完整。
- 插件的启用、停用、重载和删除会同步更新 Tool Catalog、Context Tree、后台 Job、
  Route 和界面贡献，不会回退到第二份内置实现。
- 插件与工具提供中英文元数据；Plugin Center 和权限卡使用当前界面语言展示名称、
  描述和状态。

## Plugin Center 与用户自定义

- Plugin Center 统一展示插件包和独立工具，并区分 Kernel、内置可编辑实现和用户目录
  来源。
- 每个工具都有菜单，可以编辑展示名称和给 Agent 的描述、切换直接可见/寻找使用、
  以及删除允许删除的工具。
- 工具菜单的修改作为用户自定义覆盖保存，插件重载和应用重启后继续生效。
- 插件包支持安装、启停、重载、搜索和删除；删除前明确说明是否保留数据。
- 项目插件页面继续支持分屏、全屏、独立窗口和恢复，并与聊天、文件和终端共同编排。
- 插件提供的模型、Slash Command、MCP、Skills 和上下文能力都进入统一选择体验。

## Context Plugin

- `cyrene_composer_context` 专门负责输入框 Context 菜单和相关上下文构建，包括当前
  Workspace、可选目录、MCP Server、Skills 和其他会话级能力。
- 输入框选择按对话和项目保存；切换对话、Agent 或项目不会错误带入其他作用域。
- 所有长期上下文组成都有明确插件 Owner；插件关闭后对应 Block 不再挂载。
- SOUL.md 由 SOUL 插件管理。启用时，人格 Block 位于 System Prompt 正下方；关闭时
  不注入，也不由核心偷偷补回。
- Project Memory、Onboarding、Workspace、工具说明、运行状态和其他 Context Mount
  通过同一 Context Tree 发布和检查。

## Subagent

- Subagent 创建时获得与 Main Agent 初始状态一致的 Context Tree，并额外接收 Main
  Agent 的任务指令。
- Subagent 有独立的后续 Context Ledger，不会与 Main Agent 共享可变 transcript。
- Main Agent 与 Subagent 通过持久 Inbox 发送消息、广播和接收更新。
- 唤醒、等待、恢复和完成都保留已读取的 Inbox 边界与执行检查点，避免重复处理。
- Actor 权限仍由 Runtime 审核；继承 Context 不代表继承 Main-only 权限。

## Knowledge、Schedule 与 Proactive

- `cyrene_knowledge` 插件完整拥有知识库和文献库的导入、附件、解析、向量化、检索、
  Zotero、页面、搜索和 Agent 工具。
- 旧知识库内容在首次启动新版本时自动、幂等迁移到插件数据目录；安装版同样执行，
  原数据保留用于恢复，不会重复创建条目。
- `cyrene_schedule` 插件拥有定时任务、运行历史、通知、暂停、恢复、取消和 Workbench
  页面；通用后台 Host 只负责按插件声明触发。
- Proactive Agent 由插件声明后台运行和上下文贡献，并使用同一个 Workbench Chat
  Runtime 投递结果；核心不保留另一套主动任务执行器。
- Schedule 和 Proactive 在插件停用时停止注册，在恢复启用后从持久状态继续。

## Workbench 与界面

- Workbench 是浏览器和 Electron 的唯一正式界面，Quick Chat 复用同一数据、事件和
  Runtime 服务。
- Chat 保留发送、流式显示、工具轨迹、等待用户、取消、恢复、重试、附件、语音、
  Slash Command、分屏与多窗口行为。
- 对话侧栏展示状态、Agent、模型、会话 ID、消息数、创建时间、输入/输出/总 Token、
  上下文占比、压缩阈值、Inbox 和已使用工具包。
- 每条运行消息的操作区展示当前步骤 Token、耗时与输出速度；历史重新加载后继续可见。
- Plugin Center、Context 菜单、Knowledge、Schedule、Memory、Settings 和各插件贡献页
  使用统一 i18n、状态、菜单、空状态、自动保存与错误反馈。
- 终端恢复或后台重连不会抢走当前输入焦点；中间消息和等待用户事件不会重复显示。

## 数据、打包与升级

- 主数据库、插件数据、项目数据、终端历史、SOUL、Inbox、模型配置和用户覆盖使用
  各自明确的数据位置和备份语义。
- 迁移先验证目标，再写入幂等 Marker；已经有新数据时不会静默覆盖。
- Electron 的 macOS、Windows x64、Windows ARM64 和 Linux 构建使用同一版本号、
  WebUI 资源和插件清单。
- 安装版启动时确保用户目录中存在当前可编辑插件实现，并只补齐缺失项；用户已经修改
  的实现不会被普通启动覆盖。
- 应用退出、项目关闭、插件停用和终端删除会收拢后台任务、订阅、浏览器和终端资源，
  避免下次启动继承过期状态。

## 维护约束

- 不重新引入旧 Agent Loop、旧工具包 Gateway、旧 Route 后端或兼容 Fallback。
- 新的上下文组成必须由 Context Plugin 发布，不能在 Prompt Builder 中临时拼接。
- 新工具默认进入插件；只有维持 Runtime 本身所必需的工具才能进入 Kernel。
- 新增 UI 文案和插件元数据必须同时提供中文和英文。
- 任何重构都必须保持已有功能、用户数据、权限边界、恢复行为和发布平台完整。
- 发布前以 GitHub CI 的 Python locked contracts、WebUI/Electron tests 和各平台安装包
  Smoke Test 作为最终验收。
