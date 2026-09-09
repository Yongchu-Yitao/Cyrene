# 单 APK 整合验证（2026-09-09）

交付：dist/Cyrene-Mobile-0.3.0-unified-debug.apk，1,883,118,486 字节。
SHA-256：479f08625e7d4c1682ef1e4202d0c6dd0a7a74fa87210c5c8159d5e9379b97c0。
这是开发签名实验包，未发布到商店或 GitHub。

runtime-app 改为 library 依赖，主包内包含运行时 Binder 服务、ARM64 JNI 和签名镜像。
Binder 绑定使用当前 packageName，服务 exported=false，仍运行在 :qemu 进程。
主应用初始化跳过 :qemu 等非 UI 进程。JNI 使用提取式打包，以支持引擎按路径加载。
默认构建要求桌面镜像，防止漏配置时意外打进旧 Alpine 镜像。

镜像基于已签名的干净 ARM64 模板，以 refresh-webui.py 更新静态前端并重新签名。
对原模板压缩及解压 SHA-256 校验、对全部新静态文件的写入回读校验通过。
没有导出旧模拟器的数据盘，不包含用户模型凭据或会话。
镜像版本：debian12-desktop-aarch64-unified-9d452cb8b19e。

验证环境：新建 Cyrene_Unified_ARM64_8G，Android 35 ARM64，8 GB RAM，32 GB数据分区。
安装前没有 ai.cyrene 包；仅安装一个 APK 后 pm list packages ai.cyrene 只有 ai.cyrene.mobile。
未安装 ai.cyrene.mobile.runtime；主进程和同包 :qemu 进程运行。
首次解压、原生库加载、后端 ready、WebView 首次模型/人格初始化页均通过。
本次 UI 验证未拦截静态资源，全部由 APK 自带镜像通过本地后端提供。
首次启动用时约 6 分钟；安装和解压后 /data 使用约 13 GB（此前系统使用不足1 GB）。
截图：build/unified-first-launch.png。

构建命令：:app:assembleDebug :runtime-app:testDebugUnitTest --tests ai.cyrene.mobile.runtime.*。
4 项测试通过；APK 签名校验通过。未重复全套测试。

旧双包数据未自动迁移，旧模拟器保留。当前新模拟器停在首次初始化页；模型调用未在
此次新安装中测试。此前 QEMU SIGILL/MCP/Chromium 限制不因打包整合自动解决。

## 0.3.1 菜单修复
已生成 dist/Cyrene-Mobile-0.3.1-unified-debug.apk，带内容版本的移动样式链接以及独立浮动项目操作菜单。
资源从已校验干净模板更新并重新签名，构建通过。新包未重做全新安装验证；
同一份前端在旧ARM64/QEMU模拟器正常刷新后验证，未注入临时样式。
新镜像版本仍创建独立数据盘，不自动迁移已有镜像数据。

0.3.1 最终实现使用 body portal 浮层，取消此前内联展开方案。普通刷新后的实际 WebView 验证：
父菜单高111.52px保持不变，浮层每项可见；375×240、1280×842视口边界检查通过。
重新生成APK消除增量ZIP残留空间，保留单包约1.88GB。
