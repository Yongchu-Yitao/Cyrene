"""Graceful teardown for Cyrene's cross-cutting background work."""

from __future__ import annotations


async def shutdown_background_work() -> None:
    """Stop agent jobs and flush short telemetry writes in dependency order."""
    from cyrene.agent.coordinator import shutdown_background_tasks as shutdown_coordinator
    from cyrene.agent.session import shutdown_session_tasks
    from cyrene.model_runtime.client import shutdown_background_tasks as shutdown_llm_telemetry
    from cyrene.knowledge.ingest import cancel_pending_tasks as cancel_knowledge_indexing
    from cyrene.subagent import timeout_all_subagent_tasks
    from cyrene.tooling.executor import shutdown_background_tasks as shutdown_tool_telemetry

    await shutdown_session_tasks()
    await shutdown_coordinator()
    await timeout_all_subagent_tasks("服务关闭，子代理已停止；重启后可重新执行任务。")
    await cancel_knowledge_indexing()
    await shutdown_tool_telemetry()
    await shutdown_llm_telemetry()
