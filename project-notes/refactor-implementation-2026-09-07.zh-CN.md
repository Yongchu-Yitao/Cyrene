# 2026-09-07 重构实现与验证

后续收尾已完成，最终状态见 [审查清单完成记录](refactor-completion-2026-09-07.zh-CN.md)。下文保留第一批范围与当时的验证结果。

本轮以 `212dcfaa` 为起点。目标是减少职责耦合与局部复杂度，保留功能、特性、交互和协议；不以减少功能分支或运行时 Hook 数量作为目标。

## 实现范围

| 范围 | 改动及保留的契约 |
| --- | --- |
| 模型配置 | `configuration_validation.py` 集中规范化和校验；持久化、密钥隐藏和缓存失效仍由 configuration 管理。保留首个错误、旧密钥继承、显式清除、URL 默认值和输入复制行为。 |
| 设置 | 快捷键规范化独立为辅助函数，保留原有类型转换和校验顺序。 |
| 发送 | 用 `SelectedSendModel`、`PreparedSendEnvironment` 明确准备阶段输出，替代分散的运行环境字段。保留重试、附件、工作流、模型偏好写入和运行冲突的先后顺序。此次未重写 Send/Answer 的全部事务与回滚逻辑。 |
| 会话恢复 | `restore_decision.py` 决定恢复动作；会话仍执行状态、存储、队列操作。查询保持惰性，reflection 先恢复请求再查询已有转换。 |
| 活动卡片 | `tool_activity_projection.py` 集中从 ContextTree 重建工具和权限轨迹，保留过滤、顺序、预览、ID 回退与推断完成语义；bridge 保留原公共入口。 |
| 前端 | Rail 插件集合加载、Topbar 悬浮预览计时、Composer 键盘策略、分屏升主屏与恢复的 DOM 动画各自独立。保留 IME、快捷键优先级、80/420ms 悬浮时序、视口锚点、减少动态效果与失败清理。 |
| 模型适配器 | Codex 输出累计从超时/取消循环分离；MiniMax H3/v1 请求构造与校验分离。保留流事件顺序、最终文本选择、usage、请求载荷和错误。 |
| 终端 | `ScrollbackSegments` 管理分段回滚缓冲的迁移、追加、淘汰、读取和统计，共用原条件锁；工作线程和队列保留。移除无调用方的中转方法，保留调用方及测试使用的入口。 |
| Electron | 视频全屏窗口、监听器、退出计时交给独立 owner；目录按职责整理，详见 `electron/README.md`。 |

## Electron 目录与路径

根目录保留应用入口、preload、开发启动器和包配置；其余按 `browser`、`desktop`、`automation`、`remote-desktop`、`backend`、`diagnostics`、`shared`、`scripts`、`tests` 分类。单元测试与模块相邻，跨层集成测试放 tests。

同步更新了 require、BrowserWindow preload、HTML 加载、Python RDP 桥接路径、原生辅助程序构建位置、electron-builder 清单、Linux 安装钩子、GitHub CI/Windows 发布命令、测试和开发文档。

打包后的原生辅助程序仍位于 `resources/app-use/`；开发态 macOS 二进制输出改为 `electron/automation/app-use-macos-hit-test`，保持忽略生成文件。runtime-tools 及缓存仍留在 Electron 根目录。

## 复杂度结果

以下是静态函数自身指标，不是整体功能数量、实际 Hook 执行次数或性能测量。抽取出的逻辑仍存在并继续执行。

| 指标 | 之前 | 之后 |
| --- | ---: | ---: |
| 模型配置主规范化函数：决策数 | 90 | 13 |
| 设置规范化：嵌套深度 | 12 | 8 |
| 会话恢复函数：行数 / 决策数 | 250 / 42 | 156 / 33 |
| Codex complete：行数 / 决策数 | 356 / 78 | 293 / 56 |
| MiniMax 视频入口：行数 / 决策数 | 341 / 107 | 142 / 31 |
| 终端持久化 writer：方法数 / 字段数 | 43 / 36 | 39 / 30 |
| BrowserTabManager：字段数 | 37 | 31 |
| 聊天页面模块：行数 | 3357 | 3198 |
| Electron main.js：行数 | 6660 | 6518 |

默认复杂度阈值未提高。Electron 文件的预算随路径迁移；匿名函数通过源代码身份匹配迁移预算；随后使用现有 ratchet 收紧预算，移除已不需要的例外。当前 Python 和 JavaScript 检查均无预算增长。

## 验证

- 前期高风险后端检查：123 通过。
- Python 全量：2771 通过、5 失败、2 跳过；5 项失败分别是 4 个旧路径断言和 1 个粘贴源码分号误判。修正后，对受影响区域及最终终端调整集中复测：412 全部通过。没有再次重复运行全量。
- Electron 全量：132 全部通过；最后新增 Windows 发布路径检查后，打包路径测试 3 项全部通过。
- WebUI 全量：121 全部通过；将粘贴源码断言替换为执行文件粘贴、items 回退、上传结果、普通文字、运行中及等待答复场景的行为测试后，相关模块 5 项全部通过。
- 根目录 browser input 单元测试通过。
- 重构前后一次性对照：MiniMax 4800 组请求的载荷/错误/输入变化一致；模型配置 2000 组输入的结果/错误/输入变化一致。
- Ruff、Python/JavaScript 复杂度检查、WebUI 构建、补丁格式检查通过；已更新 WebUI 生成资源。
- macOS 原生 App Use 辅助程序构建成功，产物同时包含 x86_64、arm64。
- 隔离数据目录下真实 Electron 冒烟通过：`DESKTOP_SMOKE_TEST=ok`，页面存在有效非白像素，`desktop_settings_cas` 和 `shortcut_settings_sync` 通过。测试使用原有冲突检测场景，日志中的预期 409 已被冒烟断言验证。

本地没有实际运行 Windows/Linux 原生桌面或生成完整跨平台安装包。自动化与 macOS 冒烟支持本轮行为保持结论，不能据此宣称所有平台和所有外部服务的运行情况获得绝对证明。本轮没有推送，也没有把此前 GitHub CI 成功冒充为本轮 CI 结果。

未撤销工作区里已有的 page-state/recent-tabs 测试改动。
