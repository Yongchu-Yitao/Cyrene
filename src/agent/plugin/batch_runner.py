"""Concurrent execution of one model-produced Plugin batch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping

from .batch_catcher import PluginBatchCatcher
from .plugin import PluginCall, PluginCallResult, PluginContext
from .runtime import PluginRuntime


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
        catcher = PluginBatchCatcher(batch, on_result=on_result)
        completed = dict(completed or {})
        for call in batch:
            if call.id in completed:
                catcher.catch(completed[call.id], notify=False)
        pending = tuple(call for call in batch if call.id not in completed)
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
                await asyncio.gather(
                    *(execute(item) for item in parallel[offset:offset + self.max_parallel_tools])
                )
            parallel = []

        for item in allowed:
            if item.plugin.allow_parallel:
                parallel.append(item)
                continue
            await flush_parallel()
            await execute(item)
        await flush_parallel()
        return catcher.results()


__all__ = ["PluginBatchRunner"]
