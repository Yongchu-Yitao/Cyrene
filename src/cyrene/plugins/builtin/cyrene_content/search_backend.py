"""
Deep Search Pipeline -- search and parallel page fetching.

Architecture:
  SimpleXNG Searcher --> parallel Fetch --> Evidence
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from urllib.parse import urlparse

import httpx
import requests

from cyrene.localization import accept_language, locale_tag
from cyrene.observability.trace import new_trace_id, trace_span
from .search_settings import provider_api_key, runtime_settings
from .runtime_config import SEARXNG_URL
from .deepseek_web_search import (
    DeepSeekWebSearchError,
    find_official_deepseek_search_candidate,
    search_with_deepseek,
)

logger = logging.getLogger(__name__)


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


class ProviderHealthRegistry:
    """Application-owned circuit state for individual search providers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._health: dict[str, dict[str, object]] = {}

    @staticmethod
    def _closed(provider: str) -> dict[str, object]:
        return {
            "provider": provider,
            "state": "closed",
            "error_code": "",
            "retryable": True,
            "retry_scope": "after_delay",
            "retry_after_ms": None,
            "consecutive_failures": 0,
        }

    def reset(self) -> None:
        with self._lock:
            self._health.clear()

    def before_call(self, provider: str) -> SearchBackendUnavailable | None:
        normalized = str(provider or "").strip()
        now = time.monotonic()
        with self._lock:
            record = self._health.get(normalized)
            if not record or record.get("state") == "closed":
                return None
            if record.get("state") == "half_open" and record.get("probe_inflight"):
                return SearchBackendUnavailable(
                    "Search provider circuit is waiting for its half-open probe.",
                    provider=normalized,
                    error_code="provider_probe_inflight",
                    retryable=True,
                    retry_scope="after_delay",
                    retry_after_ms=1_000,
                    affects_health=False,
                    circuit_scope="run_plugin",
                )
            opened_until = record.get("opened_until")
            if isinstance(opened_until, (int, float)) and now >= float(opened_until):
                record["state"] = "half_open"
                record["probe_inflight"] = True
                return None
            retry_after_ms = None
            if isinstance(opened_until, (int, float)):
                retry_after_ms = max(0, int((float(opened_until) - now) * 1000))
            return SearchBackendUnavailable(
                "Search provider circuit is open.",
                provider=normalized,
                error_code=str(record.get("error_code") or "provider_circuit_open"),
                retryable=record.get("retryable") is True,
                retry_scope=str(record.get("retry_scope") or "never"),
                retry_after_ms=retry_after_ms,
                affects_health=False,
                circuit_scope="run_plugin",
            )

    def record_failure(self, failure: SearchBackendUnavailable) -> None:
        provider = str(failure.provider or "").strip()
        if not provider or not failure.affects_health:
            return
        with self._lock:
            previous = self._health.get(provider) or self._closed(provider)
            failures = int(previous.get("consecutive_failures") or 0) + 1
            opened_until = (
                None
                if failure.retry_scope == "after_config_change"
                else time.monotonic() + max(1, failure.retry_after_ms or 30_000) / 1000
            )
            self._health[provider] = {
                "provider": provider,
                "state": "open",
                "error_code": failure.error_code,
                "retryable": failure.retryable,
                "retry_scope": failure.retry_scope,
                "retry_after_ms": failure.retry_after_ms,
                "opened_until": opened_until,
                "consecutive_failures": failures,
            }

    def record_success(self, provider: str) -> None:
        normalized = str(provider or "").strip()
        if not normalized:
            return
        with self._lock:
            self._health[normalized] = self._closed(normalized)

    def snapshots(self, providers: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        now = time.monotonic()
        snapshots: list[dict[str, object]] = []
        with self._lock:
            for provider in providers:
                record = deepcopy(self._health.get(provider) or self._closed(provider))
                opened_until = record.pop("opened_until", None)
                record.pop("probe_inflight", None)
                if isinstance(opened_until, (int, float)):
                    record["retry_after_ms"] = max(
                        0,
                        int((float(opened_until) - now) * 1000),
                    )
                snapshots.append(record)
        return tuple(snapshots)


def _provider_request_failure(
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


def _proxied_session() -> requests.Session:
    """创建 requests Session，如果配置了代理则使用代理。"""
    from .search_service import get_effective_search_proxy

    s = requests.Session()
    # Proxy selection is centralized in get_effective_search_proxy().  Letting
    # requests merge OS/environment proxies again can override the explicit
    # Cyrene address (notably on macOS) or resurrect a proxy rejected as
    # unreachable by the policy layer.
    s.trust_env = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": accept_language(),
    })
    proxy_url = get_effective_search_proxy()
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    return s


