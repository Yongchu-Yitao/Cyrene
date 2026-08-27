# Cyrene PluginPack 自定义界面

Cyrene 只有一套插件框架。自定义工具、应用服务、上下文 Hook、模型和 Workbench 界面都由 `Plugin` / `PluginPack` 提供；旧的 `plugin.json`、项目插件子进程和 `cyrene.view` 扩展点已废弃。

## 目录格式

用户插件位于应用数据目录的 `plugin_impl/`。一个工具可以是直接暴露 `plugin` 的 Python 文件；包含应用界面时使用目录包，并从 `__init__.py` 暴露 `plugin_pack`。

```python
from agent.plugin import PluginPack
from .application import setup_application

plugin_pack = PluginPack(
    id="example_dashboard",
    description="Example dashboard.",
    plugins=(),
    application_setup=setup_application,
    metadata={
        "frontend_views": ({
            "id": "main",
            "entry": "ui/index.html",
            "title": "Dashboard",
            "i18n": {"zh": {"title": "仪表盘"}},
        },),
        "project_tools": ({
            "id": "main",
            "view": "main",
            "title": "Dashboard",
            "subtitle": "Plugin view",
            "icon_text": "◇",
            "i18n": {"zh": {"title": "仪表盘", "subtitle": "插件视图"}},
        },),
    },
)
```

`project_tools[].view` 必须指向同一包的 `frontend_views[].id`。View 的 `entry` 必须位于插件包内部。启用插件包后，入口显示在 Workbench 左侧栏，打开后成为普通 Pane，支持上下/左右分屏、拖动、恢复和独立窗口。

## 后端 RPC

```python
async def load(arguments, request_context):
    return {"ok": True, "project_id": request_context["project_id"]}

def setup_application(context):
    context.provide_frontend_method("dashboard.load", load)
```

iframe 使用 `postMessage` 发送：

```js
parent.postMessage({source:'cyrene-plugin',type:'call',requestId,method,args}, '*')
```

宿主返回 `source: "cyrene-host"` 的 `init` 和 `response` 消息。iframe 默认使用 sandbox，不能直接访问宿主 React 或 DOM。

## 创建流程

Agent 使用 `PluginAuthoringGuide → PluginScaffold → PluginValidate → PluginInstall` 创建和安装插件。`PluginScaffold.plugin_type` 支持：

- `standalone_tool`：直接注册的独立工具文件；
- `tool_pack`：包含一个或多个普通工具的工具包；
- `model_plugin`：支持模型发现和补全的 Provider；
- `context_plugin`：通过 Hook 构建上下文；
- `application_plugin`：应用路由、服务和生命周期；
- `ui_plugin`：带后端 RPC 的 Workbench 分屏界面；
- `full_pack`：组合以上能力的完整插件包。

生成结果均带英文、中文元数据。`PluginReload` 重新扫描唯一的插件目录。新增或修改 `application_setup` 需要重启应用；纯前端资源由宿主直接读取。

完整示例见 `examples/plugins/model-usage`。
