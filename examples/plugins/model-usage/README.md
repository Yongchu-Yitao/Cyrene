# 模型用量示例插件

这是一个可直接安装的 Cyrene 项目插件示例，演示：

- 在侧栏“工具”区域注册入口；
- 在统一 Pane 中显示插件自己的 iframe UI；
- 通过 `postMessage` RPC 调用独立插件后端；
- 按模型展示 Cyrene 最近 7 天的 Token 用量。

## 安装

1. 打开“设置 → 扩展中心 → 插件”。
2. 点击“安装项目插件”。
3. 选择本目录 `examples/plugins/model-usage`。
4. 在当前项目打开插件开关。
5. 返回工作页面，在侧栏“工具”中打开“模型用量”。

关闭开关后，插件入口会从“工具”区域消失；再次开启后恢复。

## 文件

- `plugin.json`：插件清单和 UI/工具贡献。
- `plugin.py`：注册 `usage.load` RPC 方法并读取用量统计。
- `ui/index.html`：插件自己的卡片界面。