_HTTP_TIMEOUT = 30.0
_MAX_CONCURRENT = 20
_EVIDENCE_EXCERPT_CHARS = 1_500
_PREVIEW_REMAINING_TIMEOUT = 5.0


def _simplexng_language() -> str:
    """Return an explicit search override or Cyrene's effective locale tag."""

    for key in ("CYRENE_SEARCH_LOCALE", "SEARXNG_LANGUAGE"):
        override = str(os.environ.get(key) or "").strip()
        if override:
            return override
    return locale_tag()


def _get_simplexng_url() -> str:
    """Resolve the app-managed SimpleXNG search API URL."""
    external_url = str(SEARXNG_URL or "").strip().rstrip("/")
    if external_url:
        return external_url
    from .search_service import get_manager
    manager = get_manager()
    if manager.is_running:
        return manager.url
    return ""


def _is_loopback_url(url: str) -> bool:
    hostname = urlparse(url).hostname
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _unresponsive_engine_names(data: dict) -> list[str]:
    failures = data.get("unresponsive_engines") or []
    return sorted({
        str(item[0]).strip()
        for item in failures
        if isinstance(item, (list, tuple)) and item and str(item[0]).strip()
    })


async def _search_simplexng(query: str, *, max_results: int = 5) -> list[dict]:
    """Search via the built-in SimpleXNG SearXNG-compatible API."""
    base_url = _get_simplexng_url()
    if not base_url:
        raise SearchBackendUnavailable(
            "Web search backend is not running or configured.",
            error_code="provider_not_running",
            retryable=True,
            retry_scope="after_delay",
        )
    url = f"{base_url.rstrip('/')}/search"
    search_language = _simplexng_language()
    headers = {
        "Accept": "application/json",
        "Accept-Language": accept_language(search_language),
    }

    def _fetch() -> list[dict]:
        if _is_loopback_url(base_url):
            # Local SimpleXNG traffic must not be routed through a system proxy.
            sess = requests.Session()
            sess.trust_env = False
        else:
            # External SearXNG may require Cyrene's configured search proxy.
            sess = _proxied_session()
        with sess:
            r = sess.get(
                url,
                params={
                    "q": query,
                    "format": "json",
                    "language": search_language,
                    "safesearch": "0",
                },
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            raw_results = data.get("results") or []
            failed_engines = _unresponsive_engine_names(data)
            if not raw_results and failed_engines:
                engine_list = ", ".join(failed_engines)
                raise SearchBackendUnavailable(
                    "Web search backend could not obtain results because search "
                    f"engines were unreachable: {engine_list}.",
                    error_code="upstream_unreachable",
                    retryable=True,
                    retry_scope="after_delay",
                )
            return raw_results

    try:
        raw_results = await asyncio.to_thread(_fetch)
    except SearchBackendUnavailable:
        raise
    except Exception as exc:
        logger.warning("SimpleXNG search failed: %s", exc)
        raise _provider_request_failure("simplexng", exc) from exc

    results = []
    for r in raw_results:
        title = r.get("title", "").strip()
        url_val = r.get("url", "")
        content = r.get("content", "").strip()
        if title and url_val:
            results.append({"title": title, "url": url_val, "snippet": content, "query": query})
        if len(results) >= max(1, min(8, int(max_results))):
            break

    return results

def _strip_html(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    # Remove script/style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    # Remove all tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&#x27;", "'")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _fetch_url(url: str) -> str:
    """Fetch a URL and return its plain text content, truncated to 3000 chars."""

    session = _proxied_session()

    def _fetch() -> str:
        r = session.get(url, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        encoding = r.encoding
        if not encoding or encoding.lower() in {"iso-8859-1", "latin-1", "windows-1252"}:
            # 无 charset 声明的页面 requests 默认按 ISO-8859-1 解码,
            # 中文站点(UTF-8/GBK)会整体乱码;改按字节级检测
            encoding = r.apparent_encoding or "utf-8"
        return r.content.decode(encoding, errors="replace")

    try:
        html = await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.debug("Failed to fetch URL %r: %s", url, exc)
        return ""
    finally:
        session.close()

    text = _strip_html(html)
    return text[:3000]


async def _fetch_preview_url(url: str, client: httpx.AsyncClient) -> str:
    """Fetch a preview page with cancellation support and no request timeout."""
    try:
        response = await client.get(url)
        response.raise_for_status()
        encoding = response.charset_encoding
        if not encoding:
            probe = requests.Response()
            probe._content = response.content
            encoding = probe.apparent_encoding or "utf-8"
        html = response.content.decode(encoding, errors="replace")
    except Exception as exc:
        logger.debug("Failed to fetch preview URL %r: %s", url, exc)
        return ""
    return _strip_html(html)[:3000]


async def _fetch_preview_pages(
    results: list[dict],
    *,
    remaining_timeout: float = _PREVIEW_REMAINING_TIMEOUT,
) -> list[str]:
    """Fetch three pages concurrently, then bound laggards after first success."""
    from .search_service import get_effective_search_proxy

    proxy_url = get_effective_search_proxy()
    async with httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Accept-Language": accept_language(),
        },
        proxy=proxy_url or None,
        trust_env=False,
        timeout=None,
        follow_redirects=True,
    ) as client:
        tasks = [
            asyncio.create_task(_fetch_preview_url(str(result.get("url") or ""), client))
            for result in results[:3]
        ]
        task_indexes = {task: index for index, task in enumerate(tasks)}
        outputs = [""] * len(tasks)
        pending = set(tasks)
        first_success = False

        while pending and not first_success:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                value = task.result()
                outputs[task_indexes[task]] = value
                first_success = first_success or bool(value)

        if first_success and pending:
            done, pending = await asyncio.wait(pending, timeout=remaining_timeout)
            for task in done:
                outputs[task_indexes[task]] = task.result()

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return outputs


def _self_contained_search_result(
    relevant_results: list[dict],
    fetched_contents: list[str],
) -> str:
    """Expose fetched evidence for the main agent.

    The previous pipeline fetched page bodies but discarded nearly all of them
    after an internal synthesis call. That duplicated the main Agent's final
    synthesis and made it call WebFetch for evidence already retrieved. This
    projection performs no new network or model work.
    """
    sections = [
        "WebSearch completed search and page retrieval. "
        "No internal answer synthesis was performed; synthesize the final answer "
        "from the evidence below. Do not call WebFetch for the listed sources "
        "unless the user explicitly requests an exact quotation/full-page "
        "verification or a required detail is absent below.",
        "",
        "Source evidence:",
    ]
    for index, result in enumerate(relevant_results, start=1):
        content = (
            fetched_contents[index - 1]
            if index - 1 < len(fetched_contents)
            else ""
        ) or str(result.get("snippet") or "")
        excerpt = str(content).strip()[:_EVIDENCE_EXCERPT_CHARS]
        sections.extend(
            [
                f"[{index}] {result.get('title', '?')}",
                f"URL: {result.get('url', '')}",
                f"Excerpt: {excerpt}" if excerpt else "Excerpt: unavailable",
                "",
            ]
        )
    return "\n".join(sections).rstrip()


def _preview_search_result(
    results: list[dict],
    fetched_contents: list[str],
) -> str:
    """Return page previews for the first three search results."""
    sections = [
        "WebSearch completed a preview search and fetched the first three result pages.",
        "Use the titles, URLs, and page previews below to answer. If broader evidence "
        "is needed, repeat WebSearch "
        'with detail="content".',
        "",
        "Preview results:",
    ]
    for index, result in enumerate(results, start=1):
        content = (
            fetched_contents[index - 1]
            if index - 1 < len(fetched_contents)
            else ""
        ) or str(result.get("snippet") or "")
        preview = str(content).strip()[:_EVIDENCE_EXCERPT_CHARS]
        sections.extend(
            [
                f"[{index}] {result.get('title', '?')}",
                f"URL: {result.get('url', '')}",
                f"Preview: {preview}" if preview else "Preview: unavailable",
                "",
            ]
        )
    return "\n".join(sections).rstrip()


def _normalized_api_results(
    raw_results: list[dict],
    query: str,
    *,
    max_results: int = 8,
) -> list[dict]:
    results: list[dict] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        snippet = str(raw.get("content") or raw.get("description") or "").strip()
        if title and url and snippet:
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "query": query,
            })
        if len(results) >= max(1, min(8, int(max_results))):
            break
    return results


