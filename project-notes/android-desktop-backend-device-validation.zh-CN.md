# Android 桌面后端移植实验记录（2026-09-09）

## 当前结论（ARM64 本地模拟器）

用户已断开手机，当前验证对象为 `Cyrene_ARM64_8G`，Android 35 ARM64、8 GB RAM。
Linux 客体为 Debian 12 ARM64 / Python 3.12，单核 Cortex-A72 TCG、2 GB RAM。

- ARM64 完整镜像构建、签名及哈希验证通过。
- 主应用 → 签名保护 Binder → 前台服务 → Linux → Python 后端的完整启动链路通过。
- 连续状态检查通过；无令牌 HTTP 返回 401，有效令牌返回 200。结果保存在
  `build/desktop-arm64-binder-success.json`。
- 本轮含缓存重建约 5 分钟，不能作为可接受的日常启动性能。
- 修改后多轮短诊断和本轮后端关闭未新增原先的 native SIGSEGV；
  仍未完成长期、同进程连续会话压力验证。
- 文件/SQLite、Bash、Git、Node、uv、Codex CLI 版本命令通过。MCP 2.x 探针兼容问题已修正，
  最终工具组合测试触发 QEMU 原生 SIGILL，未产生通过报告；MCP 和 Chromium 不计通过。
- 重复启动仍不稳定，Python 优雅关闭未通过；会话迁移和移动端原生能力桥接未完成。
- 最新主应用默认打开桌面 Workbench，真实 Gemma4 聊天和刷新后记录保留通过；UI 未做优化。
  详见 `android-workbench-integration.zh-CN.md`。

以下保留各阶段历史结果，早期失败不代表最终链路状态。

目标：保留桌面 Python 后端能力，先完成运行时工程和真机验证，不优化 UI。
用户指定按 8 GB 机型预算；当前连接设备为 OnePlus PLZ110、Android 16/API 36，
MemTotal 11,284,960 kB（约 12 GB 标称内存），并非真正的 8 GB 设备。
实验 Linux 客体配置 2,048 MiB 内存、8,192 MiB 持久化磁盘、单核 x86_64 TCG。

## 已验证

- 主应用和 Runtime debug APK 均通过 `adb install -r` 覆盖安装，未卸载或清除现有数据。
- Android 相关单元测试与两个 APK 构建通过；Python 构建逻辑测试 2 项通过。
- 新代码搭配原有 Alpine 镜像的真机回归通过：启动、网络、跨会话共享文件、
  文件导出、超过 12 KB 的串口命令。结果位于 `build/desktop-alpine-device-regression.json`。
- Debian/Python 构建阶段完成依赖一致性检查和关键模块导入；Playwright 所需
  Chromium、FFmpeg、Headless Shell 已下载。使用 Google 官方 Chrome for Testing
  源绕过 Playwright CDN 超时，安装后恢复下载器代码。

## 待验证

完整镜像已封装和签名，桌面 Runtime APK 构建通过并安装，约 1.8 GiB。
镜像版本 `debian12-desktop-x86_64-v1-1158dc16ee71`，磁盘压缩资产 1,809,698,485 字节。
首次测试 14:30:08 启动，14:32:15 Linux 与网络就绪；之后 Python 认证健康检查通过。
14:38:35 返回工具测试失败，具体错误正在第二轮补充日志中定位。
首次启动阶段 PSS 约 1.1–1.2 GiB，包含约 250–530 MiB Swap PSS，不能当作峰值或 8 GB 基准。
主应用到 Runtime 的关联启动被一加系统阻止；APK 签名权限已核实授予。
Runtime 前台调试入口可启动，同一条 Binder/前台服务调用链仍待验证。
打包时为 gzip 资源关闭重复压缩，解决 Android asset worker 的 Java heap space 错误。
尚未证明真实模型调用、Subagent、重启恢复、长时间后台运行或 8 GB 设备性能。
现有聊天仍使用 Kotlin local agent；新增 DesktopRuntimeClient 尚未成为默认执行链。
模型配置和会话迁移、移动端音频/文件与 Office 桥接仍未完成。ARM64 引擎进展见下文。
不得将基础 Alpine 成功误报为桌面端完整移植成功。

实现说明及复现命令：`runtime-image/desktop/README.md`。

## 路线切换

用户要求直接实验 ARM64，并已断开手机，改用本地 Android 模拟器。
x86_64 第二轮在 `testing_tools` 阶段被主动停止；Python 后端就绪时间记录为
14:43:27（本轮开始于 14:40:04），没有工具最终结果，不能视为通过。
上游同版本 ARM APK 的 AArch64 引擎已接入，两种 Android ABI 的共享 JNI
依赖逐字节一致。ARM APK SHA-256 为
`ae22305b5f79815f738b752a6cfa0b00e6bdd80334ff3df8fb986ed4c1d1442b`。
新增 ARM64 构建选项、virt 机型和 ttyAMA0 串口适配，镜像已构建完成。
独立 AVD：Cyrene_ARM64_8G，Android 35 ARM64，8 GB 内存、16 GB 数据分区。
模拟器结果不能直接作为该手机的性能结果。

## ARM64 模拟器初轮结果

- ARM64 镜像构建、签名和资产哈希验证通过，版本
  `debian12-desktop-aarch64-v1-7efc68748c75`。
- AVD 实测 MemTotal 8,129,460 kB；Runtime 和主 APK 安装通过。
- Binder 可关联启动，前台服务正常建立；主应用认证请求尚未通过，因为后端启动超时。
- `max` 与 `cortex-a72` 两轮 Linux 和网络均就绪，但 Python 健康检查在 300 秒内未通过。
- 两轮关闭复现 SIGSEGV；原生回溯显示卸载 QEMU 后在线程退出的
  `pthread_key_clean_all` 调用失效的 TLS destructor。已调整为进程生命周期工作线程，待复测。
