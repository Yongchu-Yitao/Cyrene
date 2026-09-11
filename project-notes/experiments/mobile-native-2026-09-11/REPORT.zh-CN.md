**Cyrene 移动端接近桌面体验：实验研究，2026-09-11**

本轮结论：保留真实 Linux VM；优先复用当前 Workbench 和共享 Agent 微内核，逐步让核心在 Android 原生执行。0.2.5 的职责划分值得借鉴，不能直接回退其业务实现。精简 Linux 核心可作为过渡方案，但仍需证明完整业务能力与桌面一致。

本轮做了实际构建、Android 模拟器运行、真实工具与恢复校验，不是仅根据代码推测。没有修改产品业务源码，没有调用付费模型，没有使用私人会话或模型配置。真机未连接，因此本文所有 Android 数字都是模拟器结果，不能代替目标手机验收。

**实验条件与可比较范围**

- 桌面：本机 ARM64 macOS、项目 Python 3.12.11、当前主仓库提交 `254a91b700d7ed9797f43db08f56c106f42c9629`。原有未提交前端改动保留。
- Android：专门创建的 API 35 ARM64 AVD，实际 MemTotal 8,129,460 kB，32 GiB 全新数据盘，序列号 `emulator-5582`。所有应用数据为空白实验数据。
- 旧版：从独立仓库标签 `v0.2.5`（`f5b6c5b2368ad745ed4d6a4af5a0fc4cea2e9295`）导出构建，仅向 Debug 探针添加计时，VM 与工具生产逻辑不变。Alpine x86_64、1 vCPU、256 MiB、512 MiB 逻辑根盘。
- 完整版：从已有测试 AVD 提取 APK 本体，再安装到空白 AVD；没有复制其应用数据。版本 beta18，镜像 `debian12-desktop-aarch64-unified-5257e123a5a3`，ARM64、4 GiB guest、8 GiB 逻辑根盘。不是本次最新主仓库源码重新构建的 APK。
- 原生原型：独立包名 `ai.cyrene.research.nativeprobe`、targetSdk 35、Chaquopy 17.0、ARM64。完整依赖构建失败；最小解释器对照构建成功。
- 不清空 OS 页缓存。首次样本含安装/预编译等影响，并有少量构建或安装并发，视为探索样本；后续冷启动样本没有并发构建或另一项性能实验。
- 桌面与 guest 软件版本、操作系统、运行环境不同。结果用于分解架构成本，不能直接给出“TCG 慢了多少倍”的纯因果结论。

**1. 旧 VM 的快速就绪可以复现**

`SESSION_MOUNT` 包括镜像处理、工作盘准备、VM 引导和工具通道就绪：首次 5.540 秒；后续三个新进程为 3.865、3.534、3.704 秒，中位数 3.704 秒。

每轮都验证了真实 guest 架构、跨会话文件写入/读取、产物导出、普通命令、12 KiB 长命令与退出码。普通 `uname/printf` 命令约 98–113 ms；小文件读约 127–154 ms。这是有限工具样本，不代表编译、Node、浏览器等重负载的速度。

旧版同样使用 TCG，且在 ARM64 上模拟 x86_64；每条命令后也执行 sync。因而“存在 TCG/全局 sync 就必然启动数分钟”不成立。旧版在 Android 执行 Agent 与模型调用，VM 不启动全套 Python 应用，这是关键架构差异。

证据：[旧版原始结果](old-runtime-results.json)、[可复现计时补丁生成器](prepare_old_probe.py)、[Android 采集器](android_probe.py)。

**2. 完整 Debian 应用的慢启动在四核版本仍然存在**

| 场景 | 后端认证就绪 | 后端就绪后到页面加载完成 | 从启动阶段开始到页面完成 |
|---|---:|---:|---:|
| 首次安装准备 | 187.019 s | 13.590 s | 200.609 s |
| 冷启动 1 | 159.970 s | 11.383 s | 171.351 s |
| 冷启动 2 | 156.635 s | 11.292 s | 167.926 s |
| 冷启动 3 | 157.060 s | 10.307 s | 167.358 s |

后续冷启动是对实验应用执行 force-stop 后重开，包含 guest 在非正常退出后的恢复，不是正常 guest 关机。服务启动时间取日志内部计时，跨阶段差值取日志时间戳，两者可能有毫秒级差异。