async def _search_tavily(topic: str, *, max_results: int = 5) -> str:
    api_key = provider_api_key("tavily")
    if not api_key:
        raise SearchBackendUnavailable(
            "Tavily API key is not configured.",
            error_code="credentials_missing",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )

    def _request() -> list[dict]:
        with _proxied_session() as session:
            response = session.post(
                "https://api.tavily.com/search",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "query": topic,
                    "search_depth": "basic",
                    "max_results": max(1, min(8, int(max_results))),
                    "include_answer": False,
                    "include_raw_content": False,
                },
                timeout=_HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("results") or []

    try:
        results = _normalized_api_results(
            await asyncio.to_thread(_request),
            topic,
            max_results=max_results,
        )
    except Exception as exc:
        raise _provider_request_failure("tavily", exc) from exc
    if not results:
        raise SearchBackendUnavailable(
            "Tavily returned no usable search content.",
            error_code="no_results",
            retryable=True,
            retry_scope="different_arguments",
            retry_after_ms=None,
            affects_health=False,
            circuit_scope="none",
        )
    return _self_contained_search_result(results, [item["snippet"] for item in results])


async def _search_brave(topic: str, *, max_results: int = 5) -> str:
    api_key = provider_api_key("brave")
    if not api_key:
        raise SearchBackendUnavailable(
            "Brave Search API key is not configured.",
            error_code="credentials_missing",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )

    def _request() -> list[dict]:
        with _proxied_session() as session:
            response = session.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": topic,
                    "count": max(1, min(8, int(max_results))),
                    "safesearch": "moderate",
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                timeout=_HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            web = payload.get("web") if isinstance(payload, dict) else None
            return web.get("results") or [] if isinstance(web, dict) else []

    try:
        results = _normalized_api_results(
            await asyncio.to_thread(_request),
            topic,
            max_results=max_results,
        )
    except Exception as exc:
        raise _provider_request_failure("brave", exc) from exc
    if not results:
        raise SearchBackendUnavailable(
            "Brave Search returned no usable search content.",
            error_code="no_results",
            retryable=True,
            retry_scope="different_arguments",
            retry_after_ms=None,
            affects_health=False,
            circuit_scope="none",
        )
    return _self_contained_search_result(results, [item["snippet"] for item in results])


