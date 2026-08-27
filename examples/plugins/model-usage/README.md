# 模型用量 PluginPack 示例

这是新的统一插件框架示例，不再使用 `plugin.json` 或旧的项目插件进程。

- `__init__.py` 声明 `PluginPack`、沙箱 View、侧栏入口及 i18n。
- `application.py` 使用 `provide_frontend_method` 注册 View RPC。
- `ui/index.html` 是插件拥有的 iframe 页面。

使用 `PluginValidate` 验证本目录，再通过 `PluginInstall` 安装。应用贡献首次安装或 Python 代码变化后需要重启 Cyrene；HTML/CSS/JS 资源可直接刷新。
