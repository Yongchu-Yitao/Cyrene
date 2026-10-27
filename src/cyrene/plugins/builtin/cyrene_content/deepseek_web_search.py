"""DeepSeek's standalone Search and Fetch JSON APIs.

This module deliberately recognizes only DeepSeek's official API endpoint.
OpenAI-compatible proxies that happen to expose a DeepSeek-named model must not
receive requests or an unrelated provider key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from cyrene.plugins.model_catalog import candidate_provider_id, model_plugin_catalog
from .search_service import get_effective_search_proxy

_OFFICIAL_HOST = "api.deepseek.com"
_OFFICIAL_PATHS = {"", "/", "/v1", "/v1/"}
_SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
_FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"
_TIMEOUT_SECONDS = 120.0
_PREVIEW_EVIDENCE_CHARS = 1500
_CONTENT_EVIDENCE_CHARS = 8000


@dataclass(frozen=True, slots=True)
class DeepSeekSearchCandidate:
    """Official DeepSeek credentials selected from the user's model settings."""

    candidate_id: str
    configured_model: str
    api_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DeepSeekWebSearchResult:
    """Source evidence consumed by Cyrene's WebSearch tool (no LLM call)."""

    text: str
    duration_ms: int
    result_count: int


class DeepSeekWebSearchError(RuntimeError):
    """A safe, credential-free failure from the Search or Fetch API."""

    def __init__(self, message: str, *, status_code: int | None = None, error_code: str = "provider_request_failed") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def _is_official_deepseek_url(base_url: str) -> bool:
    """Return whether *base_url* is the documented official OpenAI endpoint."""
    try:
        parsed = urlparse(str(base_url or "").strip())
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == _OFFICIAL_HOST
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
        and parsed.path in _OFFICIAL_PATHS
        and not parsed.query
        and not parsed.fragment
    )


def _configured_model_id(raw: dict[str, Any]) -> str:
    model = str(raw.get("model") or raw.get("name") or raw.get("id") or "").strip()
    # Bracketed context suffixes are accepted by some DeepSeek integrations.
    return model.split("[", 1)[0].strip().lower()


def _uses_deepseek_plugin(raw: dict[str, Any]) -> bool:
    return candidate_provider_id(raw) == "deepseek"


def _configured_deepseek_models() -> list[dict[str, Any]]:
    available = {
        str(item.get("id") or "").strip().lower()
        for item in model_plugin_catalog()
        if isinstance(item, dict)
    }
    if "deepseek" not in available:
        return []
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    if service is None:
        return []
    configuration = service.get_model_configuration()
    result: list[dict[str, Any]] = []
    for profile in configuration.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        candidate = service.candidate_for_profile(
            str(profile.get("id") or ""),
            configuration,
        )
        if candidate is not None:
            result.append(dict(candidate))
    return result


def find_official_deepseek_search_candidate(
    models: list[dict[str, Any]] | None = None,
) -> DeepSeekSearchCandidate | None:
    """Reuse an enabled official DeepSeek profile's key, independent of model."""

    configured = list(_configured_deepseek_models() if models is None else models)

    for index, item in enumerate(configured):
        if not isinstance(item, dict):
            continue
        configured_model = _configured_model_id(item)
        if (
            not _uses_deepseek_plugin(item)
            or item.get("enabled") is False
            or not _is_official_deepseek_url(str(item.get("base_url") or ""))
        ):
            continue
        api_key = str(item.get("api_key") or "").strip()
        if len(api_key) >= 2 and api_key[0] == api_key[-1] and api_key[0] in {'"', "'"}:
            api_key = api_key[1:-1].strip()
        if not api_key:
            continue
        return DeepSeekSearchCandidate(
            candidate_id=str(item.get("id") or f"candidate-{index + 1}").strip(),
            configured_model=configured_model,
            api_key=api_key,
        )
    return None


def _http_url(value: Any) -> str:
    url = value.strip() if isinstance(value, str) else ""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port < 1)
    ):
        return ""
    return url


