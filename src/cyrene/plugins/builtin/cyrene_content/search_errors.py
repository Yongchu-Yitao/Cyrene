"""Provider failure classification shared by search routing and health tracking."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import httpx
import requests

from .deepseek_web_search import DeepSeekWebSearchError


class SearchBackendUnavailable(RuntimeError):
    """A classified provider or aggregate search failure."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        error_code: str = "search_provider_unavailable",
        retryable: bool = True,
        retry_scope: str = "after_delay",
        retry_after_ms: int | None = 30_000,
        affects_health: bool = True,
        circuit_scope: str = "run_plugin",
        provider_health: tuple[Mapping[str, object], ...] = (),
    ) -> None:
        super().__init__(str(message or error_code))
        self.provider = str(provider or "").strip()
        self.error_code = str(error_code or "search_provider_unavailable")
        self.retryable = bool(retryable)
        self.retry_scope = str(retry_scope or "never")
        self.retry_after_ms = (
            max(0, int(retry_after_ms)) if retry_after_ms is not None else None
        )
        self.affects_health = bool(affects_health)
        self.circuit_scope = str(circuit_scope or "none")
        self.provider_health = tuple(
            deepcopy(dict(item))
            for item in provider_health
            if isinstance(item, Mapping)
        )

    def for_provider(self, provider: str) -> SearchBackendUnavailable:
        return SearchBackendUnavailable(
            str(self),
            provider=self.provider or provider,
            error_code=self.error_code,
            retryable=self.retryable,
            retry_scope=self.retry_scope,
            retry_after_ms=self.retry_after_ms,
            affects_health=self.affects_health,
            circuit_scope=self.circuit_scope,
            provider_health=self.provider_health,
        )


def provider_request_failure(
    provider: str,
    exc: Exception,
) -> SearchBackendUnavailable:
    """Classify transport failures without exposing credentials or response bodies."""

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {401, 403}:
        return SearchBackendUnavailable(
            f"{provider} rejected its configured credentials.",
            provider=provider,
            error_code="credentials_invalid",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )
    if status_code == 429:
        retry_after_ms = 60_000
        headers = getattr(response, "headers", {})
        raw_retry_after = headers.get("Retry-After") if isinstance(headers, Mapping) else None
        try:
            retry_after_ms = max(1_000, int(float(raw_retry_after) * 1000))
        except (TypeError, ValueError):
            pass
        return SearchBackendUnavailable(
            f"{provider} rate limit was reached.",
            provider=provider,
            error_code="rate_limited",
            retryable=True,
            retry_scope="after_delay",
            retry_after_ms=retry_after_ms,
        )
    if isinstance(exc, (requests.Timeout, httpx.TimeoutException)):
        error_code = "provider_timeout"
    else:
        error_code = "provider_request_failed"
    return SearchBackendUnavailable(
        f"{provider} request failed ({exc.__class__.__name__}).",
        provider=provider,
        error_code=error_code,
        retryable=True,
        retry_scope="after_delay",
        retry_after_ms=30_000,
    )


def deepseek_request_failure(
    exc: DeepSeekWebSearchError,
    provider: str = "deepseek",
) -> SearchBackendUnavailable:
    """Translate standalone API failures into the search fallback/retry policy."""
    status_code = exc.status_code
    if exc.error_code in {"no_results", "invalid_arguments"}:
        return SearchBackendUnavailable(
            str(exc),
            provider=provider,
            error_code=exc.error_code,
            retryable=True,
            retry_scope="different_arguments",
            retry_after_ms=None,
            affects_health=False,
            circuit_scope="none",
        )
    if status_code in {401, 403}:
        return SearchBackendUnavailable(
            "DeepSeek rejected its configured credentials.",
            provider=provider,
            error_code="credentials_invalid",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )
    if status_code == 429:
        return SearchBackendUnavailable(
            "DeepSeek rate limit was reached.",
            provider=provider,
            error_code="rate_limited",
            retryable=True,
            retry_scope="after_delay",
            retry_after_ms=60_000,
        )
    if status_code == 402:
        return SearchBackendUnavailable(
            "DeepSeek account has insufficient balance.",
            provider=provider,
            error_code="insufficient_balance",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )
    if status_code is not None and 400 <= status_code < 500 and status_code != 408:
        return SearchBackendUnavailable(
            str(exc),
            provider=provider,
            error_code="invalid_arguments",
            retryable=True,
            retry_scope="different_arguments",
            retry_after_ms=None,
            affects_health=False,
            circuit_scope="none",
        )
    return SearchBackendUnavailable(
        str(exc),
        provider=provider,
        error_code="provider_request_failed",
        retryable=True,
        retry_scope="after_delay",
    )