后三次后端就绪中位数 157.060 秒。资源验证约 12–15 秒，Linux 引导约 20–23 秒，进入 backend 阶段后仍需约 120–125 秒。页面加载事件是 WebView 回调，不等同于所有功能可操作或真实模型首 token。

就绪后 runtime 进程 PSS 约 1.66–1.72 GiB，采样时 TOTAL SWAP 为 0；因此这组结果不能用此前低内存模拟器的持续换页解释。只测了就绪时内存，不代表长时间后台或复杂任务的峰值。

桌面全新数据首次后端健康检查成功为 2.577 秒，后三次新进程为 1.843、1.784、1.817 秒。该指标不含 Electron 首屏，也不含真实模型调用；桌面日志显示默认挂载/启动 23 个 pack，不应把发现的全部插件数量当作实际启动数量。

证据：[预览版完整阶段和内存](preview-startup-results.json)、[桌面结果](desktop-startup-results.json)、[桌面分阶段日志](desktop-stage-timings.txt)、[镜像清单](preview-image-manifest.json)。

**3. 不启动完整应用，也能运行当前共享 Agent 微内核**

[微内核探针](core_session_probe.py) 使用真实 `AgentSession`、真实 Write/Read 工具与持久化文件，固定模型响应为“写文件→读文件→最终回答”，然后关闭并重新打开会话，验证最终回答仍存在。模型传输是确定性替身，没有访问 LLM；不等同于完整模型/provider/插件集成测试。

单个 guest 内的三个独立 Python 进程，完整脚本分别用时 6.189、4.922、5.148 秒；其中实际三轮模型调度和两次工具调用为 1.740、1.623、1.661 秒。每轮文件、模型收到的工具结果与会话恢复都通过断言。脚本计时不含 Linux boot；第一轮 Binder 总耗时包含 VM mount，不能拿脚本的 5 秒当作 App 冷启动。

这证明当前共享微内核可以脱离完整 Workbench 应用插件启动。它支持先做一个轻量 Linux 宿主的过渡实验，但尚未证明去掉其他插件后仍满足所有桌面功能。长远看，调度与读写带来的秒级开销仍支持原生执行核心的方向。

第一次连续调用两个 Binder 探针时，第二次出现 `Unable to boot signed Linux VM: null`。该失败原样保留，不能声称 VM 生命周期已可靠。随后用一次 VM 内的独立 Python 子进程重复实验，消除探针复用影响；没有修复或掩盖产品生命周期问题。

证据：[三个微内核样本](guest-core-results.json)、[包含失败的首次试验](guest-core-pilot-with-reuse-failure.json)、[guest 采集器](guest_probe.py)。

同一探针在桌面执行三次，总耗时 185、133、142 ms，文件和恢复断言同样通过。由于桌面与镜像内核心版本不同，此结果只作为共享微内核的桌面参考，不用于计算纯 TCG 倍率。[桌面微内核结果](desktop-core-results.json)

另外执行了相同的离线负载脚本，结果如下（各项内部重复采样的中位数，单位 ms）：

| 负载 | 桌面 Python | ARM64 guest Python |
|---|---:|---:|
| 新 Python 进程执行 pass | 12.262 | 272.529 |
| 新进程导入 jsonschema | 52.225 | 1584.454 |
| 500 份工具结构做 50 次 JSON 往返 | 26.902 | 528.067 |
| 16 MiB SHA-256 | 5.894 | 64.001 |
| 500 个小文件 stat + 内容哈希 | 7.748 | 380.321 |
| Bash 启动并 printf | 2.824 | 56.654 |
| Node 启动并输出 | 21.192 | 407.470 |
| 空仓库 Git status | 6.194 | 64.176 |
| SQLite WAL/FULL 单事务 | 0.043 | 4.700 |

这是组件负载，不是整机/真实 Agent 基准。OS、CPython 微版本、文件系统与缓存不同；SQLite 的 FULL 设置也不证明两端断电持久化语义相同。guest 单个简单事务仍是毫秒级，不能把此前秒级 SQLite 锁等待直接解释为每次提交都很慢。优先减少重复导入、扫描、JSON 构造和进程启动，比只改变 UI 线程参数更有依据。

