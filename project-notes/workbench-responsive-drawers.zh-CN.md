# Workbench 窄屏滑入卡片

2026-09-09。适用于 <768 CSS px；宽屏继续使用原布局。

默认中间工作区，两侧卡片覆盖滑入。左侧整合目录与原模块 Dock，右侧覆盖现有上下文、知识详情及双栏右列。
两侧不可同时打开；切换模块关闭卡片。原组件保持挂载，桌面侧栏折叠偏好不被窄屏展开状态写回。
顶部保留左右按钮，无右侧内容时禁用右按钮。侧边触发带距屏幕外缘 24px、宽 20px，
水平移动至少 48px 且明显超过竖直位移才打开；正文内横向滚动不会触发。
卡片顶部“滑动收起”区域支持反向关闭，也可点击遮罩/关闭按钮或按 Escape。
没有禁用 Android 系统返回手势；OEM 实际手势区域仍需设备验证。

隐藏卡片与被覆盖的主内容使用 inert；打开后转移焦点，Tab 在卡片内循环，关闭恢复原焦点。
减少动画偏好关闭滑入动画。当前是超过阈值后播放滑入动画，不是逐帧跟手拖动。

验证：手势方向/阈值单元测试 2 项通过，已纳入 npm test 选择；前端正式构建通过，git diff --check 通过。
使用真实 Chromium 加载新 React 控件和完整 Workbench 样式的隔离布局夹具，验证：
360/390px 无主体横向溢出、CDP 触控左右打开、顶部反向收起、焦点循环、Escape、
侧栏滚动位置 240px 保留、双栏右列位于遮罩之上、768/1280px 移除移动属性恢复桌面。
夹具位于 output/playwright/mobile-drawers-harness.html；它不是完整业务页面，不能替代所有模块验收。

当前 Android 模拟器离线；本轮未重新封装或安装 Runtime 镜像。生成的 static/app 已更新，
需要同步进 Android 客体或重建镜像后，才能在已安装 Android 应用中看到变化。
仍待设备端确认：系统返回区域、真实中文键盘、复杂模块与弹出菜单、横竖屏及真实长会话。
本次未更改任务信息架构、模块名称、模型调用或运行时生命周期。


## 窄屏布局修订

移除常驻左右按钮、滑动收起条及关闭按钮；仅键盘聚焦时显示辅助入口，普通触控界面不占空间。
主内容从 106px 顶部偏移改为原顶栏的 58px；侧栏顶边统一为顶栏下方 8px。
右侧上下文按内容高度显示，消除整屏空白底板；左侧目录和 Dock 采用一致的内外边距。
打开后直接在卡片内反向滑动关闭，也可点击遮罩或按 Escape。输入区、代码与横向滚动容器保留自身手势。
滚动容器会使 Pointer Events 提前 cancel，因此卡片收起使用局部非 passive touchmove，
只拦截明确的反向水平移动；普通竖向滚动不阻止。桌面触控板有相应水平 wheel 路径。

Workbench grid、聊天历史、项目工具及看板 wheel 均使用 useNativeWheel，替代 React passive onWheel。
callback ref 支持卸载解绑、现有 ref 转发和最新闭包。窄屏停用旧侧栏/聊天横滑切换，避免与卡片手势争抢。

本轮验证：18 项手势/监听/历史布局测试通过，正式前端构建与 diff 检查通过。
Chromium 隔离布局夹具：左右打开、卡片内反向关闭、可见新增控件数量 0、390px 无主体溢出，
实际 wheel 触发并取消默认行为且无 passive 警告。此夹具不等于真实业务页面视觉验收。
Android 安装包未重封装；桌面开发环境需刷新 Workbench 加载更新后的静态资源。


## Android 输入与滑动修复（2026-09-09）

最新实现以此节为准：边缘触发区域扩大至 48 CSS px，移除独立 edge DOM；
原生 touchmove 使用 passive:false 并仅取消已识别的水平手势。增加鼠标 pointer 拖动，
保留触控板 wheel，输入框、代码区及横向滚动容器仍使用自身交互。
Android 根布局同时处理 systemBars 与 IME insets，软键盘弹出时缩小 WebView，避免覆盖输入框。
模拟器 Gboard 浮动手写面板通过 secure stylus_handwriting_enabled=0 切回普通键盘；
WebView setAutoHandwritingEnabled(false) 单独不足以替代该模拟器设置。

实际验证环境：Cyrene_ARM64_8G Android 35 ARM64，运行已有 QEMU Linux 后端。
最新版 APK 已安装，前端已同步至当前 guest 的 site-packages 静态资源目录；
这不是重新封装可分发的 Runtime 基础镜像。
ADB 原生点按软键盘 Q：textarea 从空变为 Q；键盘弹出时 innerHeight 842→530，
输入框仍可见，测试字符已删除。ADB 左右边缘滑动及反向关闭通过；
WebView CDP 实际 mouse 拖动左侧打开/关闭通过。
硬件 Mac 键盘转发及真实中文 IME 尚未验证。
前端构建、Android debug 构建成功；最终 3 项布局手势/原生 wheel 测试通过，diff 检查通过。
截图：output/playwright/qemu-keyboard-fixed.png。

## 顶栏响应式整合
窄屏保留项目切换、当前标签中心、更多三个入口。标签中心复用原分组与操作，包含全部标签。
更多菜单使用三列图标文字网格，保留原按钮处理函数；项目操作在项目行下展开，避免末行向上弹出被裁切。
375/412px WebView 检查无页面横向溢出，768/1280px 恢复桌面标签栏；项目、标签、更多和帮助弹层均已打开检查。
构建及4项相关测试通过。资源已同步当前QEMU guest；本次WebView交互检查使用本地同版本静态资源拦截以绕过guest传输慢，API仍连接QEMU。
截图：output/playwright/mobile-project-actions-fixed.png、mobile-more-grid-fixed.png。

## 菜单缓存修复（2026-09-10）
实际旧模拟器页面加载的 mobile-drawers.css 只有6条顶层规则，缺少项目菜单内联展开样式。
原因是此样式链接漏了 ?v= 构建版本。补入后由构建器自动计算内容指纹。
本次未注入样式、未拦截请求，正常刷新实际 WebView 后三项操作均通过 elementFromPoint 遮挡检查。
截图：output/playwright/project-menu-cache-fixed.png。

## 最终菜单行为：独立浮层
根据用户纠正，取消内联展开。ProjectActionPopover 使用 body portal 和 fixed 定位，
测量真实尺寸后选择上/下方并限制在 visualViewport 内，监听滚动和视口变化重新定位。
实际正常加载的 WebView：项目列表展开前后高度均 111.52px，浮层三项均通过遮挡命中检查；
375×240 与1280×842边界检查通过，Escape仅关闭子菜单，父菜单保留。
截图：output/playwright/project-actions-floating-detail.png。
