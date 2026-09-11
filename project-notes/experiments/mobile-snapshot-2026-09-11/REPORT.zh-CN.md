# Cyrene Android 完整后端快照实验（2026-09-11）

**结论：现有 Android QEMU 5.1 引擎能够保存、跨进程恢复完整 Debian/Cyrene 后端。三个有效样本中，从启动新 Android 进程到首次串口工具调用及认证健康检查完成为 4.729–5.079 秒，中位数 5.036 秒。值得继续做移动端休眠恢复原型，但尚未达到桌面就绪时间，也不是可直接发布的实现。**

本轮只新增独立实验脚本、Android 探针和证据，没有修改移动端或桌面业务代码。没有真实手机、私人数据或模型调用。

## 条件和测量口径

- 新建 API 35 ARM64 空白 Android 模拟器：8 GiB 内存、32 GiB 数据盘，`SnapshotResearch`，`emulator-5582`。
- 探针包名 `ai.cyrene.research.snapshot`，targetSdk 35；复用当前仓库 ARM64 QEMU JNI 库，QMP 实际返回版本 5.1.0，库摘要见 [environment.json](environment.json)。
- Debian 来自上一轮 beta18 预览 APK 内镜像 `debian12-desktop-aarch64-unified-5257e123a5a3`。没有从原应用复制数据库、账户或用户工作区。它不是最新主仓库重新构建的后端。
- 与当前移动端相同的 ARM virt/cortex-a72、4 vCPU、TCG 多线程、4 GiB guest 内存、内核启动参数和网络设备。实验把 raw 根盘转换为 qcow2，并增加 QMP 与本地 TCP 串口，以便宿主采集；没有接入产品的生命周期管理。
- 计时在 `am start` 前开始，包含启动新 Android 进程、加载快照、串口执行 `cat`/`systemctl` 和认证 HTTP 健康检查。每轮前 force-stop 探针；不是同进程内恢复。
- 不含 APK 解包、签名/资源校验、WebView 首屏、模型首 token、全工具能力验收。没有清空宿主或 Android 文件缓存。

## 有效结果

| 样本 | QMP 可操作 | 首次真实串口命令完成 | 再完成 HTTP 健康检查 | 首次命令本身 |
|---|---:|---:|---:|---:|
| 1 | 4.333 s | 4.945 s | 5.079 s | 0.607 s |
| 2 | 4.232 s | 4.857 s | 5.036 s | 0.618 s |
| 3 | 3.996 s | 4.585 s | 4.729 s | 0.586 s |

三次 guest boot ID 都为 `e92af661-66f3-4dd5-ac88-9fc49ba94f0d`，后端 MainPID 都为 `324`，与保存前相同。认证健康检查均返回 200/status=ok，证明恢复了既有 Linux 和后端进程。

每轮读出上一轮文件值，再写入新值；独立 SQLite 数据库使用 WAL/FULL 提交，记录从 v2、v3 连续增加到 v4，每轮 `quick_check` 返回 ok。这是受控持久化检查，不等于完整用户数据库迁移、断电耐久性或所有副作用语义验证。

首次快照保存耗时 2.962 秒；三次更新快照分别 12.903、2.076、1.925 秒，存在明显波动，不能只报最快值。QEMU 报告每份 VM 状态约 753 MiB；不包含根盘，也不是总磁盘增量。4 GiB 配置内存不意味着每次都写出 4 GiB。

证据：[save.json](save.json)、[restores.json](restores.json)、[restore_probe.py](restore_probe.py)。

上一轮完整预览 APK 后端冷启动中位数约 157 秒，桌面约 1.817 秒，可作背景参考；本轮是独立探针，绕过产品资源校验等步骤，不能据此宣称 App 端到端加速了某个精确倍数。本轮早期冷启动计时因探针认证头和本机代理问题剔除，没有制造一组“可比”的冷启动数字。

## 真实终端与 WebSocket

通过完整后端的项目 API 创建实验项目，再经终端 API 创建 Bash，使用 WebSocket 设置仅存在于 shell 内存中的环境变量。保存整个 VM，force-stop Android 探针，再启动新进程加载快照、重连 WebSocket，执行引用该变量的新命令。

结果通过：从启动到收到新命令输出为 **4.957 秒**（单次扩展样本）。终端 ID 和 Bash PID=450 保持不变，新命令输出 `AFTER_memory_keep_20260911`。该字符串不存在于恢复前的输出，也不是命令回显，证明恢复了原 Bash 内存状态。WebSocket 本身是重新连接，未声称跨进程保留原 TCP 连接。

证据：[terminal.json](terminal.json)、[terminal_probe.py](terminal_probe.py)。这比单独健康检查覆盖更多，但没有测试全部工具、模型流、外部网络、PTY 取消/并行、长期后台或 WebView UI。恢复后的日志时间仍来自旧 guest 状态；时间校正需要专门处理。

