# Workbench 科研一站式改造方案

[中文](research-workbench-roadmap.md) ·
[English](research-workbench-roadmap.en.md)

> 状态核对：2026-07-26。本文件同时包含已落地能力和后续规划。
> “当前已实现”均已对照 `src/cyrene/knowledge/`、
> `src/route/workbench/library.py`、`src/workbench-webui/workbench-library.*`
> 与 `tests/test_workbench_library.py`、`tests/test_library_tools.py`；
> 其余内容仍是路线图，不代表已经交付。

## 当前实现状态

| 范围 | 2026-07-26 状态 | 代码依据 |
|---|---|---|
| Library 基础 | **已实现**：项目隔离条目、集合、标签、阅读状态、星标、回收站、笔记、批注、附件、条目关系、统计、字段与全文检索 | `cyrene.knowledge.library`、`route.workbench.library` |
| 既有 Knowledge 兼容 | **已实现**：同一项目 `kb_documents` 幂等映射到 Library，复用附件与索引，不复制文件 | `library.sync_knowledge_documents` |
| 文献导入与同步 | **部分实现**：CSL JSON、RIS、BibTeX、普通文件/PDF 导入和 Zotero Local API 增量同步已实现；DOI/题名在线导入、Crossref、OpenAlex、Semantic Scholar 尚未实现 | `cyrene.knowledge.bibliography`、`cyrene.knowledge.zotero` |
| 引用与阅读联动 | **部分实现**：IEEE/APA/MLA/Chicago 文本引用和 BibTeX 输出、附件阅读状态联动已实现；稳定 citekey 冲突策略和页面锚点批注尚未实现 | `library.render_citation`、`library.render_bibtex`、Library API/UI |
| Experiment Runtime | **未实现**：现有 Agent run、Shell 和任务能力不能视为本路线图中的可复现实验运行 | 当前没有 experiment service、queue 或对应路由 |
| Manuscript Studio | **未实现**：现有 Markdown/PDF/报告能力不能视为学术文章对象、引用校验和 Quarto/Pandoc 编译工作流 | 当前没有 manuscript service 或对应路由 |
| 统一 provenance | **未实现**：Library 条目关系已存在，但尚未形成覆盖 paper、experiment、run、artifact、manuscript 的统一证据链 | 当前没有通用 ResearchObject/ProvenanceEdge 模型 |

## Executive Summary

- **方向可行，Library 的第一批能力已经落地，其余科研闭环仍待建设。** Workbench 已有项目隔离、PDF/文档索引、语义检索、任务执行、Shell、产物下载、调度和报告导出，也已经拥有结构化文献库。当前仍缺少实验、科研运行、数据集、文章等一等对象，以及贯穿它们的可复现溯源链。
- **产品形态应是科研项目操作系统，而不是三个新页面的集合。** 以 `Paper → Note → Experiment → Run → Artifact → Manuscript` 为主链，用统一的 provenance edge 记录“引用、使用、生成、支持、插入”等关系。文献、实验和写作模块都围绕这条链读写。
- **MVP 应走本地优先、文件为真源、适配器集成的路线。** 文献元数据用 CSL-JSON；实验先支持 `uv` 环境下的命令和参数化 Notebook；文章以 `.qmd`/Markdown 为真源，通过 Quarto 或 Pandoc 编译。Zotero、OpenAlex、Jupyter、MLflow 都是可替换适配器，不应成为 Cyrene 的内部数据模型。
- **推荐依次交付“基础溯源 → 文献库 → 实验运行 → 文章写作 → 端到端联动”。** 不要先做 WYSIWYG、远程集群、多人协作或完整 MLflow 克隆；这些会延迟最关键的闭环：从可信来源到可复现实验，再到可核验文章。

## 1. 现有 Workbench 适配度

