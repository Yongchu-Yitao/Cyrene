# QEMU 实际 App 验证：响应式侧栏与加载页

2026-09-09，Cyrene_ARM64_8G / Android 35 ARM64，内层 Debian ARM64 QEMU。
本轮实际打开 Android WebView 与客体 Python 服务，非浏览器隔离夹具。

- 主 APK 移除启动/停止后端调试按钮、常驻技术状态行，使用居中 Cyrene 图标、品牌和简短加载提示。
- 页面加载完成自动隐藏原生加载区；失败才显示通用错误与重试，HTTP 主文档错误及 renderer 退出走同一错误状态。
- 文件保存结果改为 Toast，避免写入已隐藏的状态区。
- APK 构建成功并覆盖安装；正常加载切换通过。失败/重试路径已实现，本轮未注入故障验收。
- 将最新前端同步到客体 /usr/local/lib/python3.12/site-packages/cyrene/workbench/webui/static/app，
  原始文件备份 /tmp/workbench-before-responsive.tar.gz。
- 客体 app.js SHA256 d5285b536a3fe2b3b608fd40fe2f0a43824791b00aa37ab9c5dba281d0ff8744，
  mobile-drawers.css SHA256 13185684d7e4907776aa18f89b36ab8ced65d38e0dda9a2507e5dd3a40b9e3fa，均与宿主一致。
- WebView 实测 412 CSS px：默认中间工作区，新增可见工具条数 0；页面 scrollWidth 412，无主体横向溢出。
- CDP 向实际 Android WebView 注入触控：左/右打开、左/右卡片内部反向滑动关闭均通过。
- 截图确认：左侧目录与 Dock 正常显示，右侧上下文卡片按内容高度显示，没有整屏空白侧板。
- 触控与滚轮检查期间 passive 警告为 0。唯一观测到的 400 来自 debug 接入探针故意提交无效聊天请求。
- health/projects 200，SSE 连接通过；保留已有 Gemma4 会话，本轮没有新增模型调用。
- 当前输入法以浮动模式显示，输入框可见；普通全宽软键盘及中文 IME 尚未完成验收。

截图在桌面仓库 output/playwright/qemu-workbench-main.png、qemu-workbench-left.png、qemu-workbench-right.png。
加载页截图 qemu-workbench-clean.png。模拟器保留运行。

注意：此次前端同步进入现有可写客体磁盘，签名 Runtime 基础镜像尚未重新封装。
新主 APK 包含加载页改动；从旧 Runtime 镜像全新安装时仍需重建镜像获得最新前端。


## 输入修复追加验证
2026-09-09：根布局加入 IME inset 避让，WebView 禁用自动手写启动。
构建记录 build/workbench-ime-build.log，APK 已安装。Gboard 模拟器配置仍需
stylus_handwriting_enabled=0 才稳定使用普通软键盘，此项为模拟器设置。
实际 ADB 点击软键盘 Q 成功，页面高度从 842 缩至 530 CSS px，输入框保持可见。
ADB 左右打开/反向关闭、CDP mouse 左侧拖动通过。测试字符已清除。
当前 guest 静态资源已同步新的 48px 边缘入口和鼠标拖动支持，基础 Runtime APK 未重封装。
Mac 硬件键盘转发、真实中文输入法待验证。
