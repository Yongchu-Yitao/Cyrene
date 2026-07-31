> **PLANNED / 规划中 — 2026-07-31：** 本文记录目录/包边界迁移完成后
> （见 [架构重构 Handoff](COMPLETED-refactor-handoff.zh-CN.md)）的独立后续
> 改进项。各项尚未开始，内容为现状盘点、目标、建议做法与完成标准，
> 不承诺交付时间。

# 更广泛的产品 / 架构工作 Handoff

[项目记录索引](README.zh-CN.md) ·
[架构重构 Handoff](COMPLETED-refactor-handoff.zh-CN.md) ·
[Research Workbench 路线图](research-workbench-roadmap.md)

更新时间：2026-07-31

分支：`feature/project-literature-library`

## 1. 背景与范围

包边界重构与 WebUI / Workbench 合并已经完成，现有完整 pytest、WebUI Build
与 Electron App Use 三条 PR CI 链路。以下七项是迁移完成后的独立改进，
互不阻塞、可按任意顺序推进：

1. 用显式领域模型和 Repository 替换剩余的 Workbench 大型 dict 模型；
2. 把 Browser 和 Subagent 大模块拆成更小的状态机 / Transport；
3. 拆分行为学习的存储、候选生成、版本和执行服务；
4. 减少导入时配置变更；
5. 在现有 CI 基础上，按平台成本补充 Ruff 与打包 Smoke；
6. 在下次 Release 前解决 Locked FastAPI/Pydantic Environment 下的
   OpenAPI Normalized Snapshot Mismatch；
7. 实现 `research-workbench-roadmap.md` 中的 Experiments 和 Manuscripts。

每项独立分支/PR，不捆绑提交。完成后更新本文状态与真实测试数字。

## 2. 后续改进项

### 2.1 显式领域模型 + Repository 替换 Workbench 大型 dict 模型

**现状（代码依据）：** Workbench 各模块仍以裸 `dict[str, Any]` 在模块间
流动，字段漂移只能靠手工同步与运行时调试：

| 模块 | `dict[str, Any]` 出现次数 |
|---|---|
| `src/cyrene/workbench/chat.py` | 105 |
| `src/cyrene/workbench/goal_loop.py` | 43 |
| `src/cyrene/workbench/chat_runs.py` | 13 |
| `src/cyrene/workbench/workspace_changes.py` | 11 |
| `src/cyrene/workbench/notifications.py` | 8 |
| `src/cyrene/workbench/memory.py` | 4 |

**目标：** 对话、任务、运行、记忆、会话指标等核心实体引入显式领域模型
（dataclass/Pydantic），读写收敛到 Repository 接口——查询与业务逻辑分离，
存储格式不变。

**建议做法：**

- 从量小、边界清晰的模块起步（`chat_runs.py` 或 `memory.py`），渐进替换；
- 模型与 Repository 放 `src/cyrene/workbench/` 内的独立子包
  （如 `workbench/domain/`），不新增顶层包，避免违反包边界测试；
- 存储层的 JSON 序列化格式保持向后兼容（旧数据照常读入）；
- 每个实体迁移独立提交，迁移期间新老路径可并存。

**完成标准：** 核心实体不再以裸 dict 跨模块传递；Repository 有独立单测；
旧存储数据迁移后行为回归全绿。

### 2.2 Browser 与 Subagent 大模块拆分

**现状（代码依据）：** `src/cyrene/browser.py` 2565 行、
`src/cyrene/subagent.py` 2873 行。browser.py 单模块同时承担 Electron RPC、
Playwright 回退、登录接管（takeover）、Screencast、Snapshot/Ref 与坐标点击
等职责；subagent.py 承担运行生命周期、消息 Transport、工具执行与结算等待
原语（如 `subagent.wait_until_settled`）。

**目标：** 拆成更小的状态机 + Transport：

- Browser：会话生命周期状态机（headless/headed/takeover/screencast 状态
  已存在但散落）、Transport 抽象（Electron RPC 与 Playwright 两个后端）、
  Action 执行器、Snapshot/Ref 服务；
- Subagent：Run 状态机、消息 Transport、结果结算 / 等待服务。

**建议做法：**

- 保持公开工具签名与工具结果契约不变（`browser_snapshot`/`browser_click_ref`
  等 tool 的输入输出是稳定对外面，不能因为拆分漂移）；
- 先画状态转换表，再按状态机重排代码，不先改行为；
- 近期浏览器点击定位修复（ref 编号统一、坐标点击校验）应作为拆分时的
  回归基线纳入测试。

**完成标准：** 每个子模块可独立单测；浏览器与子代理工具行为回归全绿；
冻结包动态导入检查通过。

