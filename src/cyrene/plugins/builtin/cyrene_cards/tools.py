"""Assistant tools: authority comes from the host, never from model arguments."""

from cyrene.core.plugin import Plugin
from cyrene.core.plugin.execution import require_plugin_execution
from cyrene.plugins.native_runtime import run_context_value

from .definitions import TOOL_NAMES, definition
from .schemas import CardError


def caller(context):
    if run_context_value(context, "personal_assistant", False) is not True:
        raise CardError("assistant_only", "Card tools are only available to the personal assistant")
    owner = str(run_context_value(context, "assistant_home_owner") or run_context_value(context, "session_id") or context.tree_id or "").strip()
    if not owner:
        raise CardError("missing_session", "Card tools require an assistant session")
    tasks = context.services.get("task_contexts")
    context_id = tasks.read().get("active") if tasks is not None else None
    return owner, context_id


def create_tool(name):
    description, schema = definition(name)
    read_only = name in {"card.get", "card.list"}

    async def handler(arguments, context):
        try:
            owner, context_id = caller(context)
            service = context.services.get("cards")
            if service is None:
                raise CardError("cards_unavailable", "The Cards application service is not active")
            effect = None
            if not read_only:
                execution = require_plugin_execution()
                effect = f"{context.node_id}:{execution.call.id}"
            origin = {"project_id": str(run_context_value(context, "project_id") or ""), "node_id": context.node_id}
            value = await service.command(owner, context_id, name, arguments, effect=effect, origin=origin)
            return {"ok": True, **value}
        except CardError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}

    return Plugin(name=name, description=description, input_schema=schema, handler=handler,
                  allow_parallel=read_only, timeout_seconds=30,
                  metadata={"main_only": True, "model_visible": "assistant", "requires_order": not read_only, "read_only": read_only})


plugins = tuple(create_tool(name) for name in TOOL_NAMES)