# ---------------------------------------------------------------------------
# Main entry: deep_search
# ---------------------------------------------------------------------------


async def _deep_search_simplexng(
    topic: str,
    *,
    detail: str = "content",
    max_results: int = 5,
) -> str:
    """Run the SimpleXNG search pipeline.

    Stages:
        1. Query selection: use the original user topic
        2. SimpleXNG search + fetch URL contents

    Any transport failure, empty result set, or result set without usable
    evidence is reported to the ordered provider chain so it can continue.
    """
    logger.info("Deep search starting for: %s", topic)

    # -----------------------------------------------------------------------
    # Stage 1: Single query only — 不生成多轮搜索，避免触发限流
    # -----------------------------------------------------------------------
    queries = [topic]
    logger.info("Stage 1: single query only")

    # -----------------------------------------------------------------------
    # Stage 2: Parallel search and fetch
    # -----------------------------------------------------------------------
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _limited_search(q: str) -> list[dict]:
        async with semaphore:
            return await _search_simplexng(q, max_results=max_results)

    search_tasks = [_limited_search(q) for q in queries]

    async with trace_span(
        "search_stage", "simplexng_search", attributes={"query_count": len(queries)}
    ):
        search_results = await asyncio.gather(*search_tasks)

    all_results: list[dict] = []
    for sr in search_results:
        if isinstance(sr, list):
            all_results.extend(sr)

    logger.info("Stage 2 search complete: %d raw results (SimpleXNG)", len(all_results))

    if not all_results:
        raise SearchBackendUnavailable(
            "SimpleXNG returned no usable search results.",
            error_code="no_results",
            retryable=True,
            retry_scope="different_arguments",
            retry_after_ms=None,
            affects_health=False,
            circuit_scope="none",
        )

    # Deduplicate by URL (keep first occurrence)
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for r in all_results:
        u = r.get("url", "")
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped.append(r)
        elif not u:
            deduped.append(r)

    deduped = deduped[:max_results]

    fetch_targets = deduped[:3] if detail == "preview" else deduped

    if detail == "preview":
        started = time.perf_counter()
        fetched_results = await _fetch_preview_pages(fetch_targets)
        fetch_ms = (time.perf_counter() - started) * 1000
        for index, result_item in enumerate(fetch_targets):
            result_item["fetched_content"] = fetched_results[index]
        logger.info(
            "Preview fetch complete: %d URLs fetched (%.0f ms)",
            sum(1 for item in fetched_results if item),
            fetch_ms,
        )
        preview_contents = [
            r.get("fetched_content", "") or r.get("snippet", "")
            for r in fetch_targets
        ]
        if not any(str(content or "").strip() for content in preview_contents):
            raise SearchBackendUnavailable(
                "SimpleXNG returned previews without usable content.",
                error_code="no_results",
                retryable=True,
                retry_scope="different_arguments",
                retry_after_ms=None,
                affects_health=False,
                circuit_scope="none",
            )
        result = _preview_search_result(fetch_targets, preview_contents)
        logger.info(
            "WebSearch preview result generated (%d chars, %d sources)",
            len(result),
            len(fetch_targets),
        )
        return result

    # requests.Session is not thread-safe. Each concurrent fetch owns its
    # session instead of sharing one across asyncio.to_thread workers.
    async def _limited_fetch(r: dict) -> str:
        url = r.get("url", "")
        if not url:
            return ""
        async with semaphore:
            return await _fetch_url(url)

    fetch_tasks = [_limited_fetch(r) for r in fetch_targets]
    started = time.perf_counter()
    async with trace_span(
        "search_stage", "simplexng_fetch", attributes={"url_count": len(fetch_tasks)}
    ) as fetch_span:
        fetched_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        fetch_span.set_attribute(
            "fetched_count",
            sum(1 for item in fetched_results if isinstance(item, str) and item),
        )
    fetch_ms = (time.perf_counter() - started) * 1000

    # Attach fetched content back to results
    for i, r in enumerate(fetch_targets):
        if i < len(fetched_results) and isinstance(fetched_results[i], str):
            r["fetched_content"] = fetched_results[i]
        else:
            r["fetched_content"] = ""

    logger.info(
        "Stage 2 fetch complete: %d URLs fetched (%.0f ms)",
        sum(1 for f in fetched_results if isinstance(f, str) and f),
        fetch_ms,
    )

    # DEBUG: 打印原始搜索结果标题
    if deduped:
        logger.warning("=== Stage 2 raw results (%d) ===", len(deduped))
        for i, r in enumerate(deduped[:10]):
            logger.warning("  [%d] %s | %s", i+1, r.get("title", "?")[:50], r.get("url", "")[:60])
        logger.warning("=== end raw results ===")

    fetched_contents = [r.get("fetched_content", "") or r.get("snippet", "") for r in deduped]
    if not any(str(content or "").strip() for content in fetched_contents):
        raise SearchBackendUnavailable(
            "SimpleXNG returned results without usable content.",
            error_code="no_results",
            retryable=True,
            retry_scope="different_arguments",
            retry_after_ms=None,
            affects_health=False,
            circuit_scope="none",
        )
    result = _self_contained_search_result(deduped, fetched_contents)
    logger.info(
        "WebSearch evidence result generated (%d chars, %d sources)",
        len(result),
        len(deduped),
    )
    return result


