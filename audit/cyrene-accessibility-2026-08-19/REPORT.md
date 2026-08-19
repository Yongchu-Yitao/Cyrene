# Cyrene 无障碍审计：Agent 自我设置、能力与权限

日期：2026-08-19  
范围：macOS Electron 桌面端、中文、深色主题；重点检查聊天权限模式、智能代理设置、工具能力开关，以及 Agent 修改自身全局设置时的授权卡片。  
目标：以 WCAG 2.2 AA 为参考，并额外检查高风险授权的认知可访问性与知情同意。

## 总体结论

当前约为 **5/10，基础可用，但不宜称为 WCAG 2.2 AA 就绪**。

工具能力页做得最好：开关拥有明确名称、状态和解释。智能代理设置页和权限流则存在关键缺口：多个表单控件没有程序化标签；权限模式的当前选择只靠图标/颜色；授权卡片没有可靠的实时播报或焦点转移；自我配置确认没有显示具体的旧值、新值和影响范围。

## 审计步骤

1. **聊天入口与能力说明 — 一般**  
   截图：[01-chat-entry.png](01-chat-entry.png)。主要聊天控件、附件、模型入口、语音和发送按钮都有读屏名称；权限入口藏在模型菜单中，发现成本较高。

2. **权限模式菜单 — 较差**  
   通过 macOS 无障碍树确认“默认、自动、规划、完全访问”均可聚焦并读出说明。当前为“自动”，但选中状态只显示视觉勾选，树中没有 `selected`、`checked` 或 `pressed` 状态；`Escape` 不能关闭子菜单。弹出层无法被 Computer Use 保存为截图，因此本步骤以本次无障碍树与源代码为证据。

3. **智能代理设置 — 较差**  
   截图：[02-agent-settings.png](02-agent-settings.png)。界面层级和说明清楚，但人格文本框、生成策略、主动消息开关、Heartbeat 数字输入在无障碍树中分别只读成内容/当前值/“切换”/“1800”，没有对应可访问名称。

4. **工具能力管理 — 良好**  
   截图：[03-tool-capabilities.png](03-tool-capabilities.png)。每个开关都读成“启用或关闭…工具”，并有独立的用途说明；状态由原生 switch 语义暴露。这是本次范围内最成熟的页面。

5. **Agent 自我配置授权 — 较差**  
   后端正确要求本地桌面、单次人工确认，选项限定为“允许这一次/拒绝”。但前端卡片只是普通 `div`、粗体文本和按钮，没有 `role=status/alert`、`aria-live` 或明确焦点管理；此外只显示操作、工具和模型生成的理由，不显示“哪一项从什么值改为什么值”。本次没有执行真实设置变更，因此没有生成真实授权卡片截图。

## 主要发现

### P1 — 智能代理表单缺少程序化标签

`FieldRow` 使用普通 `div` 展示标签，没有通过 `<label for>`、`aria-labelledby` 或 `aria-label` 关联控件。`AgentsPanel` 中 textarea、select、switch 和 number input 都受影响。实际无障碍树已复现无名控件。

- 影响：读屏用户不知道正在修改哪一项；语音控制也难以按名称定位。
- WCAG：1.3.1、3.3.2、4.1.2。
- 代码：`src/webui/frontend/settings-overlay.jsx:3326`、`:3335`、`:3343`、`:3345`、`:7810`。
- 修复：为每项生成稳定 id；使用 `<label htmlFor>`；hint 用 `aria-describedby`；调用 `Toggle` 时强制传入 label。

### P1 — 权限请求出现时不会可靠地通知读屏用户

授权卡片没有状态区域、警报或焦点入口。内部 `uiSurface.register()` 只服务 Agent 控制协议，不能替代浏览器/系统辅助技术语义。

- 影响：Agent 停下来等待授权时，盲人用户可能只感知到“没有继续”，却不知道页面底部出现了批准/拒绝选项。
- WCAG：4.1.3、2.4.3；同时影响 3.3.2。
- 代码：`src/webui/frontend/workbench-chat.jsx:15449`。
- 修复：容器使用 `role="region"` + `aria-labelledby`，新增礼貌 `aria-live` 播报；卡片出现后把焦点移到标题或首个安全操作，并在回答后恢复到输入框。

