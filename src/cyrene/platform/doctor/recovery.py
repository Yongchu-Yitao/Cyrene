"""Bounded recovery for read-only diagnostic model calls, never work tools."""
from __future__ import annotations

import asyncio

from .evidence import error_code

MODEL_CALL_TIMEOUT = 45
ANALYSIS_TIMEOUT = 180
MAX_RECOVERY_CALLS = 2
RECOVERABLE = frozenset({
    'model_timeout', 'model_connection_failed', 'model_response_incomplete',
    'model_response_invalid', 'model_output_truncated', 'model_service_unavailable',
})


async def complete_with_recovery(gateway, messages, *, retry_budget, on_retry=None, timeout=MODEL_CALL_TIMEOUT, backoff=1, **kwargs):
    """Retry only the failed inference; retain successful evidence/tool results."""
    while True:
        try:
            return await asyncio.wait_for(gateway.complete(messages, **kwargs), timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = 'model_timeout' if isinstance(exc, TimeoutError) else error_code(exc)
            if code not in RECOVERABLE or retry_budget[0] <= 0:
                raise
            retry_budget[0] -= 1
            if code == 'model_output_truncated':
                kwargs['max_tokens'] = min(8000, max(4000, kwargs.get('max_tokens') or 0) * 2)
            if on_retry is not None:
                await on_retry(code, MAX_RECOVERY_CALLS - retry_budget[0])
            await asyncio.sleep(backoff)
