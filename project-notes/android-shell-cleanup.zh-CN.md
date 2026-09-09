# Android 外壳收尾

Android 是共享 Cyrene 的运行载体。业务功能、模型配置、会话数据库和 Workbench
均由主仓库维护，`mobile/` 只负责 Android 平台适配与打包。

## 移除的旧实现

- Kotlin Agent 执行器、编排器、子 Agent、工具调用和 Provider 客户端。
- 旧模型配置、OAuth、SecureStore、会话数据库及其存储适配器。
- MainViewModel、旧会话/附件/审批模型、桌面连接与缓存协议。
- 独立 GitHub 更新器、APK 下载器、安装权限及更新 FileProvider。
- 旧 Agent 前台服务、业务测试、旧 UI 字符串、Compose、Markwon 与 BouncyCastle 依赖。

这些实现从源码中删除，而不是隐藏入口。历史仍可通过 Git 查阅。
应用 ID 保持 `ai.cyrene.mobile`，不主动删除旧安装的数据文件；旧数据库不迁移、不读取。
原生启动页跟随 Android 系统语言和主题，Workbench 内的设置由共享前端负责。

## 保留的外壳能力

| 能力 | 实现与范围 |
| --- | --- |
| 界面 | 同一套 Workbench，包含响应式布局与手势 |
| 后端 | 同一提交的 Python 后端，运行于内置 ARM64 Linux |
| 生命周期 | 私有 `:qemu` 服务、Binder 客户端、带认证回环代理 |
| 文件 | 系统文档选择器上传、系统文档保存器下载 |
| 输入 | Android 输入法、键盘避让、WebView 触摸 |
| 外部网页 | 用户点击 HTTP(S) 链接时交给系统浏览器 |

低层 Guest 协议及运行时诊断工具继续保留；它们负责运行环境，不提供另一套 Agent 业务逻辑。
原客户端从 `localagent.runtime` 移到 `desktop`，不再依赖旧 Agent 包。

## 一个版本源

`pyproject.toml` 的 `[project].version` 是 Android 唯一版本输入。
显示版本原样使用，安装序号由 `mobile/buildSrc/src/main/java/CyreneVersion.java` 确定性转换：

```text
versionCode = ((major × 100 + minor) × 100 + patch) × 10000
              + phase × 1000 + sequence
phase: dev=0, alpha=1, beta=2, rc=3, 正式版=9
```

例如 `0.9.0-beta17` → `9002017`，高于旧 APK 的序号 10/11。
相同统一版本的所有构建使用相同序号；有新修订就更新主版本，不再单独发布 Android 版本。
`dev → alpha → beta → rc → 正式版 → 下一补丁` 保持递增。
major 支持 0–20，minor/patch 支持 0–99，预发布序号支持 0–999；超出范围或
未知格式会明确拒绝构建，避免版本冲突或溢出 Android 的整数上限。
安装升级还要求 APK 签名一致；CI 临时 Debug 签名仍不能当成发行签名。

## 验证与未完成的平台能力

收尾验证覆盖版本映射的排序/边界、保留的 Workbench 代理与运行时测试，以及实际 APK 打包。
GitHub Android workflow 已去掉旧业务测试，改为运行版本映射测试和保留的外壳测试。

本地结果：3 项版本映射测试、3 项代理测试、4 项运行时测试通过，清理构建后的单 APK
打包成功。检查生成 Manifest 和 APK DEX，确认旧 Agent/data/MainViewModel 类、旧服务、
安装更新权限及 FileProvider 均不存在，桌面运行时客户端仍在；版本为 `0.9.0-beta17 / 9002017`。
这次本地打包复用此前的干净签名运行时资源，未重新执行真机/模拟器端到端验收。

这次收尾不把尚未完成的平台功能标为支持：Office 宿主集成、原生音频/麦克风桥接、
应用内升级、旧数据迁移及完整真机验收仍未实现；此前 QEMU MCP/Chromium SIGILL
也不是删除旧 Agent 代码能解决的问题。详见 [移植总结](android-port-summary.zh-CN.md)。
