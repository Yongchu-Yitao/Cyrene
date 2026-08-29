"""Load selected Workbench interactive-response format contracts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from cyrene.core.plugin import Plugin, PluginContext
from cyrene.plugins.native_runtime import plugin_localized

TOOL_NAME = "LoadRendererContract"
_FORMATS = ("details", "card", "chart", "button", "layout")

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Load the exact Workbench syntax for selected interactive response "
            "formats. Call this only when an interactive block would materially "
            "improve the answer, and request only the formats you need."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(_FORMATS)},
                    "minItems": 1,
                    "maxItems": len(_FORMATS),
                    "description": "Renderer format contracts to load.",
                },
            },
            "required": ["formats"],
            "additionalProperties": False,
        },
    },
}

TOOL_METADATA = {
    "read_only": True,
    "requires_order": False,
    "resource_keys": ("renderer:workbench-contract",),
}

_CONTRACTS = {
    "details": """### details
Use for long derivations, optional analysis, or detailed alternatives:
```text
:::details Title
Markdown content
:::
```""",
    "card": """### card
Use for one structured result or compact comparison:
```text
:::card Title
Markdown content
:::
```""",
    "chart": """### chart
Use `:::chart <type>` where type is `line`, `scatter`, or `bar`. The body is declarative data, never code:
```text
:::chart line
x: [-2,-1,0,1,2]
y-binds: "a*x*x + b"
controls:
  - param: a
    range: [-5, 5]
    step: 0.1
    default: 1
  - param: b
    range: [-5, 5]
    step: 0.1
    default: 0
options:
  title: Interactive curve
  grid: true
:::
```
`x` is numeric. Supply either a same-length numeric `y` array or `y-binds`, which may use only numbers, `x`, declared control params, and `+ - * / ( )`. Every other variable requires a control with `param`, `range: [min, max]`, `step`, and `default`. Options may include `title`, `grid`, `color`, `x-min`, `x-max`, `y-min`, and `y-max`. Keep the spec under 32 KB.""",
    "button": """### button
Use only when one concrete action helps the user continue:
```text
:::button
label: Start translation
action_id: translate_start
style: primary
mode: model
value: zh->en
:::
```
`action_id` must match lowercase `[a-z0-9_]+` and be at most 32 characters. `style` is `primary`, `default`, or `danger`. `mode` is `local` for a frontend event or `model` to send a new user turn. `value` is optional context up to 256 characters; `disabled: true` starts the button inert. A `mode: model` event begins with `[按钮操作]`; treat it as the user pressing the button.""",
    "layout": """### layout
Group buttons with `actions`:
```text
:::actions
  :::button
  label: Continue
  action_id: continue
  style: primary
  mode: model
  :::
:::
```
Place cards or charts side by side with `:::grid cols: 2`; indent every child by two spaces. `actions` may contain only buttons; `grid` may contain only cards or charts. Containers cannot contain containers, and no other nesting is allowed (maximum depth 2).""",
}

_GLOBAL_RULES = """### Global rules
- Keep the essential answer in normal Markdown; use interactive blocks only when they materially improve clarity.
- Close every block with `:::` on its own line.
- Never place an interactive block inside a `details` or `card` body.
- Follow only the contracts loaded in this result."""


async def handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    run_context = context.data.get("run_context")
    capabilities = context.data.get("response_capabilities")
    if capabilities is None and isinstance(run_context, Mapping):
        capabilities = run_context.get("response_capabilities")
    supported = (
        capabilities
        if isinstance(capabilities, Collection)
        and not isinstance(capabilities, (str, bytes, bytearray))
        else ()
    )
    if "interactive_blocks" not in supported:
        return plugin_localized(
            context,
            "Tool unavailable: the current client does not support interactive response blocks.",
            "工具不可用：当前客户端不支持交互式响应块。",
        )
    requested = args.get("formats")
    if not isinstance(requested, list):
        return plugin_localized(
            context,
            "Tool failed: formats must be an array.",
            "工具失败：formats 必须是数组。",
        )
    formats = list(dict.fromkeys(str(item or "").strip() for item in requested))
    unknown = [item for item in formats if item not in _CONTRACTS]
    if unknown:
        return plugin_localized(
            context,
            "Tool failed: unsupported renderer format(s): {formats}",
            "工具失败：不支持的渲染格式：{formats}",
            formats=", ".join(unknown),
        )
    if not formats:
        return plugin_localized(
            context,
            "Tool failed: select at least one renderer format.",
            "工具失败：请至少选择一种渲染格式。",
        )
    sections = [_CONTRACTS[item] for item in formats]
    return "\n\n".join([
        "[Trusted Workbench renderer contract]",
        *sections,
        _GLOBAL_RULES,
    ])


_FUNCTION = TOOL_DEF["function"]
plugin = Plugin(
    name=TOOL_NAME,
    description=str(_FUNCTION["description"]),
    input_schema=dict(_FUNCTION["parameters"]),
    handler=handler,
    allow_parallel=True,
    timeout_seconds=30.0,
    metadata={**TOOL_METADATA, "main_only": True},
)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "plugin"]