| 现有能力 | 可直接复用 | 需要补齐 |
|---|---|---|
| 项目与工作区 | 项目隔离、项目路径、任务与对话上下文 | 科研项目模板、研究问题、假设、里程碑 |
| Knowledge | PDF/文本上传、分块、FTS/向量检索、标签、文档关系、关联任务 | 结构化论文元数据、DOI/作者/期刊、引用键、馆藏、阅读状态、PDF 批注 |
| Task/Run | Agent 计划、运行事件、Shell、文件变更、审批、产物下载 | 可复现实验定义、环境快照、参数/指标、日志、取消/恢复、运行比较 |
| Artifact | 工作区文件可下载，并可归档进 Knowledge | 不可变内容哈希、逻辑角色、输入输出关系、实验/文章归属 |
| 编辑与渲染 | Markdown、KaTeX、代码编辑/差异、PDF.js、ReportLab PDF | 学术引用、交叉引用、文章大纲、编译器、期刊模板、DOCX/PDF/HTML 多格式 |
| 调度与 Agent | 定时任务、Deep Research、浏览器、MCP、子任务 | 文献提醒、实验队列、写作检查、科研专用工具权限与审计 |

现有知识库表只把内容建模为 `kb_documents / kb_chunks / kb_relations`，其中论文结构化信息只能塞进 `metadata` JSON。现有 Workbench `runs` 是 Agent 交互运行，`artifacts` 又明确只接受 `file_change` 文件。二者都不应直接改名复用为科研实验运行；否则 Agent 生命周期、实验生命周期和文件生命周期会缠在一起。

## 2. 产品主线：统一科研对象与证据链

建议把 Workbench 的产品主语从“任务”提升为“研究项目”，任务仍是 Agent 的工作单元，但不是科研数据的唯一容器。

```text
Paper ──cited_by──> Manuscript
  │                    ▲
  ├─supports_claim─────┤
  │                    │
  └─motivates──> Experiment ──has_run──> Run
                       │                  │
Dataset ──used_by──────┘                  ├─produces──> Figure/Table/Model
Environment ──used_by─────────────────────┘              │
                                                        └─inserted_into──> Manuscript
```

每个对象至少具备：稳定 ID、所属项目、显示名称、状态、创建/更新时间、来源、内容哈希或版本、可查询元数据。每条边具备：关系类型、源对象、目标对象、创建者（用户/Agent/导入器）、创建时间和可选证据片段。

这条证据链带来三个核心能力：

1. 从文章中的结论回到引用论文或实验运行；
2. 从图表回到生成它的代码、参数、数据和环境；
3. 在论文、数据或实验变化后，识别受影响的文章段落与产物。

## 3. 文献管理：把 Knowledge 升级为 Library

### 3.1 MVP 功能

1. **统一导入**：DOI、题名搜索、URL、BibTeX/RIS/CSL-JSON、PDF 拖入、Zotero collection 导入。
2. **元数据校验与去重**：优先 DOI；其次标题规范化、作者和年份；保留合并历史，不静默覆盖用户修改。
3. **论文详情**：题名、作者、摘要、期刊、年份、DOI、开放获取链接、标签、阅读状态、收藏夹、PDF、笔记和关联对象。
4. **阅读工作流**：Inbox → To Read → Reading → Read；PDF 选中文本可生成带页码与原文锚点的笔记。
5. **引用键**：生成稳定 `citekey`，支持冲突处理；向写作模块提供 `@citekey` 自动补全。
6. **发现与提醒**：保存检索式，复用现有 scheduler 定期查询新论文；结果先进入候选 Inbox，避免自动污染馆藏。

### 3.2 外部服务策略