### P1 — 授权文案不足以支持知情确认

自我配置请求只提供通用 operation、tool、reason 和不可读指纹；UI 还主动隐藏 `cyrene-setting:*` 目标。用户看不到设置名、旧值、新值、持续范围和回滚方式。

- 影响：认知障碍、低注意力或依赖读屏的用户很难判断“允许这一次”具体允许了什么。
- 这是认知可访问性和安全 UX 的高风险项，不单独断言为某一条 WCAG 失败。
- 代码：`src/cyrene/tooling/runtime_support.py:424`、`src/webui/frontend/workbench-i18n.jsx:7944`。
- 修复：后端公开经过脱敏的 `setting_label`、`before_value`、`after_value`、`scope`、`reversible`；前端以定义列表或 diff 展示，并把这些内容纳入可访问名称。

### P1 — 默认强调色不足以保证权限文本对比度

权限标题为 13px accent 文本，首个授权按钮为白字 accent 背景。默认深色 accent `#7c5ce8` 对 `#101114` 为 **4.09:1**；默认浅色 accent `#4d9a78` 对白色为 **3.38:1**，都低于普通文本 4.5:1。自定义强调色也没有对比度约束。

- WCAG：1.4.3。
- 代码：`src/webui/frontend/shared/theme/base.css:15`、`:56`；`src/webui/frontend/workbench.css:24453`、`:24503`。
- 修复：权限和确认界面使用独立、经过校验的高对比色 token；颜色自定义时实时校验文本和焦点环，自动选择深/浅前景色。

### P2 — 权限模式选中状态没有程序化暴露

菜单声明了 `role="menu"`，但子项仍是普通 button；当前模式只靠 `.active` 和勾选 SVG 表达，没有 `role="menuitemradio"` + `aria-checked`。本次树中“自动”与其他选项读法相同。

- WCAG：1.3.1、4.1.2。
- 代码：`src/webui/frontend/workbench-chat.jsx:18245`、`:18439`。
- 修复：使用 radiogroup/radio，或 menu/menuitemradio；补充 roving tabindex、上下方向键、Home/End、Escape 和关闭后的焦点恢复。

### P2 — 授权卡片把第一个 Agent 选项无条件设为主按钮

第一项总是获得 `.primary`，而自我配置协议通常把“允许这一次”放在第一项，“拒绝”是次按钮。这在视觉和认知上偏向批准高风险动作。

- 影响：低视力、注意力或执行功能受限用户更容易误批。
- 代码：`src/webui/frontend/workbench-chat.jsx:15462`。
- 修复：拒绝/返回应是同等可见或默认焦点；危险批准不应仅因排序而成为主按钮。

## 已确认的优点

- 设置侧栏使用 `nav`，当前页通过 `aria-current="page"` 暴露。
- 工具能力开关拥有 switch 角色、明确名称和可读说明。
- 自我配置变更被限制为本地桌面人工确认，不能被自动/完全访问模式绕过。
- 授权文案会本地化工具、操作、目标和原因；无有效选项时使用 `role="alert"` 报错。
- CSS 广泛使用可缩放字号，部分控件和动画已支持 `prefers-reduced-motion`。

## 建议修复顺序

1. 先修智能代理表单的 label/description 关联。
2. 再修授权卡片的实时播报、焦点生命周期和 before/after 信息。
3. 把权限模式改为完整的 radio/menuitemradio 模式并补键盘行为。
4. 给权限相关颜色建立不可被自定义主题破坏的 AA token。
5. 最后补自动化：axe-core + Playwright 键盘用例 + macOS VoiceOver 手工回归。

## 证据限制

- 没有执行真实权限批准或设置写入；因此未验证批准后的成功通知、焦点恢复和撤销路径。
- Computer Use 的弹出菜单层不提供可保存截图；菜单结论来自本次实时无障碍树和实现代码。
- 未运行 VoiceOver、Windows Narrator、200%/400% 缩放和高对比模式；不能据此宣称完整 WCAG 合规。
