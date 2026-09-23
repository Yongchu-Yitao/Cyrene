# 对话上下文分支导图：实现与验证

日期：2026-09-21。对应 `context-mindmap-complete-plan-2026-09-21.zh-CN.md`，实现直接接入 Cyrene Workbench 的上下文指标入口和分支面板。

## 已实现的操作

- 默认打开分支导图，保留模型输入列表与全屏模式。共同前缀合并，保留每个物理来源；修订内容、挂载生命周期或覆盖配置不同则拆开。
- 同时读取跨对话 fork 和同树 retry 的历史。当前路径高亮；点击历史节点只预览，续聊会恢复所选路径为独立执行树。
- React Flow 画布支持平移、缩放、小地图、节点移动、折叠、搜索定位、键盘可访问的节点列表、工具步骤收起、隐藏和恢复。
- 右侧查看完整正文、模型输入、来源与修订信息；可编辑用户/助手消息、系统和上下文、完整工具结果组、压缩/反思内容与任务文档。
- 编辑保存创建独立分支。用户问题保存为持久草稿，可单独生成；原文分叉复用现有 forkReplay 运行链路。人工回复明确标记，原历史保留。
- 原“模型输入”列表编辑入口也改用新分支修订接口，保存后激活新对话。底层旧节点更新 API 为兼容仍保留，并补上事务内版本校验。
- 复制用户问题到另一完成回复：按钮或拖动触发，确认面板显示两条路径的模型输入差异；保存目标历史与问题草稿，不把来源后续答案伪装成新结果。附件复制并重写路径。
- 新增/移除上下文、分支后续轮次覆盖、引用固定内容；备注仅保存到图元数据。覆盖在提供器不再生成挂载时仍进入模型输入。
- 对比固定节点的正文、有效输入和模型配置；已保存修订通过“恢复此前内容”为新分支生成反向修订。
- 分支标题、颜色、收藏、归档恢复、删除；删除复用已有对话删除流程，后代分支不连带删除，已知来源保留占位。
- 布局撤销/重做；“保存布局”持久化位置、折叠、隐藏、视口和选择。JSON/Markdown 导出完整正文，SVG/PNG 导出当前可见图。附件文件本体不包含在这些导出中。

## 与现有框架的衔接

`core/context/paths.py` 统一恢复叶子规则，供 Session、上下文查询、检查点和 fork 使用。显式历史路径严格校验祖先链；检查点兼容只有末端节点的窗口读取。

`ContextGraphService` 使用现有 `ChatRepository`、`ContextStoreRouter` 和 `ConversationRuntime.fork_context`。执行状态仍由核心 committed state 管理。任务文档按成功历史重建；人工任务编辑形成可重放事件。新草稿使用内部完成检查点保证重启后空闲，实际发送时移除草稿占位，避免重复问题。

元数据采用一张 `workbench_context_graph` SQLite 表中的分类型记录（lineage、branch、view、revision、operation），而非方案中的多张独立表。每条记录有 CAS revision；分支操作有 OS 进程锁、幂等标识、准备/提交日志和失败清理。复制到独立树时，来源树发生变化会拒绝注册目标。文件复制先暂存再就位。

现有历史按需建立来源索引，不重写旧树或强行合并物理数据库。导图接口一次返回族内节点摘要，正文和投影按选中节点加载；目前没有游标分页。复制的相同节点 ID 通过 `(chatId,nodeId)` 区分。

前端图与 ELK 布局按需加载，ELK 使用 Worker。React Flow 与现有 UMD React 共用实例。主入口构建后为 **3.49 MiB**，未提高现有 3.5 MiB 限制或 JavaScript 复杂度基线。

## 验证记录

- 全量 Python 测试最近一次：**3,225 passed，5 skipped**。
- 之后补充的 HTTP 契约及用户反向修订测试，连同全部导图专项：**20 passed**。
- 最终导图、生命周期、前端契约、构建契约及工作区相关回归：**412 passed**。
- 前端 Node 测试：**192 passed**。
- `npm run check:complexity`：通过，无预算增长。
- `npm run build`：通过，图组件、样式和 Worker 资源均生成。
- `git diff --check`：通过。
- 浏览器使用临时 SQLite 数据库、真实图 API 和真实前端组件：验证共同前缀及分叉、节点详情、编辑并保存新分支、原分支保留、复制问题差异确认及保存、保存布局并刷新、390px 窄屏。
- Session 测试使用项目现有模型替身，实际运行提交/恢复/任务投影，验证草稿只发送一次及后续上下文覆盖。未向外部模型发起付费请求。
- 100/500/1000 节点纯投影及 ELK 基准见 `experiments/context-mindmap/benchmark.json`；布局约 68/98/153ms。该基准是合成树计算时间，不代表浏览器帧率。

复现命令（仓库根目录）：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_context_graph.py -q
npm --prefix src/cyrene/workbench/webui test
npm --prefix src/cyrene/workbench/webui run check:complexity
npm --prefix src/cyrene/workbench/webui run build
node project-notes/experiments/context-mindmap/benchmark.mjs
```

## 验证边界

本轮没有操作用户现有对话数据库，也没有重启其正在运行的应用；需要重新启动后端并刷新前端，才能让现有进程加载新路由和会话代码。Web 交互已验证；没有完成 Electron 原生窗口里的逐项人工验证。未实测真实长历史下的 60 FPS 或点击延迟 P95，不能将合成布局基准替代这些指标。

外部 Agent 没有本地 ContextTree 时，图接口明确返回不支持。待答权限请求不复制为新分支；不完整工具调用不能直接续跑。复制问题保留独立附件，外部工作区文件和已完成工具的外部副作用仍遵循原项目语义，图操作不会回滚这些副作用。
