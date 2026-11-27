"""Assistant tools: authority comes from the host, never from model arguments."""

from cyrene.core.context.tasks import task_state_field
from cyrene.core.plugin import Plugin
from cyrene.core.plugin.execution import require_plugin_execution
from cyrene.plugins.native_runtime import resolve_exportable_path, run_context_value

from .definitions import TOOL_NAMES, definition
from .schemas import CardError


def caller(context):
    owner = run_context_value(context, "session_id")
    if run_context_value(context, "personal_assistant", False) is True:
        owner = run_context_value(context, "assistant_home_owner") or owner or context.tree_id
    owner = str(owner or "").strip()
    if not owner:
        raise CardError("missing_session", "Card tools require a conversation session")
    tasks = context.services.get("task_contexts")
    context_id = task_state_field(tasks, "active") if tasks is not None else None
    return owner, context_id


def read_html_file(context, owner, path):
    """Use the same file authority as assistant delivery, then snapshot UTF-8."""
    try:
        try:
            resolved = resolve_exportable_path(path)
        except ValueError:
            tasks = context.services.get("session_management")
            resolved = tasks.export_path(owner, path) if tasks else None
            if resolved is None:
                raise
        if not resolved.is_file():
            raise ValueError("HTML path must name an existing regular file")
        return resolved.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise CardError("html_file_unavailable", f"Cannot read HTML file {path}: {exc}") from exc


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
            value = await service.command(owner, context_id, name, arguments, effect=effect, origin=origin,
                                          allow_home=run_context_value(context, "personal_assistant", False) is True,
                                          read_html=lambda path: read_html_file(context, owner, path))
            return {"ok": True, **value}
        except CardError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}

    return Plugin(name=name, description=description, input_schema=schema, handler=handler,
                  allow_parallel=read_only, timeout_seconds=30,
                  metadata={"agent_exposure": "direct", "main_only": True, "requires_order": not read_only, "read_only": read_only})


plugins = tuple(create_tool(name) for name in TOOL_NAMES)
