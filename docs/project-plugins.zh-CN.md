# Cyrene 项目插件

项目插件是可信的本地代码，与 Custom Tools 和 Extension Center 相互独立。Cyrene 只负责安装包、按项目启停、独立进程、扩展注册、RPC 和 iframe Pane；模型文件、服务、端口、配置和业务正确性都由插件负责。

## 插件包

插件目录必须包含 `plugin.json`：

```json
{
  "apiVersion": 1,
  "id": "com.example.local-ai",
  "name": "Local AI",
  "version": "1.0.0",
  "backend": { "type": "python", "entry": "plugin.py" },
  "frontend": { "mode": "iframe", "entry": "ui/index.html" }
}
```

启用插件等同于允许其以当前用户身份运行本地代码。插件后端按“插件 × 项目”运行在独立 Python 子进程中；关闭插件会结束该进程并注销全部贡献。

## 后端 API

```python
def activate(context):
    context.register_method("usage.refresh", refresh_usage)
    context.register("cyrene.view", {
        "id": "usage.dashboard",
        "title": "模型用量",
    })
    context.register("cyrene.projectTool", {
        "id": "usage",
        "title": "模型用量",
        "view": "usage.dashboard",
    })

def deactivate(context):
    # 停止由插件创建的服务或子进程。
    pass
```

`context.package_dir`、`context.data_dir`、`context.project_id` 和 `context.plugin_id` 可直接使用。方法接收一个 JSON 参数并返回 JSON。插件可以用 `context.emit(name, payload)` 向已打开的 UI 发送事件。

## iframe 桥接

插件 UI 通过 `postMessage` 调用后端：

```js
const pending = new Map();

function call(method, args) {
  const requestId = crypto.randomUUID();
  parent.postMessage({
    source: "cyrene-plugin",
    type: "call",
    requestId,
    method,
    args
  }, "*");
  return new Promise((resolve, reject) => pending.set(requestId, { resolve, reject }));
}

addEventListener("message", event => {
  if (event.data?.source !== "cyrene-host") return;
  if (event.data.type === "init") window.pluginContext = event.data.context;
  if (event.data.type === "event") handlePluginEvent(event.data.event, event.data.payload);
  if (event.data.type === "response") {
    const request = pending.get(event.data.requestId);
    if (!request) return;
    pending.delete(event.data.requestId);
    event.data.ok ? request.resolve(event.data.result) : request.reject(new Error(event.data.error));
  }
});
```

`cyrene.projectTool` 的 `view` 指向一个 `cyrene.view`。插件只在当前项目启用时，这个入口才会显示在侧栏“工具”区域。点击后，Cyrene 创建 `kind: "plugin-view"` 的普通 Pane，因此自动支持现有的上下/左右分屏、交换、拖动、全屏和独立窗口。关闭插件会停止其进程并移除入口；已打开 Pane 会保留位置并显示禁用状态，重新开启后原位恢复。

插件 iframe 默认透明并跟随 Cyrene 当前的明暗 `color-scheme`，因此未设置页面背景时会显示所属 Pane 的统一表面色。插件可以通过自己的 `body` 背景或 `color-scheme` 明确覆盖这一默认外观。

## Chat Provider

插件可以把自己的模型直接加入 Agent Chat：

```python
async def complete(request):
    # 插件自行调用本地 runtime、远端 API 或已有服务。
    return {
        "message": {"role": "assistant", "content": "..."},
        "usage": {"prompt_tokens": 10, "completion_tokens": 4}
    }

def activate(context):
    context.register("cyrene.chatProvider", {
        "id": "local-runtime",
        "models": [{
            "id": "my-model",
            "name": "My Model",
            "capabilities": ["chat", "tools"],
            "contextLimit": 32768
        }],
        "complete": complete
    })
```

`complete` 收到模型、消息、工具定义、流式标志、推理强度、调用阶段和 session id。返回值可以是字符串、规范化 message，或 `{message, usage, events}`。如果未返回流事件，Cyrene 会把最终结果作为单个流式增量交给现有 Agent Chat。

其他开放扩展点可以直接注册，例如 `cyrene.command`、`cyrene.embeddingProvider`、`cyrene.ocrProvider`、`cyrene.asrProvider`、`cyrene.ttsProvider` 和 `cyrene.agentAction`。Cyrene 不解释这些插件的内部 runtime；需要被核心功能消费的扩展点由对应功能通过通用贡献查询和 RPC 调用。

## 斜杠命令

`cyrene.command` 会加入内置 Cyrene Agent 的动态斜杠命令目录。命令可以声明静态 Agent 提示，也可以用 `prepare` 在用户提交时动态生成提示：

```python
async def prepare_review(args):
    return {"prompt": "审查当前改动，优先报告正确性与回归风险。"}

def activate(context):
    context.register("cyrene.command", {
        "id": "review",
        "command": "review",
        "title": "审查改动",
        "description": "运行项目约定的代码审查流程",
        "prepare": prepare_review,
    })
```

`prepare` 收到 `commandId`、`arguments`、`chatId` 和 `projectId`，返回字符串或 `{prompt: "..."}`。也可省略 `prepare`，直接声明 `prompt`。命令名与内置命令冲突时，Cyrene 会自动使用带插件命名空间的稳定名称。插件命令只生成本轮 Agent 指令；工具调用仍服从正常权限与审核。

## 管理 API

设置 → 自定义插件提供安装、按当前项目启停、重载与删除；它与扩展中心、自定义工具完全独立。启停状态按项目保存：同一插件可以在项目 A 显示于“工具”，同时在项目 B 保持关闭。普通删除保留插件数据和日志，明确选择“删除插件及数据”才会清理它们。

- `POST /api/plugins/install`
- `GET /api/plugins?project_id=...`
- `POST /api/plugins/{id}/enabled`
- `POST /api/plugins/{id}/reload`
- `DELETE /api/plugins/{id}`：默认保留数据
- `DELETE /api/plugins/{id}?delete_data=true`：同时删除数据和日志
- `GET /api/plugins/contributions?project_id=...`
- `POST /api/plugins/{id}/call`
- `GET /api/plugins/{id}/logs?project_id=...`
- `GET /api/plugins/events?project_id=...`
- `GET /api/plugins/{id}/projects/{project_id}/assets/{path}`

完整的自带 UI 与后端 RPC 示例位于 `examples/plugins/model-usage`。

## Agent 插件开发工具包

Agent 通过独立的渐进式“插件开发工具”完成插件开发，而不是在每轮系统提示中常驻整份协议。识别到插件开发任务后，Agent 先按需读取 API v1 完整规范，然后可以完成以下闭环：

- 在当前项目 workspace 创建安全的插件骨架；
- 不执行插件代码地校验 manifest、入口边界、Python 语法、重复贡献和静态 View 引用；
- 安装或替换开发目录中的插件包；
- 对当前项目启用、停用和重载插件；
- 查询实时贡献、调用已注册后端方法并读取进程日志；
- 删除插件，或在明确的破坏性确认后同时删除数据与日志。

插件源码仍由 Agent 使用普通文件工具编辑；Cyrene 不替插件生成或管理 llama.cpp、GGUF、OCR、TTS/ASR 等业务 runtime。所有持久化生命周期变更和 Agent 发起的插件 RPC 都使用统一审核入口；自动模式由统一审核 Agent 对参数绑定的具体操作作出决定，不存在插件工具自己的审核旁路。删除插件数据继续遵守不可逆操作必须由真人确认的全局规则。
