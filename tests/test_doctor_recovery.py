import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyrene.platform.doctor.recovery import complete_with_recovery


def failure(code):
    exc = RuntimeError('controlled fault')
    exc.code = code
    return exc


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['model_response_incomplete', 'model_connection_failed', 'model_response_invalid', 'model_output_truncated'])
async def test_recovers_failed_inference_without_replaying_history(code):
    gateway = SimpleNamespace(complete=AsyncMock(side_effect=[failure(code), {'content':'complete'}]))
    progress = AsyncMock()
    history = [{'role':'tool', 'content':'existing evidence'}]
    result = await complete_with_recovery(gateway, history, retry_budget=[2], on_retry=progress, max_tokens=4000, backoff=0)
    assert result['content'] == 'complete'
    assert gateway.complete.call_count == 2
    assert all(call.args[0] is history for call in gateway.complete.call_args_list)
    assert gateway.complete.call_args.kwargs['max_tokens'] == (8000 if code == 'model_output_truncated' else 4000)
    progress.assert_awaited_once_with(code, 1)


@pytest.mark.asyncio
async def test_timeout_cancels_old_request_before_recovery():
    cancelled = []
    calls = 0
    async def complete(messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        assert cancelled
        return {'content':'recovered'}
    assert await complete_with_recovery(SimpleNamespace(complete=complete), [], retry_budget=[2], timeout=.01, backoff=0) == {'content':'recovered'}


@pytest.mark.asyncio
async def test_recovery_budget_is_shared_and_exhausts():
    gateway = SimpleNamespace(complete=AsyncMock(side_effect=failure('model_response_incomplete')))
    budget = [2]
    with pytest.raises(RuntimeError):
        await complete_with_recovery(gateway, [], retry_budget=budget, backoff=0)
    assert gateway.complete.call_count == 3
    assert budget == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize('exc', [failure('model_authentication_failed'), failure('model_quota_exceeded'), asyncio.CancelledError()])
async def test_auth_quota_and_cancellation_are_not_retried(exc):
    gateway = SimpleNamespace(complete=AsyncMock(side_effect=exc))
    with pytest.raises(type(exc)):
        await complete_with_recovery(gateway, [], retry_budget=[2], backoff=0)
    assert gateway.complete.call_count == 1
