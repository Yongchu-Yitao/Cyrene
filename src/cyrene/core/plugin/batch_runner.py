"""Concurrent execution of one model-produced Plugin batch."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone

from ..localization import localized
from ..observability import operation
from .batch_catcher import PluginBatchCatcher
from .plugin import PluginCall, PluginCallResult, PluginContext
from .runtime import PluginRuntime

logger = logging.getLogger(__name__)


class PluginBatchRunner:
    MAX_PARALLEL_TOOLS = 8

    def __init__(self, runtime: PluginRuntime, *, max_parallel_tools: int = 8) -> None:
        if not 1 <= int(max_parallel_tools) <= self.MAX_PARALLEL_TOOLS:
            raise ValueError("max_parallel_tools must be between 1 and 8")
        self.runtime = runtime
        self.max_parallel_tools = int(max_parallel_tools)

    async def run(
        self,
        calls: Iterable[PluginCall],
        context: PluginContext | None = None,
        *,
        completed: Mapping[str, PluginCallResult] | None = None,
        on_result: Callable[[PluginCallResult], None] | None = None,
    ) -> tuple[PluginCallResult, ...]:
        batch = tuple(calls)
        context_fields = {
            "tree_id": context.tree_id if context is not None else None,
            "node_id": context.node_id if context is not None else None,
            "context_data": dict(context.data) if context is not None else {},
        }
        with operation(
            logger,
            "plugin.batch",
            "run",
            calls=[
                {"call_id": call.id, "plugin": call.name, "arguments": dict(call.arguments)}
                for call in batch
            ],
            restored_call_ids=tuple((completed or {}).keys()),
            max_parallel_tools=self.max_parallel_tools,
            **context_fields,
        ) as op:
            catcher = PluginBatchCatcher(batch, on_result=on_result)
            completed = dict(completed or {})
            for call in batch:
                if call.id in completed:
                    catcher.catch(completed[call.id], notify=False)
            pending = tuple(call for call in batch if call.id not in completed)
            guidance = (
                context.services.get("guidance")
                if context is not None
                else None
            )

            def guidance_pending() -> bool:
                return bool(
                    guidance is not None
                    and getattr(guidance, "enabled", False)
                    and getattr(guidance, "has_pending", False)
                )

            def skip(item) -> None:
                call = getattr(item, "call", item)
                catcher.catch(
                    PluginCallResult(
                        call.id,
                        call.name,
                        True,
                        {
                            "status": "skipped",
                            "reason": "user_guidance",
                            "message": localized(
                                "Skipped because new user guidance arrived before execution.",
                                "因执行前收到新的用户引导，已跳过。",
                            ),
                        },
                        "",
                        datetime.now(timezone.utc),
                    )
                )

            if guidance_pending():
                for call in pending:
                    skip(call)
                reviewed = ()
            else:
                reviewed = await self.runtime.review_batch(pending, context)

            allowed = []
            for item in reviewed:
                if isinstance(item, PluginCallResult):
                    catcher.catch(item)
                else:
                    allowed.append(item)

            async def execute(item) -> None:
                catcher.catch(await self.runtime.execute(item, context))

            parallel: list = []

            async def flush_parallel() -> None:
                nonlocal parallel
                for offset in range(0, len(parallel), self.max_parallel_tools):
                    chunk = parallel[offset:offset + self.max_parallel_tools]
                    if guidance_pending():
                        for item in chunk:
                            skip(item)
                        for item in parallel[offset + self.max_parallel_tools:]:
                            skip(item)
                        break
                    await asyncio.gather(
                        *(execute(item) for item in chunk)
                    )
                parallel = []

            for item in allowed:
                if item.plugin.allow_parallel:
                    parallel.append(item)
                    continue
                await flush_parallel()
                if guidance_pending():
                    skip(item)
                else:
                    await execute(item)
            await flush_parallel()
            results = catcher.results()
            op.finish(
                result_count=len(results),
                succeeded=sum(item.success for item in results),
                failed=sum(not item.success for item in results),
                results=[
                    {
                        "call_id": item.call_id,
                        "plugin": item.name,
                        "success": item.success,
                        "value": item.value,
                        "error": item.error,
                    }
                    for item in results
                ],
            )
            return results


__all__ = ["PluginBatchRunner"]
