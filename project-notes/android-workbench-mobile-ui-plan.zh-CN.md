# Workbench 移动端 UI 适配研究

日期：2026-09-09。范围：Android WebView 复用桌面前端；本轮删除 legacy UI，以下布局为待实施方案。

## 结论

保留同一套 Workbench 组件、状态和 API，新增响应式展示层。按可用窗口宽度调整面板，
不要通过缩放整个桌面页面实现手机适配。保持现有主题、字体和图标，优先修正信息结构与操作。
技能检索的 newsletter 模板与本产品不匹配，未采用其配色和页面结构。

## 源码发现

源码根目录：/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend。

- features/chat/shell.css：侧栏默认 300px；手机不适合常驻占宽。
- features/chat/workspace.css：看板最小宽度含 1748px 基数、五列至少 340px；局部已有 680/760/1040px 规则，仍需要完整布局策略。
- features/chat/pane-layout-controller.jsx：分屏布局按会话持久化。手机只显示活动面板时，应保留桌面布局数据，避免窄屏覆盖桌面设置。
- features/chat/composer-keyboard.jsx：存在 Enter 发送与 IME guard；移动软键盘建议 Enter 换行、点击发送，外接键盘保留快捷键。
- index.html 已有 device-width viewport；不能简单靠增加 viewport 标签解决键盘问题。
- Android DesktopWorkbenchActivity 目前常驻启动/停止和状态区，后续应在就绪时收起至运行状态菜单；启动及错误时展示完整状态页。

## 建议的信息架构

手机顶部：项目/会话入口、当前标题、新建会话、更多。主内容一次只显示一个面板。
项目、历史会话、搜索进入抽屉；主要模块使用底部“工作、计划、资料、更多”四项。
“工作”承载聊天和看板；“计划”承载 Schedule；“资料”包含 Knowledge 与 Memory。
这是建议的信息组织，需通过原型验证可发现性，所有原功能保持可达。

聊天底部保留多行输入、附件、发送/停止；模型、工具、上下文等进入有标签的底部面板。
文件、终端、浏览器和上下文树打开为全屏子页，带返回和面板切换入口，替代手机左右分屏。
返回顺序：关闭键盘/浮层 → 返回来源面板 → 会话列表 → 系统退出；需原生与 Web 导航协调。

看板默认按状态筛选的单列列表，提供显式“移动到”操作；日程默认议程列表；
知识与记忆采用列表→详情。拖拽、悬停、右键操作均提供点击菜单替代。
代码块与宽表格允许局部横向滚动，页面主体不得横向溢出。

## 窗口与输入策略

建议初始断点：<600 CSS px 单栏，600–839 可选列表/详情，≥840 按内容恢复双栏。
CSS px 为 Web 布局口径，Android dp 为原生窗口口径，需核对 WebView 缩放，不直接混用。
断点是本项目的起点，最终按内容最小宽度和横屏实测调整，不能仅检测 Android UA。
触控目标以 48px 起步，在 WebView 实测其 dp 对应关系；正文 16px 起，支持字体放大。

键盘采用 Android adjustResize、WindowInsets 与 Web visualViewport 联合诊断，确定唯一高度来源；
不要叠加两份键盘/安全区 padding。Chrome 的 interactive-widget 行为不能直接当作 WebView 保证。
100dvh 也不能单独保证输入框避让键盘。验证中文输入法组词、软键盘发送、外接键盘、横屏和手势返回。

## 分阶段落地

1. 外壳与聊天：共享断点 hook、单活动面板、抽屉、输入栏、键盘/返回键、启动状态折叠。
2. 完整功能可达：资源全屏页、模型与工具 sheet、设置页、附件与下载、点击操作替代拖拽。
3. 复杂模块：看板列表、议程、资料列表详情、平板分栏和跨尺寸状态恢复。
4. 性能与可访问性：长会话虚拟化评估、暂停不可见重型面板渲染、TalkBack、主题、减少动画。

预估 8–14 个工程日完成 UI 实现与设备验收（单人、现有 API 不变的粗估），不包含 QEMU 稳定性和终端后端修复。
首阶段预计 2–4 日。优先交付可用聊天再逐步覆盖复杂模块。

## 验收矩阵

360/390/412px 手机竖屏、手机横屏、768/1024px 平板、桌面回归；
键盘开关、中文 IME、200% 字体、明暗主题、TalkBack、减少动画。
验证新建/恢复聊天、流式输出、停止生成、切换模型、附件、资源打开与返回、重连和刷新。
宽度切换不得丢失草稿、活动会话、滚动位置或重写桌面分屏布局。
UI 可达性与后端执行分别记录：当前终端超时、QEMU SIGILL 不能算 UI 完成即解决。
前端构建后须更新客体资源，并在发布时重新封装签名镜像，不能只改宿主源码。

## 官方参考

- Android 响应式导航：https://developer.android.com/develop/ui/views/layout/build-responsive-navigation
- Android 标准布局与列表详情：https://developer.android.com/develop/adaptive-apps/guides/canonical-layouts
- Android 窗口尺寸：https://developer.android.com/develop/adaptive-apps/guides/use-window-size-classes
- Chrome 键盘 viewport 行为（文章明确其更改不影响 WebView）：https://developer.chrome.com/blog/viewport-resize-behavior/

## 本轮 legacy 清理

删除旧 MainActivity.kt UI 和 Manifest 注册，删除 Workbench 的 legacy 按钮与中英文资源，
LocalAgentForegroundService 通知统一指向 DesktopWorkbenchActivity。
旧文件包含先前未提交改动，删除前保存在 build/legacy-ui-backup/MainActivity.kt（不参与编译）。
底层 local agent、数据存储和历史数据保留；它们不是 UI，后续另行分析依赖后再决定清理。

验证：:app:assembleDebug 成功，新 APK 覆盖安装成功并启动 Workbench；设备 UI 树未出现 legacy 按钮。删除旧 UI 后发现共用附件类型依赖，已将 PendingAttachment 独立成数据文件。git diff --check 通过。
