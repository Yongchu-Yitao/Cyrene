"""Graceful teardown for Cyrene's cross-cutting background work."""

from __future__ import annotations


async def shutdown_background_work() -> None:
    """Flush host-owned background work after domain run managers stop.

    Workbench Chat, Task, and Goal Loop own their AgentSession bridges and are
    drained by the application lifespan before this cross-cutting cleanup is
    entered.  Keeping the retired global Agent coordinator here caused a
    graceful server shutdown to mutate an unrelated legacy session instead of
    preserving the Plugin ContextTrees that startup recovery reopens.
    """
    from cyrene.agent_runtime import get_acp_runtime_service
    from cyrene.agent_runtime.model_gateway import revoke_all_model_gateway_scopes

    await get_acp_runtime_service().close_all()
    revoke_all_model_gateway_scopes()