## 强杀与旧快照回退

实际执行：保存含 v4 的快照 → 写入并提交 v5 → force-stop Android 探针。

- 不加载快照、直接从当前磁盘冷启动：文件和 SQLite 均保留 v5，SQLite quick_check=ok。18.908 秒仅为 Linux 串口就绪时间，不是完整后端就绪时间。
- 随后加载旧的 v4 快照：文件和 SQLite 都回到 v4，已提交的 v5 消失，quick_check 仍为 ok。

因此，“数据库没有损坏”不等于“用户数据没有丢失”。不能在每次启动时无条件 `loadvm latest`；快照包含可写磁盘状态，加载旧快照会回退磁盘。

证据：[crash.json](crash.json)、[crash_probe.py](crash_probe.py)。上述回退只发生在一次性实验数据中。

## 接入产品的必要边界（尚未实现）

1. 使用一次性、与当前磁盘匹配的休眠检查点。恢复前先持久化标记“已消费/运行中”，再允许 guest 执行；运行期间被强杀时走当前磁盘冷启动，不能复用旧检查点。
2. 生成检查点前停止接收新任务，让关键持久化完成，再暂停 VM、保存状态。保存后保持暂停并退出，避免 `savevm` 返回后 guest 继续写盘，却仍把检查点标记为最新。实验脚本故意没有实现产品级状态机。
3. 检查点与 QEMU 版本、guest 镜像、CPU/设备配置、磁盘版本绑定。升级、缺失或不一致时回退冷启动，不能强行加载。
4. 恢复后重建 Android 侧连接、校正时间并检查网络。不能假设暂停期间外部 HTTP/模型流、远程会话、令牌有效期和系统时钟都没有变化。进行中的外部副作用需要单独设计恢复语义。
5. Android 后台回调和进程寿命不足以保证总能完成保存，尤其已经观测到一次约 13 秒的保存。必须允许“没有有效快照”的正常路径。
6. 当前实验没有证明首次安装能快速启动。预置模板须在接入用户数据库之前定义专用初始化边界，不能用恢复整个旧用户环境的方式实现。

可优先将移动端 VM 管理层做成“有效休眠检查点 → 恢复；否则 → 冷启动”。这可以保留 Linux 和共享后端；桌面不必接入 VM 休眠路径。但 TCG 执行开销仍在，快照不等于原生计算性能。

## 失败与排除项

- 首轮脚本误用了 Bearer 认证，而后端使用 `X-Cyrene-Token`；第二轮还受本机 HTTP 代理干扰。都未计入性能结论。最终 HTTP 使用空 ProxyHandler 直连 localhost。
- 直连刚切换时有一次 2 秒健康超时；随后 guest 内健康请求 200（约 0.607 秒），宿主转发请求 200（约 0.088 秒），再保存快照。首次准备过程没有有效的完整计时。
- 首次终端探针使用未创建的 projectId，POST 返回 503，日志明确是 `LookupError: Project not found`；改为通过项目 API 创建。指定 `/workspace` 的请求返回 400，改用后端默认工作区。终端输出是 Base64，初版探针未解码导致等待超时，之后按协议修正。这些是探针准备问题，不计作恢复性能样本。
- [pilot-invalid-health-header.json](pilot-invalid-health-header.json)、[cold.json](cold.json) 只保留 Linux 引导日志和阶段时间；不是有效后端冷启动成绩。
- [backend-diagnostic.json](backend-diagnostic.json)、[terminal-error-filtered.json](terminal-error-filtered.json) 保留诊断。日志中持续出现 watchfiles change 消息，未在本轮定位或修改，不据此推断根因。

## 复现入口

`prepare.py` 从指定实验 APK 提取系统镜像，以 Android SDK 自带 qemu-img 转为 qcow2，并复制主仓库 JNI 桥接/库、创建独立 AVD 配置。大镜像、JNI 副本和构建缓存不纳入实验源码。

依次启动 `/tmp/cyrene-snapshot-avd` 内的 AVD、用 Gradle 构建 `harness`，执行 `probe.py install`、`probe.py cold`（修正后的版本）、`restore_probe.py`、`crash_probe.py`。本次因冷启动采集器修正，实际保存步骤使用 `save_existing.py`。终端扩展实验执行 `terminal_probe.py`，需要项目环境中的 websockets。

所有 adb 操作固定指向 `emulator-5582`，安装前检查 AVD 名称。只应在本实验空白 AVD 使用这些脚本；强杀/旧快照实验会故意回退测试数据。

最终核对：实验脚本 Ruff、Python 语法、全部 JSON、三轮恢复连续性、强杀回退断言、终端内存变量断言和报告链接检查通过。没有运行产品测试，因为没有修改产品代码。实验模拟器已关闭，大镜像与构建缓存已清理，保留源码、原始结果及 `/tmp/cyrene-snapshot-research.apk`。