### 2.3 拆分行为学习的存储、候选生成、版本和执行服务

**现状（代码依据）：** `src/cyrene/learning/facade.py` 是单点门面（约 30 个
async 函数），混合承担记录动作、候选生成与决策、技能版本化、补丁应用、
回滚、执行与重放；`engine.py`、`skills.py`、`claude_code.py` 职责互相
交叠。

**目标：** 拆成四个内聚服务：

1. 存储：learned_skills / patches / runs / candidates 的 Repository；
2. 候选生成：`record_action` → 候选 pipeline（扫描、去重、打分）；
3. 版本：版本化、补丁应用、回滚、发布/弃用；
4. 执行：`run_learned_skill` 的运行时（参数合并、运行记录）。

**建议做法：**

- 保持 `facade.py` 为稳定公共 API（路由与 Agent 工具依赖它），内部改为
  服务装配层；
- 每拆一个服务独立验证，行为学习回归（`learn_from_turn`、`tick`、
  `rebuild_learning_state`）必须全绿；
- 存储格式不变，历史 skills 数据照常读取。

**完成标准：** 四个服务可独立单测；facade 只做装配与转发；行为学习
回归全绿。

### 2.4 减少导入时配置变更

**现状（代码依据）：** `src/cyrene/config.py` 在模块顶层：

- 从加密配置读取全部环境变量并 `os.environ.setdefault` 注入
  （`config.py:40-44`）；
- 模块级求值一批配置常量（`OWNER_ID`、`WEB_PORT`、`SEARXNG_*`、
  `MAX_HISTORY_MESSAGES` 等，`config.py:48-110`）。

因此 `import cyrene.<任意子模块>` 都会触发加密配置读取与 `os.environ`
变异；测试与工具脚本的导入顺序会悄悄影响全局环境。

**目标：** import 无副作用；配置读取收敛到显式初始化点（Daemon 启动路径），
`os.environ` 注入只发生在该路径。

**建议做法：**

- `config.py` 改为惰性 accessor（`get_xxx()`）或 settings 对象；
- 启动入口（`cyrene.cli`、`runtime.host`、Daemon bootstrap）显式调用
  `load_config_into_env()`；
- 保留模块常量兼容层（首次访问时求值），避免全仓一次性改调用点；
- 新增测试：import 前后 `os.environ` 快照不变、不读取配置文件。

**完成标准：** `import cyrene.<module>` 不读配置文件、不变异 `os.environ`；
启动路径行为不变；冻结包 Smoke 通过。

### 2.5 CI 补充 Ruff 与打包 Smoke

**现状（代码依据）：** `.github/workflows/ci.yml` 已有两条 job：

- `Python / locked contracts`：`uv sync --locked`、compileall、`ruff check
  src tests`、完整 pytest；
- `WebUI build / Electron tests`：`npm ci`、WebUI build、生成产物 diff
  校验、Electron App Use 测试、`git diff --check`。

没有对 PyInstaller/Electron 打包产物的启动验证。

**目标：** 按平台成本补充打包 Smoke：打包产物能启动、Daemon 健康、跑通
最小 Agent 调用。

**建议做法：**

- 先补 macOS / Linux 打包 Smoke（成本可控）；Windows 视 CI 成本再定；
- Smoke 内容最小化：产物启动 → 服务健康 → 一次最小 agent 调用 → 正常退出，
  不复制完整 App Use 测试；
- Ruff 已有 `[tool.ruff]` 配置与 lint baseline（`pyproject.toml:65-73`），
  CI 已在跑 `ruff check`——检查是否需要把 `ruff format --check` 或分平台
  矩阵纳入，避免重复劳动。

**完成标准：** PR CI 全绿；Release 产物至少一平台启动冒烟通过；文档记录
Smoke 范围与跳过条件。

### 2.6 Locked 环境下的 OpenAPI Normalized Snapshot Mismatch

**现状（代码依据）：** 重构 Handoff 记录过 OpenAPI Baseline Mismatch，
当时定位为用 Ambient 而非 Locked Dependency Version 采集 Hash，并已修正。
但 `pyproject.toml` 对 `fastapi>=0.115.0`、`pydantic>=2.10` 仍是范围约束
+ `uv.lock` 锁定的组合，Locked 环境下 OpenAPI 生成仍可能与会话中
Normalized Snapshot 漂移。

**目标：** `uv sync --locked` 环境下，OpenAPI 生成结果与已提交的
Normalized Snapshot 恒等。

**建议做法：**