- **Zotero 是首选互操作对象，不是内部数据库。** Zotero Web API v3 支持馆藏、条目、附件、增量版本和写请求；桌面端还在 `localhost:23119/api/` 暴露相同风格的本地 API。MVP 做本地/云端只读增量同步，二期再做带冲突检查的双向写回。[Zotero API](https://www.zotero.org/support/dev/web_api/v3/basics)
- **OpenAlex 负责跨学科检索与关系扩展。** 它提供 works、authors、sources、institutions、topics 等实体，适合相关论文、被引/参考关系和作者消歧。[OpenAlex API](https://developers.openalex.org/api-reference/introduction)
- **Crossref 负责 DOI 权威元数据补全。** REST API 无需注册即可检索作品记录，适合作为 DOI 规范化与元数据校验源。[Crossref REST API](https://support.crossref.org/hc/en-us/articles/214320426-REST-API)
- **Semantic Scholar 作为可选推荐适配器。** Academic Graph API 可提供论文与作者数据；不要把其 `paperId` 当成全局主键，内部仍以 DOI/规范化标识为主。[Semantic Scholar API](https://api.semanticscholar.org/api-docs/)

### 3.3 关键数据模型

```text
research_papers
  id, project_id, doi, title, abstract, year, venue,
  citekey, csl_json, status, kb_document_id, pdf_document_id,
  metadata_source, metadata_version, created_at, updated_at

research_authors
  id, canonical_name, orcid, openalex_id

research_paper_authors
  paper_id, author_id, ordinal, role

research_collections / research_collection_items
research_annotations
  id, paper_id, page, quote, note, color, anchor_json

research_sync_cursors
  project_id, provider, library_id, cursor, last_synced_at
```

CSL-JSON 应作为引用元数据的规范表示，BibTeX/RIS 是导入导出格式。论文全文继续由现有 Knowledge 负责分块和检索，`research_papers` 只保存结构化元数据与知识库文档 ID。

## 4. 实验运行：新增独立的 Experiment Runtime

### 4.1 为什么不能只复用 Shell

Shell 解决“执行命令”，但科研运行还必须回答：使用了哪个代码版本、环境、数据、参数和随机种子；生成了哪些指标和产物；运行能否被取消、恢复、比较和重放。现有 Agent `runs` 也记录一次任务执行，但它的输入是自然语言、生命周期受 Agent 计划控制，不适合作为实验记录的真源。

### 4.2 MVP 运行类型

1. **Command Run**：在项目工作区和选定环境中执行脚本/命令。
2. **Notebook Run**：通过 Papermill 批量执行带参数 Notebook；交互式 Notebook 后续通过 Jupyter Server 的 sessions/kernels API 接入。
3. **Compile Run**：文章编译也复用统一运行基础设施，但 `kind=manuscript_compile`，不与科学实验混为一类。

Jupyter Server 已提供 contents、sessions、kernels、interrupt/kill 等 REST 接口，可作为交互式内核的边界，而不是自行实现 kernel wire protocol。[Jupyter Server REST API](https://jupyter-server.readthedocs.io/en/stable/developers/rest-api.html)

### 4.3 运行生命周期与记录

```text
draft → queued → preparing → running → succeeded
                              ├──────→ failed
                              ├──────→ cancelled
                              └──────→ orphaned → reconciled/failed
```

每次运行记录：

- experiment/spec 版本与完整命令；
- git commit、dirty diff 哈希；
- Python/OS/CPU/GPU 摘要；
- `pyproject.toml`、`uv.lock` 或其他环境锁文件哈希；
- 输入数据集 ID 与内容哈希；
- 参数、随机种子和允许记录的环境变量；
- stdout/stderr 顺序日志、退出码、开始/结束时间；
- 指标序列和输出 artifact manifest。

Cyrene 已使用 `uv`。`uv run --locked` 能在锁文件过期时拒绝运行，`uv.lock` 又记录精确依赖版本，适合作为 Python MVP 的默认可复现环境。[uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)

### 4.4 Runner 架构

```text
ExperimentService
  ├── RunQueue (SQLite durable queue)
  ├── ProcessSupervisor (async subprocess + process group)
  ├── EnvironmentAdapter
  │     ├── UvAdapter        [MVP]
  │     ├── ExistingVenv     [MVP]
  │     ├── CondaAdapter     [later]
  │     └── ContainerAdapter [later]
  ├── WorkloadAdapter
  │     ├── CommandAdapter   [MVP]
  │     ├── PapermillAdapter [MVP]
  │     └── JupyterAdapter   [later]
  └── ArtifactCollector + MetricParser
```

运行日志通过 SSE 推送；进程由服务端 supervisor 持有，关闭页面不应终止。应用重启后根据 PID、启动标识和输出目录进行 reconciliation。取消操作必须终止整个进程组。路径限制沿用现有 workspace 校验；敏感环境变量只按 allowlist 记录并做脱敏。

MLflow 很适合做可选兼容层，因为它的核心也是 experiment/run/param/metric/artifact，并且可本地文件落盘或使用 REST server。但 MVP 不应强依赖或嵌入完整 MLflow UI；先提供 import/export adapter 即可。[MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/)

### 4.5 关键数据模型

```text
research_experiments
  id, project_id, name, description, spec_path, spec_hash, created_at, updated_at

research_runs
  id, experiment_id, kind, status, command_json, params_json,
  environment_id, git_commit, git_diff_hash, input_fingerprint,
  pid, exit_code, queued_at, started_at, finished_at, error

research_run_events
  id, run_id, seq, stream, event_type, payload_json, created_at

research_metrics
  run_id, key, step, value, timestamp

research_artifacts
  id, project_id, run_id, path, role, media_type, size,
  content_hash, metadata_json, created_at

research_environments
  id, project_id, kind, name, spec_json, lock_hash, last_verified_at
```

## 5. 文章撰写：文件为真源的 Manuscript Studio

### 5.1 编辑体验

- 左侧文章大纲与文件树；中间 Markdown/Quarto 编辑器；右侧即时预览、引用搜索或 Agent 修订建议。
- 输入 `@` 搜索当前项目 Library，插入稳定 citekey；悬浮显示论文元数据和阅读笔记。
- 从 Run/Artifact 面板插入图、表和结果块；系统自动建立 `inserted_into` 与 `derived_from` 边。
- Agent 修改通过现有 diff 审阅能力提交，不直接覆盖用户文稿。
- 编译问题显示为可定位诊断：缺失 citekey、交叉引用、图片路径、LaTeX/字体问题等。

### 5.2 文档与编译策略

推荐以 `.qmd` 作为默认真源，因为 Quarto Manuscripts 能把 `.qmd` 或 Notebook、图表、公式、引用和交叉引用组织为学术文章，并输出 HTML、PDF、DOCX/LaTeX 等格式。[Quarto Manuscripts](https://quarto.org/docs/manuscripts/)

引用使用 `@citekey`，项目维护 `references.bib` 或 `references.json`。Quarto Visual Editor 已证明 DOI、Zotero、Crossref 等来源可以统一进入这一交互模型；Workbench 可以在自己的 UI 中实现更轻量的同类体验。[Quarto citation authoring](https://quarto.org/docs/manuscripts/authoring/vscode.html)

编译层保持适配：

1. `QuartoCompiler`：默认与推荐；支持 manuscript、cross-reference 和多格式；
2. `PandocCompiler`：缺少 Quarto 时的 Markdown + CSL 引用回退，Pandoc 内置 citeproc 并接受 bibliography 与 CSL 样式；[Pandoc citations](https://pandoc.org/MANUAL.html#citation-rendering)
3. `TypstCompiler`：后续为快速 PDF 与模板化排版提供选项，Typst 也支持 Bib/CSL 引用。[Typst bibliography](https://typst.app/docs/reference/model/bibliography/)

MVP 不应自造排版引擎，也不应先做完整 WYSIWYG。现有 Markdown/KaTeX、代码编辑器、diff 和 PDF viewer 足以搭出专业的源文件 + 预览工作流。

### 5.3 关键数据模型与文件布局

```text
research_manuscripts
  id, project_id, title, root_path, entry_path, format,
  compiler, citation_library_id, status, updated_at

research_citations
  manuscript_id, citekey, paper_id, first_seen_at, last_seen_at

research_claim_links
  id, manuscript_id, source_path, anchor_json,
  target_type, target_id, relation, created_by
```

```text
workspace/
  research/
    library/          # 用户选择落地的 PDF、补充材料
    experiments/      # experiment.yaml、脚本、notebooks
    runs/<run-id>/    # 日志、manifest、输出（可配置忽略大文件）
    manuscripts/<id>/ # index.qmd、references.bib、figures、模板配置
```

文章源文件、实验 spec 和小型 manifest 应进入 Git；运行缓存、大模型权重和大数据默认不进入 Git，只保存内容哈希与外部位置。数据库是索引与状态，不是文章或实验文件的唯一副本。

## 6. Workbench 信息架构

MVP 先在现有侧栏增量加入三个模块，避免一次性重构所有导航：

| 模块 | 入口与主要视图 | 与现有模块的关系 |
|---|---|---|
| Library | Inbox、All Papers、Collections、Saved Searches、详情/PDF/笔记 | 从 Knowledge 演进；Knowledge 保留为“所有文件”，Library 是“论文视图” |
| Experiments | 实验列表、运行队列、运行详情、比较、环境 | 独立于 Task；Task/Agent 可创建或解释实验运行 |
| Manuscripts | 文章列表、编辑器、引用、预览、编译历史 | 复用代码编辑、diff、PDF viewer 和 Artifact |

项目 Dashboard 增加 Research Overview：待读论文、正在运行实验、失败运行、未解决引用、最近编译和下一里程碑。全局搜索扩展到论文、实验、运行、指标、文章段落与 citekey。

## 7. 后端模块与 API 边界

后续新增代码应遵循当前重构后的领域边界，不再采用旧版
`src/cyrene/research/` 或 `src/webui/routes_workbench_*.py` 布局：

```text
src/cyrene/knowledge/                 # 已有 Library 存储、导入与 Zotero 适配
src/cyrene/workbench/research/        # 后续跨科研对象的 Workbench 业务服务
  models.py
  provenance.py
  experiments/
  manuscripts/

src/route/workbench/
  library.py                          # 已有
  experiments.py                      # 后续
  manuscripts.py                      # 后续

src/workbench-webui/
  workbench-library.jsx               # 已有
  workbench-experiments.jsx           # 后续
  workbench-manuscripts.jsx           # 后续
```

API 使用当前 Workbench 前缀并保持领域隔离：

```text
GET/POST /api/workbench/research/papers
POST     /api/workbench/research/papers/import
POST     /api/workbench/research/integrations/zotero/sync

GET/POST /api/workbench/research/experiments
POST     /api/workbench/research/experiments/{id}/runs
GET      /api/workbench/research/runs/{id}
GET      /api/workbench/research/runs/{id}/events
POST     /api/workbench/research/runs/{id}/cancel

GET/POST /api/workbench/research/manuscripts
POST     /api/workbench/research/manuscripts/{id}/compile
GET      /api/workbench/research/manuscripts/{id}/citations
POST     /api/workbench/research/manuscripts/{id}/insert-artifact
```

科研状态应进入规范化 SQLite 表，不要继续膨胀 `workbench_state.payload_json`。文件内容仍在 workspace/附件目录；KB 继续负责全文索引；provider 与 compiler 都通过小接口注入，便于测试和替换。

## 8. 分阶段实施路线

下列周期按 1–2 名熟悉现有仓库的工程师估算，只用于排优先级，不是承诺排期。

### Phase 0 — Research Core（未开始，约 2 周）

- `research_*` 迁移与 repository；
- ResearchObject/ProvenanceEdge/ArtifactManifest；
- 项目权限、路径校验、审计事件；
- 统一 provider/runner/compiler protocol；
- 端到端 fixture 与迁移回滚测试。

**退出条件：** 能创建 paper、experiment、run、artifact、manuscript，并查询完整关系链。

### Phase 1 — Library MVP（部分完成）

- [x] PDF/普通文件、CSL JSON、BibTeX、RIS 导入；
- [ ] DOI/题名在线导入；
- [ ] Crossref + OpenAlex 搜索和补全；
- [x] collections、标签、阅读状态、笔记、批注、附件和条目关系；
- [ ] DOI/题名级去重与稳定 citekey 冲突策略；
- [x] Zotero Local API 项目级增量同步；
- [ ] Zotero Web API 与冲突语义；
- [ ] PDF 页面锚点笔记；
- [x] Agent 可列举、检索并更新已经核验的 Library 元数据。

**退出条件尚未满足：** 当前可从 PDF/书目文件导入并阅读做笔记，但 DOI
在线导入、页面锚点和唯一 citekey 仍缺失。

### Phase 2 — Experiments MVP（未开始，约 4–6 周）

- durable run queue、process supervisor、SSE 日志、取消与重启 reconciliation；
- Command + Papermill workload；
- uv/existing-venv 环境；
- 参数、指标、git/lock/data fingerprint、artifact manifest；
- 运行比较和失败诊断；
- Agent 工具：create/run/inspect/compare experiment。

**退出条件：** 同一实验可用不同参数重复运行，结果可比较，并能从任一 artifact 追溯完整输入与环境。

### Phase 3 — Manuscript MVP（未开始，约 4–6 周）

- `.qmd`/Markdown 编辑、outline、预览；
- `@citekey` 自动补全与未解析引用检查；
- 插入运行图表/表格并建立 provenance；
- Quarto/Pandoc 编译器、诊断、PDF/DOCX/HTML 下载；
- Agent 修改走 diff 审阅。

**退出条件：** 可把 Library 引用和 Run 产物写入文章，一键编译，并从文章元素反查来源。

### Phase 4 — 闭环与增强（未开始，约 3–4 周）

- Research Overview、统一搜索、影响分析；
- 保存检索与文献提醒；
- reproducibility bundle（文章、引用、spec、环境锁、run manifest）；
- Zotero 双向同步、MLflow import/export；
- E2E、崩溃恢复、性能与跨平台打包。

## 9. P0 / P1 / Later 范围

| 优先级 | 应做 | 暂不做 |
|---|---|---|
| P0 | 规范化科研对象、溯源边、Library 基础、命令/Notebook 运行、uv 环境、文章引用与编译 | — |
| P1 | Zotero 双向、运行比较增强、Jupyter 交互内核、MLflow 兼容、期刊模板、保存检索提醒 | — |
| Later | 远程 GPU/Slurm/Kubernetes、多人实时协作、实验室仪器、系统综述专用筛选、自动投稿 | 首版明确排除 |

## 10. 最大风险与规避

1. **把 Agent run 当 experiment run。** 两者状态机、权限和可复现要求不同；使用独立表和服务，只通过 provenance 关联。
2. **把所有科研元数据塞进 Knowledge `metadata`。** 会让去重、检索、同步冲突和引用完整性无法可靠实现；结构化字段必须规范化。
3. **LLM 生成不存在的引用。** 文章编辑器只允许插入 Library 中已解析的 citekey；Agent 建议引用时返回 paper ID，并在保存前校验。
4. **任意代码执行风险。** 每个新实验 spec 首次运行需用户确认；限制 cwd、输出路径与环境变量；取消终止进程组；容器隔离作为可选增强。
5. **打包体积与跨平台编译链。** 首版检测外部 Quarto/Pandoc 并提供明确安装诊断；验证价值后再决定是否随 Electron 捆绑。
6. **大文件与数据库膨胀。** DB 只保存元数据、哈希和位置；产物在文件系统或外部 artifact store；增加清理与保留策略。
7. **一次做成 Zotero + JupyterLab + Overleaf。** 先做贯穿三者的最小闭环，外部工具通过 adapter 渐进增强。

## 11. 端到端验收场景

首个完整版本应通过一条可自动化的黄金路径：

1. 用户输入 DOI，系统从 Crossref/OpenAlex 获取元数据并导入 PDF；
2. 用户在 PDF 第 4 页选中文字形成笔记；
3. Agent 基于论文与项目数据创建参数化 Notebook 实验；
4. 运行记录代码、环境、输入、参数、指标，并生成 `figure-1.png`；
5. 用户在文章中输入 `@citekey`，插入 `figure-1.png`；
6. Workbench 编译出 PDF 与 DOCX；
7. 点击文章中的引用能打开论文，点击图能打开对应 run；
8. 导出 reproducibility bundle 后，在干净环境中可验证依赖锁和运行 manifest。

## 12. 需要产品层确认的问题

1. 首批用户主要是计算型研究（Python/R/Notebook），还是也要覆盖湿实验/仪器记录？这决定 Experiment spec 的抽象深度。
2. 是否接受首版依赖用户安装 Quarto/Pandoc，还是桌面安装包必须开箱即用？这显著影响跨平台打包工作量。
3. Zotero 同步是“可选便利”还是“迁移必需”？若是必需，应把本地 API/云 API 冲突语义提前到 Phase 1。
4. 是否需要团队协作与远程算力？现有产品是单用户、本地优先；若近期就需要，应先重新设计认证、权限和 artifact storage，而不是在本地模型上硬叠功能。

## Caveats and Assumptions

- 本方案基于 2026-07-26 仓库状态与文中链接的公开官方资料；代码实现状态已经过本地源码与测试文件核对，但本次文档更新没有重新调用外部 provider 做在线验收。
- 周期估算未包含设计资源、Quarto/Pandoc 二进制再分发许可审查、Windows/macOS 签名和远程执行基础设施。
- “一站式”在本方案中指一个项目内的统一入口、上下文和溯源，不代表首版替代 Zotero、JupyterLab、MLflow 或 Overleaf 的全部高级能力。
