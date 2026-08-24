"""DeepSeek Responses API backed web search.

This module deliberately recognizes only DeepSeek's official API endpoint.
OpenAI-compatible proxies that happen to expose a DeepSeek-named model must not
receive the server-side ``web_search`` request or an unrelated provider key.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from cyrene.config import strip_wrapping_quotes
from cyrene.runtime.settings_store import get_custom_models
from cyrene.tooling.backends.searxng_manager import get_effective_search_proxy

logger = logging.getLogger(__name__)

_OFFICIAL_HOST = "api.deepseek.com"
_OFFICIAL_PATHS = {"", "/", "/v1", "/v1/"}
_CONFIGURED_MODEL_IDS = {"deepseek-v4-flash", "deepseek-v4-pro"}
_SEARCH_MODEL = "deepseek-v4-flash"
_RESPONSES_ENDPOINT = "https://api.deepseek.com/responses"
_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class DeepSeekSearchCandidate:
    """Official DeepSeek credentials selected from the user's model settings."""

    candidate_id: str
    configured_model: str
    api_key: str
    search_model: str = _SEARCH_MODEL


@dataclass(frozen=True, slots=True)
class DeepSeekWebSearchResult:
    """Normalized native-search result consumed by Cyrene's WebSearch tool."""

    text: str
    usage: dict[str, int]
    duration_ms: int
    model: str = _SEARCH_MODEL


class DeepSeekWebSearchError(RuntimeError):
    """A safe, credential-free failure from the native search backend."""


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


def find_official_deepseek_search_candidate(
    models: list[dict[str, Any]] | None = None,
) -> DeepSeekSearchCandidate | None:
    """Find the first configured official DeepSeek V4 model with credentials.

    ``custom_models`` is intentional: it preserves a user's configured
    DeepSeek account while Codex OAuth is temporarily selected as the primary
    source. Keys may be shared by sibling models on the same official endpoint,
    matching Cyrene's normal candidate behavior.
    """
    configured = list(get_custom_models() if models is None else models)
    shared_key = next(
        (
            strip_wrapping_quotes(str(item.get("api_key") or ""))
            for item in configured
            if isinstance(item, dict)
            and _is_official_deepseek_url(str(item.get("base_url") or ""))
            and strip_wrapping_quotes(str(item.get("api_key") or ""))
        ),
        "",
    )

    for index, item in enumerate(configured):
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "openai_compatible").strip().lower()
        configured_model = _configured_model_id(item)
        if (
            provider != "openai_compatible"
            or configured_model not in _CONFIGURED_MODEL_IDS
            or not _is_official_deepseek_url(str(item.get("base_url") or ""))
        ):
            continue
        api_key = strip_wrapping_quotes(str(item.get("api_key") or "")) or shared_key
        if not api_key:
            continue
        return DeepSeekSearchCandidate(
            candidate_id=str(item.get("id") or f"candidate-{index + 1}").strip(),
            configured_model=configured_model,
            api_key=api_key,
        )
    return None


def _normalized_usage(raw: Any) -> dict[str, int]:
    usage = raw if isinstance(raw, dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    details = usage.get("input_tokens_details")
    cached_tokens = (
        int(details.get("cached_tokens") or 0)
        if isinstance(details, dict)
        else 0
    )
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
        "prompt_cache_hit_tokens": cached_tokens,
        "prompt_cache_miss_tokens": max(0, input_tokens - cached_tokens),
    }


def _citation_from_annotation(annotation: Any) -> tuple[str, str] | None:
    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
        return None
    nested = annotation.get("url_citation")
    citation = nested if isinstance(nested, dict) else annotation
    url = str(citation.get("url") or "").strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    title = " ".join(str(citation.get("title") or parsed.netloc).split())
    title = title.replace("[", "\\[").replace("]", "\\]")
    return title or parsed.netloc, url


def _parse_response(data: Any) -> tuple[str, dict[str, int]]:
    if not isinstance(data, dict):
        raise DeepSeekWebSearchError("DeepSeek returned a non-object response")

    text_parts: list[str] = []
    sources: list[tuple[str, str]] = []
    saw_search_call = False
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "web_search_call":
                status = str(item.get("status") or "").strip().lower()
                saw_search_call = status in {"", "completed"}
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = str(part.get("text") or "").strip()
                if text:
                    text_parts.append(text)
                for annotation in part.get("annotations") or []:
                    citation = _citation_from_annotation(annotation)
                    if citation is not None:
                        sources.append(citation)

    # Some compatible gateways serialize the SDK's convenience field. Keep it
    # as a fallback without duplicating the canonical output-item text.
    if not text_parts:
        convenience_text = str(data.get("output_text") or "").strip()
        if convenience_text:
            text_parts.append(convenience_text)

    if not saw_search_call:
        raise DeepSeekWebSearchError("DeepSeek response did not execute web_search")
    if not text_parts:
        raise DeepSeekWebSearchError("DeepSeek web_search returned no answer text")

    deduped_sources: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for title, url in sources:
        if url not in seen_urls:
            seen_urls.add(url)
            deduped_sources.append((title, url))

    answer = "\n\n".join(text_parts)
    if deduped_sources:
        source_lines = [f"- [{title}]({url})" for title, url in deduped_sources]
        answer = f"{answer}\n\nSources:\n" + "\n".join(source_lines)
    return answer, _normalized_usage(data.get("usage"))


async def search_with_deepseek(
    query: str,
    candidate: DeepSeekSearchCandidate,
) -> DeepSeekWebSearchResult:
    """Run one forced server-side web search through DeepSeek Responses API."""
    payload = {
        "model": candidate.search_model,
        "instructions": (
            "Search the web to answer the user's question. Follow these rules exactly:\n"
            "1. Answer in the same language as the question.\n"
            "2. Start with a direct conclusion, then present the key facts.\n"
            "3. Ground every factual claim in the search results; never invent information.\n"
            "4. End with the sources as a markdown list: - [title](url), using only URLs the search returned."
        ),
        "input": str(query),
        "tools": [{"type": "web_search"}],
        "tool_choice": {"type": "web_search"},
        "thinking": {"type": "disabled"},
    }
    headers = {
        "Authorization": f"Bearer {candidate.api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(
        _TIMEOUT_SECONDS,
        connect=min(5.0, _TIMEOUT_SECONDS),
    )
    started = time.monotonic()
    try:
        proxy_url = get_effective_search_proxy()
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            proxy=proxy_url or None,
            trust_env=False,
        ) as client:
            response = await client.post(
                _RESPONSES_ENDPOINT,
                json=payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise DeepSeekWebSearchError(
            f"DeepSeek web_search transport failed ({exc.__class__.__name__})"
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise DeepSeekWebSearchError(
            f"DeepSeek web_search returned HTTP {response.status_code}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise DeepSeekWebSearchError("DeepSeek web_search returned invalid JSON") from exc

    text, usage = _parse_response(data)
    return DeepSeekWebSearchResult(
        text=text,
        usage=usage,
        duration_ms=int((time.monotonic() - started) * 1000),
        model=candidate.search_model,
    )


__all__ = [
    "DeepSeekSearchCandidate",
    "DeepSeekWebSearchError",
    "DeepSeekWebSearchResult",
    "find_official_deepseek_search_candidate",
    "search_with_deepseek",
]
