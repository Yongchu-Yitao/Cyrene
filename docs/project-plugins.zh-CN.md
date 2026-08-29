# Cyrene PluginPack 自定义界面

Cyrene 只有一套插件框架。自定义工具、应用服务、上下文 Hook、模型和 Workbench 界面都由 `Plugin` / `PluginPack` 提供；旧的 `plugin.json`、项目插件子进程和 `cyrene.view` 扩展点已废弃。

[English](project-plugins.md) · [简体中文](project-plugins.zh-CN.md)

## 目录格式

用户插件位于应用数据目录的 `plugin_impl/`。一个工具可以是直接暴露 `plugin` 的 Python 文件；包含应用界面时使用目录包，并从 `__init__.py` 暴露 `plugin_pack`。

```python
from cyrene.core.plugin import PluginPack
from cyrene.plugins import PluginApplicationContext
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

## Agent 组装与 Context Hook

插件不是在 Agent 创建完成后再“注入”内容；启用的插件包本身就是 Agent 的组成。
应用启动时，`application_setup` 可以贡献 Route、Service、Background Job 与界面；
打开对话时，`setup` 收到 `PluginSetupContext`，可以发布 Session Service 并把 Hook
绑定到当前 ContextTree；每一轮运行再由这些 Tree-local Hook 构建 Context、审核工具、
记录结果和完成收尾。

一个最小 Context 插件只需在 `SessionStart` 返回一个 Block：

```python
from cyrene.core.hook import SESSION_START, HookEvent
from cyrene.core.plugin import PluginPack, PluginSetupContext


def setup(context: PluginSetupContext) -> None:
    async def mount(_event: HookEvent) -> dict[str, str]:
        return {
            "context": "## Project rules\nOnly edit files in this workspace.",
            "context_position": "",
        }

    context.hooks.register(
        SESSION_START,
        mount,
        plugin_id="project_rules.mount",
        hook_id="project-rules-session-start",
        root_only=True,
        failure_policy="closed",
    )


plugin_pack = PluginPack(
    id="project_rules",
    description="Mount project rules into the Agent context.",
    plugins=(),
    setup=setup,
)
```

`context_position="system"` 用于基础 System Prompt，`"top"` 用于紧随其后的高
优先级 Block（例如 SOUL），空值用于普通 Context。普通 Block 保持确定性的注册顺序。
同一 Hook ID 会随 Tree 持久化；恢复 Session 时插件应通过 `bind_plugin(...,
replace=True)` 重新绑定实现，而不是建立第二份状态。

| Hook | 适合处理的工作 |
|---|---|
| `SessionStart` | 对话开始时只运行一次并冻结稳定 Context |
| `TurnStart` | 为每个用户轮次构建动态 Context |
| `ContextChange` / `ContextUsed` | 响应 Tree 变化，记录真实 Token 使用与触发压缩/记忆逻辑 |
| `PreToolUse` | 归一化、允许或阻止工具参数；需要阻止时使用 Fail Closed |
| `PostToolUse` | 持久化 Tool Result、Learning Evidence 或 Activity |
| `SessionEnd` | 最终结果落盘后的异步收尾 |
| `Stop` | 用户取消或 Session 关闭时停止插件拥有的任务 |

如果 `SessionStart` Callable 的输出依赖可能变化的稳定输入，应通过
`with_session_start_cache_fingerprint(hook, provider)` 挂载 Provider；Provider
返回任意可 JSON 序列化的依赖投影。绑定方法的宿主对象也可以直接实现
`session_start_cache_fingerprint(event)`。Kernel 将其视为不透明值，与 Hook 拓扑及
插件包实现版本一起计算指纹；值变化时只重建一次稳定前缀。SOUL、Memory、已学习
技能、CLI Hook 与第三方 Provider 的依赖逻辑因此仍归各自插件所有。

输入框选项不是由各插件分别读取 UI State。`cyrene_composer_context` 是唯一的输入框
上下文插件：它持久化当前对话选择，再在 `TurnStart` 读取已启用的 Workspace、
MCP 与 Skills Provider 并生成一个明确的 Mount。Plugin Center 负责插件是否可用；
Composer 菜单负责本对话选中什么；工具菜单负责“Agent 直接可见”或“Agent 寻找使用”。
三者职责互不重复。

工具包和独立工具共享同一个 `Plugin` 协议。直接可见工具的 Schema 进入即时 Tool
List；其他工具仍由 `toolbox.list → describe → invoke` 发现。两种方式都会使用当前
插件的 `input_schema`、Runtime 校验、`PreToolUse` 与 `PostToolUse` Hook。

## Contribution Scope

`PluginPack` 的贡献有三种明确生命周期：

| Scope | API | 归属 |
|---|---|---|
| Application | `application_setup` / `APPLICATION_SETUP` | Cyrene Plugin Application Host；Route、进程 Service、Search、Frontend RPC、Startup/Shutdown |
| Session | `setup` / `SESSION_SETUP` | `cyrene.core.AgentSession`；ContextTree Hook 与对话级 Service |
| Run | `PluginContext.services` 与 `RUN_SERVICE` | 单次调用/Run；Request Data 与临时 Service Binding |

两个 Callback Field 只是统一 Typed Extension 系统的便利写法，Host 最终消费
归一化的 `ExtensionContribution`。Core 不得依赖 Workbench 或 FastAPI Type。
Application Callback 使用 `cyrene.plugins.PluginApplicationContext`，Session 和
Run 则使用 `cyrene.core.plugin` 中与 Host 无关的 Context。

原 `agent.*`、`route.*`、`webui.*` Python 包已删除，也不提供兼容 Alias。
插件必须使用本文的 `cyrene.core`、`cyrene.plugins` 与 `cyrene.workbench` API。

`project_tools[].view` 必须指向同一包的 `frontend_views[].id`。View 的 `entry` 必须位于插件包内部。启用插件包后，入口显示在 Workbench 左侧栏，打开后成为普通 Pane，支持上下/左右分屏、拖动、恢复和独立窗口。

## 后端 RPC

```python
async def load(arguments, request_context):
    return {"ok": True, "project_id": request_context["project_id"]}

def setup_application(context: PluginApplicationContext) -> None:
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
