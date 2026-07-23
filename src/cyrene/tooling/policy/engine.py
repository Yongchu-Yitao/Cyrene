"""Central actor, settings, and channel policy checks."""

from __future__ import annotations

from typing import Any

def tool_allowed_for_actor(concrete_name: str, actor: str) -> bool:
    from cyrene.tooling.catalog import is_tool_allowed_for_actor

    return is_tool_allowed_for_actor(concrete_name, actor)


def capability_available(
    concrete_name: str,
    *,
    capability_id: str,
    actor: str,
    bot: Any = None,
    enabled: bool | None = None,
) -> tuple[bool, str]:
    if not tool_allowed_for_actor(concrete_name, actor):
        return False, f"Capability `{capability_id}` is not allowed for actor `{actor}`."
    if enabled is False:
        return False, f"Capability `{capability_id}` is disabled in settings."
    if capability_id == "delivery.send_wechat_file" and not (
        bot is not None and hasattr(bot, "send_file")
    ):
        return False, f"Capability `{capability_id}` is unavailable in this channel."
    return True, ""