async def _deep_search_deepseek(topic: str) -> str:
    """Native DeepSeek Responses-API web search."""
    candidate = find_official_deepseek_search_candidate()
    if candidate is None:
        raise SearchBackendUnavailable(
            "no official DeepSeek account configured",
            error_code="credentials_missing",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )
    async with trace_span(
        "search_stage",
        "deepseek_pipeline",
        attributes={"backend": "deepseek", "model": candidate.search_model},
    ) as deepseek_span:
        result = await search_with_deepseek(topic, candidate)
        deepseek_span.set_attribute("answer_chars", len(result.text))
        deepseek_span.set_attribute("duration_ms", result.duration_ms)
        deepseek_span.set_attribute(
            "total_tokens", result.usage.get("total_tokens", 0)
        )
        return result.text


async def _run_search_provider(
    provider: str,
    topic: str,
    *,
    detail: str = "content",
    max_results: int = 5,
) -> str:
    try:
        if provider == "simplexng":
            return await _deep_search_simplexng(
                topic,
                detail=detail,
                max_results=max_results,
            )
        if provider == "deepseek":
            return await _deep_search_deepseek(topic)
        if provider == "tavily":
            return await _search_tavily(topic, max_results=max_results)
        if provider == "brave":
            return await _search_brave(topic, max_results=max_results)
        raise SearchBackendUnavailable(
            f"Unknown search provider: {provider}.",
            error_code="provider_unknown",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )
    except SearchBackendUnavailable as exc:
        raise exc.for_provider(provider) from exc
    except DeepSeekWebSearchError as exc:
        status_match = re.search(r"HTTP\s+(\d{3})", str(exc))
        status_code = int(status_match.group(1)) if status_match else None
        if status_code in {401, 403}:
            raise SearchBackendUnavailable(
                "DeepSeek rejected its configured credentials.",
                provider=provider,
                error_code="credentials_invalid",
                retryable=False,
                retry_scope="after_config_change",
                retry_after_ms=None,
            ) from exc
        if status_code == 429:
            raise SearchBackendUnavailable(
                "DeepSeek rate limit was reached.",
                provider=provider,
                error_code="rate_limited",
                retryable=True,
                retry_scope="after_delay",
                retry_after_ms=60_000,
            ) from exc
        raise SearchBackendUnavailable(
            str(exc),
            provider=provider,
            error_code="provider_request_failed",
            retryable=True,
            retry_scope="after_delay",
        ) from exc
    except Exception as exc:
        logger.exception("Search provider %s failed unexpectedly", provider)
        raise SearchBackendUnavailable(
            f"{provider} failed ({exc.__class__.__name__}).",
            provider=provider,
            error_code="provider_internal_error",
            retryable=False,
            retry_scope="new_run",
            retry_after_ms=None,
        ) from exc


