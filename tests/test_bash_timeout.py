import asyncio
import importlib
import os
import shlex
import sys

import pytest

from cyrene.core.plugin import (
    Plugin,
    PluginContext,
    PluginExecutionError,
    PluginPack,
    PluginRegistry,
    PluginRuntime,
)
from cyrene.core.plugin.core_impl.permission_boundaries import bash_boundary

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
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(PluginExecutionError) as captured:
            await task
        assert captured.value.failure.error_code == 'plugin_timeout'
        assert captured.value.failure.retryable is True
        assert captured.value.failure.details == {'timeout_ms': 300}
    await asyncio.sleep(0.8)
    assert ready.exists()
    assert not marker.exists()


@pytest.mark.asyncio
async def test_bash_timeout_is_a_retryable_runtime_result(tmp_path):
    runtime = PluginRuntime(PluginRegistry())
    result = await runtime.call(
        'Bash',
        {
            'command': (
                f'{shlex.quote(sys.executable)} '
                '-c "import time; time.sleep(1)"'
            ),
            'timeout_ms': 20,
        },
        PluginContext(workspace=tmp_path),
    )

    assert result.success is False
    assert result.failure is not None
    assert result.failure.error_code == 'plugin_timeout'
    assert result.failure.retryable is True
    assert result.failure.retry_scope == 'after_delay'
    assert result.failure.details == {'timeout_ms': 20}
    assert result.error == 'Bash command timed out after 0.02 seconds.'


def test_bash_boundary_requires_confirmation_for_root_scan(tmp_path):
    request = bash_boundary(
        {
            'command': (
                'find / -name "plugin.py" -path "*cyrene/core*" '
                '2>/dev/null | head -3'
            )
        },
        PluginContext(workspace=tmp_path),
    )

    assert request is not None
    assert request['kind'] == 'broad_read_confirmation'
    assert request['path_hint'] == '/'
    assert request['requires_human'] is True


def test_bash_boundary_reviews_scoped_reads_outside_workspace(tmp_path):
    outside = tmp_path.parent / 'installed.txt'
    request = bash_boundary(
        {'command': f'head -20 {shlex.quote(str(outside))}'},
        PluginContext(workspace=tmp_path),
    )

    assert request is not None
    assert request['kind'] == 'read_elevation'
    assert request['path_hint'] == str(outside)
    assert request['requires_human'] is False


def test_bash_boundary_keeps_workspace_reads_unreviewed(tmp_path):
    assert bash_boundary(
        {'command': 'find . -name "*.py" | head -3'},
        PluginContext(workspace=tmp_path),
    ) is None


@pytest.mark.asyncio
async def test_runtime_handles_timeout_without_configured_deadline(tmp_path):
    async def timeout(_arguments, _context):
        raise asyncio.TimeoutError

    registry = PluginRegistry(include_core=False)
    registry.register_pack(PluginPack(
        'self-timed',
        'self-timed',
        (Plugin('SelfTimed', 'Self timed', {'type': 'object'}, timeout),),
    ), source='test')
    result = await PluginRuntime(registry).call(
        'SelfTimed',
        {},
        PluginContext(workspace=tmp_path),
    )

    assert result.success is False
    assert result.failure is not None
    assert result.failure.error_code == 'plugin_timeout'
    assert result.error == 'Plugin timed out.'