证据：[相同负载脚本](workload_probe.py)、[桌面负载](desktop-workload-results.json)、[guest 负载](guest-workload-results.json)。

**4. 原生 CPython 很轻，但依赖移植尚未打通**

最小 Android 原型的解释器启动（Activity onCreate 起计，含原生加载/准备）四次为 231、260、280、229 ms。加载 sqlite3/ssl/httpx/aiosqlite，并执行 100 次 SQLite 提交、关闭重开和 quick_check，整个原型为 411–487 ms。四次新进程的事件计数依次为 100、200、300、400。

这些数字只说明原生解释器及少量依赖可工作。该原型不含 Agent、FastAPI、完整插件、真实 API 流式传输或 Workbench，不能宣称 Cyrene 已实现半秒启动。

实际尝试保留项目约束进行构建：

| 尝试 | 结果 |
|---|---|
| Python 3.12 + FastAPI/Pydantic 当前下限 | pydantic-core 无匹配 Android 分发包，构建失败 |
| Python 3.13 + 同样依赖要求 | 同样在 pydantic-core 失败 |
| Python 3.13 + JSON Schema + 共享源码，不引入 FastAPI | rpds-py >= 0.25.0 无匹配分发包，构建失败 |
| Python 3.13 + httpx/aiosqlite/标准库 | 构建和四次 Android 运行成功 |

失败范围是本次 Chaquopy 配置与所用 PyPI/Chaquopy 索引，不证明这些包无法交叉编译。没有通过降低 Pydantic/JSON Schema 要求或移除校验来制造成功结果。下一项原生可行性门槛应是受维护的 ARM64 Android `rpds-py` 与 `pydantic-core` 构建，而非移植全部桌面依赖。

静态 AST 审计发现 `cyrene/core` 共 62 个 Python 文件，唯一直接第三方导入是 jsonschema；仍有对 settings_store、路径和子进程环境的跨层引用。静态审计不覆盖动态导入和完整传递依赖，不能据此断言只补一个 wheel 就能运行全部业务。