async def _deep_search_impl(
    topic: str,
    *,
    db_path: str = "",
    session_id: str = "",
    round_id: str = "",
    detail: str = "content",
    max_results: int = 5,
    provider_health: ProviderHealthRegistry | None = None,
) -> str:
    """Try enabled search providers in user-configured order."""
    del db_path, session_id, round_id
    settings = runtime_settings()
    if not settings.enabled:
        raise SearchBackendUnavailable(
            "Web search is disabled in Settings.",
            error_code="search_disabled",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )
    if not settings.providers:
        raise SearchBackendUnavailable(
            "No search provider is enabled in Settings.",
            error_code="no_provider_enabled",
            retryable=False,
            retry_scope="after_config_change",
            retry_after_ms=None,
        )
    health = provider_health or ProviderHealthRegistry()
    failures: list[str] = []
    classified_failures: list[SearchBackendUnavailable] = []
    for provider in settings.providers:
        blocked = health.before_call(provider)
        if blocked is not None:
            classified_failures.append(blocked)
            failures.append(f"{provider}: {blocked}")
            continue
        try:
            async with trace_span(
                "search_stage",
                f"{provider}_pipeline",
                attributes={"backend": provider},
            ):
                result = str(
                    await _run_search_provider(
                        provider,
                        topic,
                        detail=detail,
                        max_results=max_results,
                    )
                ).strip()
            if not result:
                raise SearchBackendUnavailable(
                    "provider returned empty content",
                    provider=provider,
                    error_code="no_results",
                    retryable=True,
                    retry_scope="different_arguments",
                    retry_after_ms=None,
                    affects_health=False,
                    circuit_scope="none",
                )
            health.record_success(provider)
            return result
        except SearchBackendUnavailable as exc:
            classified = exc.for_provider(provider)
            health.record_failure(classified)
            classified_failures.append(classified)
            failures.append(f"{provider}: {classified}")
            logger.warning("Search provider %s unavailable: %s", provider, classified)
    only_no_results = bool(classified_failures) and all(
        failure.error_code == "no_results" for failure in classified_failures
    )
    retryable = any(failure.retryable for failure in classified_failures)
    retry_after_values = [
        failure.retry_after_ms
        for failure in classified_failures
        if failure.retry_after_ms is not None
    ]
    if only_no_results:
        error_code = "search_no_results"
        retry_scope = "different_arguments"
        circuit_scope = "none"
        retry_after_ms = None
    elif retry_after_values:
        error_code = "search_providers_unavailable"
        retry_scope = "after_delay"
        circuit_scope = "run_plugin"
        retry_after_ms = min(retry_after_values)
    else:
        error_code = "search_providers_unavailable"
        retry_scope = (
            "after_config_change"
            if classified_failures
            and all(
                failure.retry_scope == "after_config_change"
                for failure in classified_failures
            )
            else "new_run"
        )
        circuit_scope = "run_plugin"
        retry_after_ms = None
    raise SearchBackendUnavailable(
        "All enabled search providers failed. " + " | ".join(failures),
        error_code=error_code,
        retryable=retryable,
        retry_scope=retry_scope,
        retry_after_ms=retry_after_ms,
        affects_health=False,
        circuit_scope=circuit_scope,
        provider_health=health.snapshots(settings.providers),
    )


async def deep_search(
    topic: str,
    *,
    db_path: str = "",
    session_id: str = "",
    round_id: str = "",
    detail: str = "content",
    max_results: int = 5,
    provider_health: ProviderHealthRegistry | None = None,
) -> str:
    """Run one search under a stable, query-free trace identifier."""
    search_id = new_trace_id("search")
    async with trace_span(
        "search",
        "web_search",
        span_id=search_id,
        db_path=db_path,
        attributes={"session_id_present": bool(session_id), "round_id_present": bool(round_id)},
    ) as search_span:
        result = await _deep_search_impl(
            topic,
            db_path=db_path,
            session_id=session_id,
            round_id=round_id,
            detail=detail,
            max_results=max_results,
            provider_health=provider_health,
        )
        search_span.set_attribute("answer_chars", len(result))
        return result
