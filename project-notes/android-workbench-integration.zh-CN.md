# Android Workbench 接入验证（2026-09-09）

## 已实现

主应用启动器改为 DesktopWorkbenchActivity，复用桌面 Workbench 页面，通过
DesktopRuntimeClient 启动 ARM64 Linux Python 后端。旧 Kotlin UI 已移除，主应用只提供 Workbench。
未进行手机 UI 优化，也未迁移旧 Kotlin 会话。

WorkbenchProxy 在随机本机端口提供认证反向代理：后端 bearer 仅在原生侧注入，
WebView 使用独立 HttpOnly、SameSite=Strict cookie，并校验 Host、Origin 和 Fetch Site。
支持 HTTP、SSE 和 WebSocket 升级；提供启动、停止、重试、页面错误提示。
普通文件选择和 HTTP 下载已接入，但尚未完成设备端交互验收；blob 下载及语音等原生桥接尚未完成。

## 实际验证

- Android 35 ARM64 模拟器 Cyrene_ARM64_8G，配置 8 GB；Linux 客体 2 GB、单核 TCG。
- 主 APK 已构建并覆盖安装；使用已安装的 ARM64 桌面 Runtime APK。
- 实际 WebView：health 200、projects 200、无效聊天请求 400、SSE 成功连接。
- WorkbenchProxy 的未认证/跨站拒绝、POST/SSE 转发、双向 WebSocket 升级三个单元测试通过。
- 用户指定服务 http://100.100.8.2:2242/v1，实际模型 ID gemma4-26b-a4b。
  API key 字段使用非机密占位值 local-no-auth，服务接受该配置。
- 初始化模型连接成功，返回 OK.；随后通过真实 Workbench 输入框发送验证消息，
  模型实际回复“Android 前端已连接 Gemma4。”。页面显示耗时 1m 14s。
- 刷新页面后同一会话和回复仍存在。截图：output/playwright/android-workbench-gemma4.png。

## 模型连接测试修复与分发限制

桌面 cyrene_model/probe.py 原先 max_tokens=48，Gemma4 的推理在输出答案前耗尽预算，
因此 HTTP 已成功仍会初始化失败。源代码改为统一 1024 token 上限，文本和视觉探针共用。
uv run pytest tests/test_model_probe_budget.py tests/test_onboarding_webui.py -q：13 项通过。

本次同步修补了模拟器可写 Linux 磁盘的已安装实现和插件实现，并重启 Python 服务验证。
**既有签名 Runtime 镜像/APK 尚未重新打包该 Python 修复**；全新安装旧镜像仍可能遇到此问题。
后续分发前须从修正后的桌面源码重新构建、签名并验证 Runtime 镜像。

## 未通过与剩余范围

有效项目的终端创建触发 Terminal Daemon 12 秒超时；不能宣称真实终端可用。
WebSocket 协议代理单元测试通过不代表终端完整链路通过。
先前组合工具测试的 QEMU 原生 SIGILL、重复启动稳定性、长期后台存活仍待解决。
Android WebView 的聊天成功不等于 Linux 客体内 Playwright Chromium 验证通过。
真实 8 GB 手机性能、会话迁移、Office 与移动端能力桥接尚未完成，不能称为完整移植验收。
