# Android 启动优化与验证记录（2026-09-10）

## 已确认的问题

- `Cyrene_Browser_Check_8G` 的 AVD 配置使用了 `hw.ramSize=8G`。SDK 的硬件属性定义要求整数 MB；运行实例的 `hardware-qemu.ini` 实际为 `2560`，Android `/proc/meminfo` 的 MemTotal 为 2,531,992 kB。
- 采样时 MemAvailable 为 255,624 kB，Cyrene QEMU 进程 SwapPss 为 901,557 kB。十秒内 pswpout 增加 27,174，pswpin 增加 83，pgmajfault 增加 85，没有新增 OOM kill。该环境存在换页，不能当作正常 8 GiB 设备的性能基准；这些是运行期采样，不能直接归因到此前启动阶段。
- 最近一轮日志记录后端启动约 113 秒，随后约 18 秒页面加载。没有足够分阶段数据将这 113 秒准确分配到各插件。
- 镜像已经使用 `uv pip install --compile-bytecode`；没有重复引入字节码预编译。
- 当前源码使用四个 vCPU、多线程 TCG、virtio 块设备。没有未经测量地增加核数或翻译缓存。

## 本轮改动

1. 本地 AVD 的 `config.ini` 改成 `hw.ramSize=8192`，仅下次启动生效。没有编辑运行时生成的硬件配置，也没有重启正在用于其他测试的实例。
2. QEMU 内存参数采用签名镜像声明的 `memory_mib`，不再对桌面镜像硬编码 4096。旧桌面镜像未声明预算时仍默认 4096，非桌面镜像仍默认 256，保留既有范围检查。
3. 应用恢复时首次健康检查失败会再确认一次；确认期间保留当前页面及代理连接。连续失败仍按原流程重连，取消和显式停止仍然有效。这可能让真正断连的确认稍晚，但避免一次瞬时失败就关闭页面。
4. 运行时健康检查最多使用五秒，并受请求剩余期限约束。连接和读取状态行共用一个单调时钟期限，避免每读取一个字节都重新获得完整超时时间。
5. Python 初始化记录同步、配置、插件加载、挂载四个阶段耗时，并记录应用配置总耗时。复用既有插件启动钩子计时，不改变生命周期执行顺序。

此前已完成的成功 Schema 校验缓存、单轮文件哈希复用和不变清单跳过写入继续保留。没有跳过用户源码修改检测、删除功能、绕过认证或将必要服务虚报为就绪。

## 验证

- `uv run pytest tests/test_plugin_seed_io.py tests/test_native_plugins.py tests/test_plugin_schema_cache.py tests/test_startup_progress.py tests/test_plugin_application.py -q`：45 项通过。
- Android 定向测试：`DesktopBackendEndpointTest` 3 项、`WorkbenchSessionTest` 4 项通过；应用和运行时 Kotlin 编译成功。
- Gradle 使用独立构建目录 `/tmp/cyrene-startup-optimization-build`，测试采用已有签名镜像资产，不代表资产中的 Python 已更新。

## 尚未完成的设备验证

本轮未生成或安装更新的 APK，也未验证改动后的 QEMU 冷启动时间。主机当时仅剩约 7.3 GiB 磁盘空间，无法可靠容纳另一套 APK、解压模板和工作盘；现有模拟器还用于其他测试，不能为测量打断它。

取得足够磁盘空间后，应使用隔离 AVD，或在现有测试结束后重启已修正配置的 AVD。首先确认 `/proc/meminfo` 与预期内存一致，再使用包含这些 Python 改动的镜像测试。分别记录首次安装解压、VM 引导、后端初始化各阶段、HTTP 就绪和页面完成；测量时去掉额外采样器，并记录换页与 CPU 压力。至少区分冷启动和已运行服务的重新进入，不把两者混为同一种提速。

只有完成同环境对照，才能给出安卓端实际节省的秒数。插件并行启动、延迟加载和 TCG 缓存调参仍需独立证据，未纳入本轮改动。