- 稀疏磁盘复制已实现并通过 2 个内容/长度测试；每份 8 GiB 磁盘实际约 4.1 GiB。
- 大型 APK 打包需要本次构建使用 6 GiB Java 堆；未修改用户默认 Gradle 配置。
- 最终功能与退出稳定性仍在诊断，不能宣称 ARM64 移植通过。

## ARM64 后续诊断

- 加入 virtio RNG，减少无界面客体的随机数初始化等待；避免重复校验同一磁盘。
- Python `cyrene.config` 实测导入约 5.8 秒；共享文件、导出和长串口命令继续通过，
  记录见 `build/desktop-arm64-import-probe.json`。
- 客体 journal 已确认 Python 后端进入服务生命周期；此前健康超时不能直接解释为
  Python 未启动。观察到密集 watchfiles 变更日志，HTTP 连通性仍需定位。
- 保留进程生命周期原生工作线程后，短诊断流程未再观察到同类 SIGSEGV；这不构成
  连续多会话稳定性证明。
- 主程序完整导入诊断在 45 秒限制内仍处于 Office 插件加载，返回 timeout 124，
  不能将其报告为完整后端导入成功。见 `build/desktop-arm64-backend-import-probe.json`。

- 17:23 启动的新一轮已通过 `DESKTOP_START` 的认证健康检查，随后立即执行的
  `DESKTOP_STATUS` 返回未就绪，主探针因此失败。这与早期启动总超时不同，
  结果见 `build/desktop-arm64-binder-one-second-result.json`。
- 将单次健康检查从 1 秒放宽到 5 秒（总启动预算保持不变），保持启动轮询间隔
  1 秒；debug HTTP 401/200 探针允许 15 秒。相关单元测试和两个 APK 构建通过，
  记录见 `build/desktop-arm64-health-build.log`。

- 独立工具探针第一次被 VM 磁盘所有权锁拒绝，原因是主应用后台仍有 Binder
  绑定，并非镜像启动失败。结束模拟器主应用连接、等待服务关闭后再运行。
  不通过删除锁文件或绕过互斥来执行测试。

- 独立完整工具探针第二轮在 Linux 就绪后约 5 分钟仍停留于后端启动，
  17:48 左右主动结束模拟器 Runtime 调试进程，改跑不依赖后端 HTTP 的
  ARM64 客体工具探针；中断轮次不计为成功。认证链路曾通过，但重复启动
  仍不稳定，不能将 5 秒健康超时调整视为问题已完全解决。

## 客体工具诊断

`build/desktop-arm64-direct-tools-result.json`：文件/SQLite、Bash、Git、Node、uv、
Codex CLI `--version` 均通过；MCP 子进程因探针使用 FastMCP 旧接口退出。
镜像实际安装 `mcp==2.2.0`，探针已改为优先导入 `MCPServer`，兼容旧版回退。
参考 [MCP SDK 官方接口](https://py.sdk.modelcontextprotocol.io/api/mcp/server/mcpserver/server/)。
这次失败发生在 Chromium 之前，不能计为浏览器测试失败或通过。

服务 journal 还确认：23 个插件启动完成且 startup_failures=0，认证成功轮次
监听 4242；停止时 5 秒 systemd 期限仍会触发 SIGKILL。此前“正常停止”只说明
控制调用返回，不能据此证明 Python 优雅关闭。后续需延长/协调服务和 Binder
停止预算并验证数据完整性。

## 最终阻断：原生 SIGILL

18:13:33，修正 MCPServer 和 `is_error` 两处 MCP 2.x 探针兼容后，
工具组合测试中的 `CyreneNativeVM` 线程触发 `SIGILL / ILL_ILLOPC`。
回溯落在匿名代码区以及 `base.apk!qemu-system-aarch64 + 0x9cb554`。
这是宿主 Android Runtime 进程的原生崩溃，不是普通 Python 异常。
证据：`build/desktop-arm64-final-native-crash.log`。最终 JSON 未生成，
`probe-result.json` 中的旧结果不能算本轮结果。尚不能仅据此断言具体是哪一
个工具触发，也不能宣称 MCP 或 Chromium 已通过。

当前结论：ARM64 桌面后端在 8 GB Android 模拟器上已证明能启动并完成认证请求，
基础命令与存储可用；但旧 Limbo/QEMU 引擎在完整工具负载下仍有原生崩溃，
并存在启动和关闭稳定性问题。下一步应先定位/更新 ARM64 QEMU 引擎并为
每个工具独立保留进度，再处理默认聊天接入、会话迁移和原生桥接。

本轮已停止继续重复测试；模拟器保留，未连接或操作已断开的手机。

## 模拟器灰屏修复

Mac 模拟器窗口灰屏时，ADB 截图仍显示 Android 桌面；宿主日志反复报
`Failed to make display surface context current` / `Failed to bind to post worker context`。
将 AVD 配置 `hw.gpu.enabled=yes`、`hw.gpu.mode=host`，并使用 `-gpu host`
替换先前命令中的 `-gpu swiftshader_indirect`，保留数据冷启动。
新进程约 14 秒启动完成，原先显示上下文错误不再出现；两个应用仍安装。
配置备份：AVD 目录下 `config.ini.before-display-fix`。
日志：`build/emulator-display-fixed.log`。该修改针对 Android Emulator 窗口显示，
并不解决 Linux 客体 QEMU 的 SIGILL。
