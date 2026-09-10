# Cyrene for Android

移动端现已合入 [Cyrene 主仓库](https://github.com/Yongchu-Yitao/Cyrene/tree/main/mobile)。
后续请在主仓库 `mobile/` 开发；Workbench 和 Python 后端直接使用同一提交的 `src/cyrene/`。
原 Cyrene-mobile 仓库仅保留历史，完整提交历史也已导入主仓库。
详见 [统一仓库与自动构建](../project-notes/android-monorepo.zh-CN.md)。

Android 上的桌面 Workbench：复用现有前端，在本机 ARM64 QEMU / Debian 中运行 Python 后端。

## 单 APK

只安装 `app/build/outputs/apk/debug/app-debug.apk`。运行时模块现在是 Android Library，
其签名镜像、JNI 引擎和后台服务随主 APK 打包；无需另外安装 Runtime APK。
运行时仍在私有 `:qemu` 进程中运行，主界面通过 Binder 和带认证的回环代理访问它。

首次启动会校验并解压内置镜像，需要数分钟和充足存储空间。当前是 ARM64 实验构建，
包含压缩镜像、模板盘及可写盘；建议至少预留 12 GB 可用空间。
模型配置需在 Workbench 内自行设置；安装包不包含开发者的会话、API 密钥或设备数据。

移动端顶栏整合项目切换、标签中心和其他按钮。左右卡片可从页面中间或边缘水平滑动进入，仅在页面存在对应卡片时启用。

Android 的 `versionName` 在 Gradle 配置时读取主仓库 `pyproject.toml` 的
`[project].version`，无需单独维护。`versionCode` 也由该版本自动派生，
不再维护独立版本序号。所有平台的新修订都更新主项目版本。

## 构建

要求 JDK 17、Android SDK 35，以及签名后的桌面运行时资源。
默认资源目录为 `build/unified-assets`，缺失时构建会报错，不会退回 Alpine 镜像。

```sh
# 完整镜像生成方式见 runtime-image/desktop/README.md。
# 可显式指定另一份已签名桌面资源：
./gradlew :app:assembleDebug -PcyreneDesktopAssets=/absolute/path/to/assets \
  -Dorg.gradle.jvmargs=-Xmx6g --max-workers=2 --no-daemon
```

`runtime-image/desktop/refresh-webui.py` 可在经过签名和摘要验证的干净模板中刷新静态前端，
逐文件验证写入结果，并用提供的镜像签名密钥生成新版本。它不读取手机数据盘。

## 统一发布

从后续版本开始，主仓库 `release.yml` 的 `build-android` 任务会调用同一套 Android
镜像构建、测试和打包流程，与 macOS、Windows、Linux 一起出现在发布任务图中。
推送版本标签后，验证通过的实验 APK 和 SHA-256 校验文件会自动上传到同一 GitHub Release，
最终发布汇总包含 Android；Android 构建或上传失败时，整次发布不会报告为全部成功。
平时的分支和 PR 仍运行 Android CI，但不会创建或修改 Release。
手动运行发布工作流时可选择 `android` 仅验证 Android 构建；只有版本标签运行会上传安装包。

如果上传中断，可手动运行 Android 工作流并同时填写 `release_tag` 与 `source_run`，
直接补传已通过构建的 APK。补传会核对来源任务成功状态、标签提交、版本和校验和；
不会重新构建，也不会把其他提交的 APK 上传到该版本。

## 验证与边界

运行时测试使用 `:runtime-app:testDebugUnitTest`；安装验证必须在未安装旧 Runtime 包的设备上进行。
Debug 包仅供实验，正式发布还需配置发布签名和完整验收。当前 QEMU 为软件模拟，
不能把 ARM64 或 8 GB 模拟器验证等同于真机性能保证。此前 MCP/Chromium 联合探测存在
原生 SIGILL 问题，单 APK 整合不代表这些限制已经解决。

旧双 APK 的会话和工作目录仍留在旧包的数据目录，不自动迁移，也不自动删除旧包。
历史版本信息见 CHANGELOG.md，最新单包验证见 project-notes/android-single-apk.zh-CN.md。

## Android 外壳职责

仅维护 Workbench WebView、系统文件选择/保存、键盘避让、外部链接、
Binder 生命周期与内置 ARM64 运行时。模型配置、会话、工具和 Agent 逻辑全部由共享 Python 后端负责。
旧 Kotlin Agent、模型配置/OAuth、会话数据库、桌面连接客户端与独立更新器及其测试已移除。
旧设备数据不会被主动删除或自动迁移。详见[收尾记录](../project-notes/android-shell-cleanup.zh-CN.md)。
