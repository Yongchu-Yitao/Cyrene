# Cyrene Mobile

移动端现已合入 [Cyrene 主仓库](https://github.com/Yongchu-Yitao/Cyrene/tree/main/mobile)。
后续请在主仓库 `mobile/` 开发；Workbench 和 Python 后端直接使用同一提交的 `src/cyrene/`。
原 Cyrene-mobile 仓库仅保留历史，完整提交历史也已导入主仓库。
详见 [统一仓库与自动构建](../project-notes/android-monorepo.zh-CN.md)。

Android 上的桌面 Workbench：复用现有前端，在本机 ARM64 QEMU / Debian 中运行 Python 后端。

## 0.3.0：单 APK

只安装 `app/build/outputs/apk/debug/app-debug.apk`。运行时模块现在是 Android Library，
其签名镜像、JNI 引擎和后台服务随主 APK 打包；无需另外安装 Runtime APK。
运行时仍在私有 `:qemu` 进程中运行，主界面通过 Binder 和带认证的回环代理访问它。

首次启动会校验并解压内置镜像，需要数分钟和充足存储空间。当前是 ARM64 实验构建，
包含压缩镜像、模板盘及可写盘；建议至少预留 12 GB 可用空间。
模型配置需在 Workbench 内自行设置；安装包不包含开发者的会话、API 密钥或设备数据。

移动端顶栏整合项目切换、标签中心和其他按钮。左右卡片通过边缘滑动进入。

Android 的 `versionName` 在 Gradle 配置时读取主仓库 `pyproject.toml` 的
`[project].version`，无需单独维护。`versionCode` 独立维护在 `app/build.gradle.kts`，
每次分发新的 Android 版本时递增；修改显示版本不会自动增加它。

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

## 验证与边界

运行时测试使用 `:runtime-app:testDebugUnitTest`；安装验证必须在未安装旧 Runtime 包的设备上进行。
Debug 包仅供实验，正式发布还需配置发布签名和完整验收。当前 QEMU 为软件模拟，
不能把 ARM64 或 8 GB 模拟器验证等同于真机性能保证。此前 MCP/Chromium 联合探测存在
原生 SIGILL 问题，单 APK 整合不代表这些限制已经解决。

旧双 APK 的会话和工作目录仍留在旧包的数据目录，不自动迁移，也不自动删除旧包。
历史版本信息见 CHANGELOG.md，最新单包验证见 project-notes/android-single-apk.zh-CN.md。