证据：[原生结果](native-python-results.json)、[原型源码](native-probe)、[核心导入审计](core-import-audit.json)、[3.12 失败日志](native-python312-build-failure.txt)、[3.13 失败日志](native-python313-build-failure.txt)、[JSON Schema 失败日志](native-jsonschema-build-failure.txt)。官方说明：[Python Android 嵌入](https://docs.python.org/3.15/using/android.html)、[Chaquopy 构建与依赖配置](https://chaquo.com/chaquopy/doc/current/android.html)。

**5. 推荐的架构与迁移边界**

保留同一套 Workbench 前端和共享 AgentSession。Android 承担本地资源、原生浏览器与系统集成；核心宿主负责会话、权限、模型协议和事件状态；Linux VM 承担 Bash、Git、Node、PTY、MCP 子进程以及依赖 Linux 的重型工具。

近期先把“轻量核心宿主”从“完整应用初始化”中拆出来。它可以先运行于 Linux，以现有兼容性验证完整聊天、恢复和工具闭环；同步解决原生依赖门槛，再把同一份核心代码移到 Android CPython。保持明确接口，避免为两种宿主复制一套业务实现。

| 能力 | 放置建议 | 必须保持的桌面语义 |
|---|---|---|
| Workbench | APK 本地资源 + WebView | 同一会话/工具卡片/产物/设置行为，API 版本可协商 |
| Agent、模型协议、权限、上下文 | 共享核心；目标为 Android 原生 CPython | 多轮工具历史、重试、审批、取消、恢复一致 |
| 会话与事件 | 核心宿主的唯一权威存储 | 事件游标连续，恢复不重复执行副作用 |
| 工作区文件 | 初期保持 guest 权威，通过显式文件接口访问 | Read/Write/Bash 看见同一份文件，不做双向目录同步 |
| Bash/Git/Node/PTY/MCP 子进程 | Linux VM | 流式 stdout/stderr、stdin、信号、每个命令独立取消 |
| 网页浏览、麦克风、文件选择 | Android 平台能力 | 沿用相同工具协议，权限与结果结构一致 |
| Office/OCR/语音等重依赖 | 按能力选择 native 或 guest，按需启动 | 用户首次使用确实可用，失败可见且可重试 |

不能直接复用旧版所有底层协议：现有 `QemuRuntimeManager` 对 EXEC_WAIT/EXEC_STDIN 返回 unsupported，串口 execute 是同步的，cancel 会关闭 VM。若原生 Agent 通过这条通道运行所有工具，会限制并行、终端交互与精确取消。因此需要一个支持请求 ID、事件流、独立进程生命周期的 guest worker；不能仅把核心移出 VM 就宣布桌面体验对齐。

旧版的六个固定执行工具与远程桌面功能组合，不等于当前完整本地插件能力。恢复旧 Compose/Kotlin Agent 全栈会重新引入两套业务实现，不是本轮推荐路线。

**验收应覆盖速度和功能，不只看 am start**

第一道门：同一共享微内核在桌面、轻量 guest、Android 原生宿主上通过同一组确定性运行轨迹：多轮工具、审批拒绝/允许、错误重试、取消、不重复执行、关闭重开、产物读取、两任务并行。此次只完成其中的写/读/最终回答/恢复子集。

第二道门：接入真实 provider 与共享 Workbench，比较“启动到可发消息”“发送到请求发出”“首 token 后的显示延迟”“工具结果到下一轮模型请求”的本地附加耗时。使用同一模型和请求集合，将网络/provider 耗时单列。

第三道门：在目标真机验证首次安装、进程冷启动、VM 存活时 UI 重建、后台恢复、低内存、安装升级与旧数据迁移。至少覆盖目标 Android 版本及一台低内存设备。模拟器三次样本不足以报告稳定 p95。

速度目标应在同机桌面/手机实测后正式签定。当前可以明确的是：面向桌面体验的核心就绪需要秒级目标；现有约 157 秒的完整后端启动不满足要求。轻量 VM 的 3.7 秒与原生解释器的 0.25 秒是分别测得的组件结果，不能相加当作新架构的已实现启动时间。

**失败、清理和复现说明**

首次尝试使用旧测试 AVD 的只读临时实例，创建空白用户后资源准备失败，数据分区达到 100%；该样本未进入正式性能表。随后关闭只读实例，使用 /tmp 新建的 32 GiB 空白 AVD 重测。原 AVD 用户数据盘未保存本轮实验变化，没有修改用户手机数据。

实验结束已关闭两个本轮启动的模拟器，删除新建的大型临时 AVD 磁盘和原型构建缓存。保留报告、原始 JSON、构建失败日志与可复现源码。实验脚本静态检查通过；结果文件已完成 JSON 解析、样本数量、成功断言和报告链接核验。未运行产品全量测试，因为产品业务源码没有改动。

复现入口为本目录的 desktop_startup.py、android_probe.py、preview_startup.py、guest_probe.py 和两个负载脚本。Android 采集器限制到本轮专用 emulator-5582；不要改为真实用户设备直接运行。

原生原型完整依赖模式默认开启，会复现已记录的依赖失败。最小成功对照需 `-PprobeCoreDependencies=false`；微内核依赖探索额外设置 `-PprobeSchemaDependencies=true` 和源码目录。必须在报告中保留这些模式区别。

复现示例（在主仓库根目录；路径按本机环境调整）：

```sh
.venv/bin/python project-notes/experiments/mobile-native-2026-09-11/desktop_startup.py --output /tmp/cyrene-new-desktop-measurement
.venv/bin/python project-notes/experiments/mobile-native-2026-09-11/android_probe.py --kind old --output /tmp/cyrene-old-results.json
.venv/bin/python project-notes/experiments/mobile-native-2026-09-11/guest_probe.py --script project-notes/experiments/mobile-native-2026-09-11/core_session_probe.py --runs 1 --repeat-in-guest 3 --output /tmp/cyrene-core-results.json
```

旧版需先将 `git archive v0.2.5` 导出到独立 /tmp 目录，运行 prepare_old_probe.py 后构建 app/runtime-app 的 assembleDebug。原生项目使用本仓库 Gradle wrapper，并通过 CYRENE_RESEARCH_PYTHON 指定匹配 3.13 的构建解释器。完整启动采集脚本第一轮是附着到已开始的首次启动；复现时应使用同样的新建空白 AVD，避免混入旧日志。
