# 自定义工具

Cyrene 的自定义工具直接使用内置工具的 Python 模块格式。没有额外的 manifest、SDK、运行时声明、进程协议或发布流程。

## 存放位置

所有源码都放在系统用户数据目录的 `custom-tools` 子目录：

- macOS：`~/Library/Application Support/Cyrene/custom-tools`
- Windows：`%APPDATA%\Cyrene\custom-tools`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/Cyrene/custom-tools`
- 设置了 `CYRENE_USER_DATA_DIR` 时：`$CYRENE_USER_DATA_DIR/custom-tools`

该规则同时适用于源码运行、普通安装包和 portable 版本。目录可以直接放工具文件，也可以按文件夹组织工具包：

```text
custom-tools/
├── hello.py
└── office/
    ├── summarize.py
    ├── export.py
    └── _shared.py
```

以下划线开头的 Python 文件用于辅助代码，不会被当作工具入口。

## 工具格式

每个公开工具模块导出：

- `TOOL_DEF`：与内置工具相同的 OpenAI function 定义。
- `handler`：异步函数，签名为 `handler(args, bot, chat_id, db_path, notify_state)`。
- `TOOL_METADATA`：可选，沿用内置工具的执行调度元数据。

最小示例：

```python
from __future__ import annotations

from typing import Any


TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "Hello",
        "description": "向指定名字问好。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}

TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("custom:hello",),
    "requires_order": False,
}


async def handler(
    args: dict[str, Any],
    bot: Any,
    chat_id: int,
    db_path: str,
    notify_state: dict[str, bool] | None,
) -> str:
    return f"Hello, {args['name']}!"
```

handler 的返回值、外部命令、依赖和数据文件都由工具自身负责。Cyrene 不为自定义工具安装 Python、Node、uv、mise 或任何依赖，也不创建独立虚拟环境。

## 加载与修改

Cyrene 启动时扫描该目录，并监听后续文件变化。新增、编辑、改名或删除 Python 文件后会自动重载；也可以在“设置 → 扩展与系统 → 自定义工具”手动重载并查看工具或加载错误。

对话中的 Agent 不使用专门的自定义工具 CRUD。它通过现有的 Read、Write、Edit、Glob、Grep 和 Bash 文件接口管理上述目录，沿用当前会话已有的文件权限与确认流程。

“设置 → 扩展与系统 → 自定义工具”会按工具包分组显示源码。每个工具包标题栏右侧都有独立开关，下方会将该包的工具逐个显示为卡片；点击工具卡片可以查看源码路径、能力 ID、输入参数 Schema 和执行元数据。Cyrene 会保存最近一次成功加载的纯展示索引，因此关闭工具包并重启后仍可显示其工具卡片，同时不会为展示而导入 Python 源码；从未成功加载或关闭期间已改动的包只能显示尚未加载的源文件。关闭的工具不会进入 Agent 的工具目录或执行路径。总开关仍位于“设置 → 能力”；关闭 `custom_tools` 会停用所有自定义工具包，但保留各包原有的开关选择。

## 执行模型

自定义 handler 与内置 handler 使用相同调用链：参数按声明的 schema 校验，调用经过现有工具包、actor、hook、超时、事件和结果回传逻辑，handler 收到当前 Agent round 的 `bot`、`chat_id`、`db_path` 与 `notify_state`。

自定义工具是用户完全信任的本地 Python 代码，会在 Cyrene 进程内导入和执行。它拥有与 Cyrene 本身相同的系统权限，没有沙箱、代码审阅、diff 或二次发布批准。导入阶段的顶层代码也会执行，因此只应放入愿意完全信任的源码。

## 名称

新工具会进入 `custom_tools` 工具包。自定义工具名称与内置工具相同时，可替换该内置工具的公开实现；原始实现仍可通过系统限定身份访问。同一工具包中的重名工具都会被拒绝并显示加载冲突；不同工具包可以定义同名工具，它们会保留各自的包限定 capability，但不会取得未限定名称或覆盖同名系统工具。
