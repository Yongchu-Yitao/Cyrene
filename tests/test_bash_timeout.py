import asyncio
import importlib
import os
import shlex
import sys

import pytest

from cyrene.core.plugin import PluginContext

bash_module = importlib.import_module('cyrene.core.plugin.core_impl.bash')


@pytest.mark.asyncio
async def test_bash_explicit_deadline_is_not_overridden(tmp_path, monkeypatch):
    deadlines = []
    original = asyncio.wait_for

    async def capture(awaitable, timeout):
        deadlines.append(timeout)
        return await original(awaitable, timeout)

    monkeypatch.setattr(bash_module.asyncio, 'wait_for', capture)
    result = await bash_module.bash(
        {'command': 'echo complete', 'timeout_ms': 600_000}, PluginContext(workspace=tmp_path),
    )
    assert result['exit_code'] == 0
    assert deadlines == [600]
    assert bash_module.BASH_PLUGIN.timeout_seconds is None


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == 'nt', reason='POSIX process group integration')
@pytest.mark.parametrize('cancel', [False, True])
async def test_bash_stops_descendants_on_timeout_or_cancel(tmp_path, cancel):
    marker = tmp_path / 'leaked-child'
    ready = tmp_path / 'ready'
    script = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(0.7); Path({str(marker)!r}).touch()"
    command = f'{shlex.quote(sys.executable)} -c {shlex.quote(script)} & wait'
    task = asyncio.create_task(bash_module.bash(
        {'command': command, 'timeout_ms': 10_000 if cancel else 300},
        PluginContext(workspace=tmp_path),
    ))
    if cancel:
        async with asyncio.timeout(3):
            while not ready.exists():
                await asyncio.sleep(0.01)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    await asyncio.sleep(0.8)
    assert ready.exists()
    assert not marker.exists()