async def _request(
    endpoint: str,
    payload: dict[str, Any],
    candidate: DeepSeekSearchCandidate,
    *,
    operation: str,
) -> dict[str, Any]:
    """Use the search proxy without forwarding credentials on redirects/errors."""
    headers = {
        "Authorization": f"Bearer {candidate.api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(_TIMEOUT_SECONDS, connect=5.0)
    try:
        proxy_url = get_effective_search_proxy()
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            proxy=proxy_url or None,
            trust_env=False,
        ) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise DeepSeekWebSearchError(
            f"DeepSeek {operation} transport failed ({exc.__class__.__name__})"
        ) from exc
    if response.status_code != 200:
        raise DeepSeekWebSearchError(
            f"DeepSeek {operation} returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise DeepSeekWebSearchError(f"DeepSeek {operation} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise DeepSeekWebSearchError(f"DeepSeek {operation} returned a non-object response")
    return data


def _search_evidence(data: dict[str, Any], *, need_content: bool, count: int) -> tuple[str, int]:
    results = data.get("results")
    if not isinstance(results, list):
        raise DeepSeekWebSearchError("DeepSeek search returned invalid results")
    sections = [
        "WebSearch returned source evidence from DeepSeek Search. "
        "Synthesize the answer from the evidence below; no internal answer synthesis was performed.",
    ]
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        url = _http_url(result.get("url"))
        if not url or url in seen:
            continue
        # Prefer the long, query-specific summary when page content is unavailable.
        content = result.get("content") if need_content else ""
        evidence = next((
            value.strip() for value in (content, result.get("summary"), result.get("snippet"))
            if isinstance(value, str) and value.strip()
        ), "")
        if not evidence:
            continue
        seen.add(url)
        title = " ".join(str(result.get("title") or url).split())
        sections.extend(["", f"[{len(seen)}] {title}", f"URL: {url}"])
        for key in ("date", "site_name", "rerank_score", "authority"):
            value = result.get(key)
            if value is not None:
                sections.append(f"{key}: {value}")
        limit = _CONTENT_EVIDENCE_CHARS if need_content else _PREVIEW_EVIDENCE_CHARS
        sections.append(f"Evidence: {evidence[:limit]}")
        if len(seen) >= count:
            break
    if not seen:
        raise DeepSeekWebSearchError("DeepSeek search returned no usable results", error_code="no_results")
    return "\n".join(sections), len(seen)


async def search_with_deepseek(
    query: str,
    candidate: DeepSeekSearchCandidate,
    *,
    detail: str = "content",
    max_results: int = 5,
) -> DeepSeekWebSearchResult:
    """Return ranked evidence from the standalone API without a model call."""
    query = str(query)
    if not query.strip() or len(query) > 128:
        raise DeepSeekWebSearchError(
            "DeepSeek search query must contain 1 to 128 characters",
            error_code="invalid_arguments",
        )
    if detail not in {"preview", "content"}:
        raise DeepSeekWebSearchError("Invalid DeepSeek search detail", error_code="invalid_arguments")
    count = max(1, min(50, int(max_results)))
    need_content = detail == "content"
    started = time.monotonic()
    data = await _request(
        _SEARCH_ENDPOINT,
        {"query": query, "count": count, "need_content": need_content},
        candidate,
        operation="search",
    )
    text, result_count = _search_evidence(data, need_content=need_content, count=count)
    return DeepSeekWebSearchResult(
        text=text,
        duration_ms=int((time.monotonic() - started) * 1000),
        result_count=result_count,
    )


async def fetch_with_deepseek(url: str, candidate: DeepSeekSearchCandidate) -> str:
    """Retrieve one page as Markdown through the standalone Fetch API."""
    url = str(url)
    if len(url) > 8192 or not _http_url(url):
        raise DeepSeekWebSearchError("Invalid DeepSeek fetch URL", error_code="invalid_arguments")
    data = await _request(_FETCH_ENDPOINT, {"url": url}, candidate, operation="fetch")
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise DeepSeekWebSearchError("DeepSeek fetch returned no page content", error_code="no_results")
    title = " ".join(str(data.get("title") or "").split())
    heading = f"{title}\n" if title else ""
    return f"{heading}URL: {url}\n\n{content.strip()}"


__all__ = [
    "DeepSeekSearchCandidate",
    "DeepSeekWebSearchError",
    "DeepSeekWebSearchResult",
    "find_official_deepseek_search_candidate",
    "search_with_deepseek",
    "fetch_with_deepseek",
]