- CI `Python / locked contracts` job 增加一步：生成 OpenAPI 后
  `git diff --exit-code` 校验快照无漂移（对齐 WebUI 生成产物的既有校验方式）；
- 在 Locked 环境重新生成并提交当前基线；
- 升级 FastAPI / Pydantic 走显式流程：先 `uv lock`，再重新生成快照，
  单独提交，禁止顺手漂移；
- 在文档中记录升级与再基线化流程。

**完成标准：** Locked CI job 含快照校验步骤且全绿；升级依赖时快照变更
有独立提交说明。

### 2.7 Roadmap：Experiments 与 Manuscripts

**现状（代码依据）：** `research-workbench-roadmap.md` 状态表：
Experiment Runtime 与 Manuscript Studio 均**未实现**——当前没有
experiment/manuscript service、queue 或对应路由；路线图 Phase 2
（Experiments MVP，约 4–6 周）与 Phase 3（Manuscript MVP，约 4–6 周）
未开始。Library 第一梯能力已落地（条目、集合、引用、Zotero 同步），
统一 provenance 模型（ResearchObject / ProvenanceEdge）也未实现。

**目标：** 按路线图交付：

- **Experiments**：可复现实验定义（spec）、队列、运行、日志/指标、
  取消/恢复、运行比较、环境快照；
- **Manuscripts**：文件为真源的学术文章对象（`.qmd`/Markdown）、引用与
  交叉引用校验、Quarto/Pandoc 编译、HTML/PDF/DOCX/LaTeX 输出。

**建议做法：**

- 严格走路线图的 MVP 边界：不做 WYSIWYG 编辑器、远程集群、多人协作或
  完整 MLflow 克隆；
- 先补统一 provenance 基础（Library 条目关系已存在，扩展为覆盖 paper /
  experiment / run / artifact / manuscript 的证据链），再上 Experiments MVP，
  最后 Manuscript MVP；
- 复用既有能力：Task/Run 执行、文件变更与审批、产物下载、Markdown/PDF
  渲染、引用渲染（`library.render_citation`/`render_bibtex`）。

**完成标准：** 路线图状态表逐项更新为已实现；端到端闭环跑通
（Paper → Experiment → Run → Artifact → Manuscript）；相关测试与 UI 验收
记录回填本文档。

## 3. 建议优先级与依赖

| 优先级 | 改进项 | 理由 |
|---|---|---|
| P0 | 2.4 导入副作用、2.5 CI、2.6 Snapshot | 低风险、独立、Release 阻塞项（2.6） |
| P1 | 2.1 领域模型 | 为 2.2 / 2.3 / 2.7 提供稳定基础 |
| P2 | 2.2 Browser/Subagent 拆分、2.3 行为学习拆分 | 依赖 2.1 的实体模型 |
| P2 | 2.7 Experiments / Manuscripts | 依赖 2.1 的 provenance 基础与已落地的 Library |

2.4 / 2.5 / 2.6 与其余各项互不阻塞，可并行推进。

## 4. 接手清单

接手任一项前：

- 阅读 [架构重构 Handoff](COMPLETED-refactor-handoff.zh-CN.md)，确认包
  边界基线与兼容规则；
- 阅读 [开发进度检查点](CONTEXT_DEV_PROGRESS.zh-CN.md)，确认当前验证
  状态与测试基线（完整 Suite 数字以该文档为准）；
- 确认工作区无未提交改动，避免覆盖；
- 按 §2 的"完成标准"验收，每项独立分支/PR；
- 涉及公开工具签名、存储格式或启动行为的改动，必须先跑对应回归再提交。

交付前：

- 更新本文状态与真实测试数字；
- 更新项目记录索引（`README.md` / `README.zh-CN.md`）；
- 记录最终分支、Commit 与冻结构建结果；
- 不以"代码能跑"替代完成标准中的验证项（Snapshot 校验、Smoke、回归）。

## 5. 风险与开放决策

- **2.1 替换范围**：Workbench 全部 dict 模型一次性替换风险高，推荐按实体
  渐进迁移；需在"字段校验提前"与"改动面可控"之间取舍。
- **2.2 拆分边界**：Browser 的 Electron RPC 与 Playwright 回退两条路径
  契约不完全一致，拆分 Transport 时需先冻结工具结果契约；点击定位近期
  修复（ref 编号统一）应纳入回归。
- **2.6 再基线化**：OpenAPI Snapshot 的再基线化可能掩盖真实漂移，升级
  FastAPI/Pydantic 时必须以显式提交记录，禁止"顺手更新快照"。
- **2.7 时间盒**：Experiments / Manuscripts 是路线图中最重的两项，需严格
  守住 MVP 边界，先闭环后增强。
