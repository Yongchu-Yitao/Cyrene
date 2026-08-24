"""
Deep Search Pipeline -- search and parallel page fetching.

Architecture:
  SimpleXNG Searcher --> parallel Fetch --> Evidence
"""

import asyncio
import ipaddress
import logging
import re
import time
from urllib.parse import urlparse

import httpx
import requests

from cyrene.config import SEARXNG_URL
from cyrene.observability.trace import new_trace_id, trace_span
from cyrene.runtime.search_settings import provider_api_key, runtime_settings
from cyrene.tooling.backends.deepseek_web_search import (
    DeepSeekWebSearchError,
    find_official_deepseek_search_candidate,
    search_with_deepseek,
)

logger = logging.getLogger(__name__)


class SearchBackendUnavailable(RuntimeError):
    """The configured search service could not execute the query."""


def _proxied_session() -> requests.Session:
    """创建 requests Session，如果配置了代理则使用代理。"""
    from cyrene.tooling.backends.searxng_manager import get_effective_search_proxy

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    proxy_url = get_effective_search_proxy()
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    return s


_HTTP_TIMEOUT = 30.0
_MAX_CONCURRENT = 20
_EVIDENCE_EXCERPT_CHARS = 1_500
_PREVIEW_REMAINING_TIMEOUT = 5.0


def _get_simplexng_url() -> str:
    """Resolve the app-managed SimpleXNG search API URL."""
    external_url = str(SEARXNG_URL or "").strip().rstrip("/")
    if external_url:
        return external_url
    from cyrene.tooling.backends.searxng_manager import get_manager
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


async def _search_simplexng(query: str) -> list[dict]:
    """Search via the built-in SimpleXNG SearXNG-compatible API."""
    base_url = _get_simplexng_url()
    if not base_url:
        raise SearchBackendUnavailable("Web search backend is not running or configured.")
    url = f"{base_url.rstrip('/')}/search"
    headers = {"Accept": "application/json"}

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
                    "language": "zh-CN",
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
                    f"engines were unreachable: {engine_list}."
                )
            return raw_results

    try:
        raw_results = await asyncio.to_thread(_fetch)
    except SearchBackendUnavailable:
        raise
    except Exception as exc:
        logger.warning("SimpleXNG search failed: %s", exc)
        raise SearchBackendUnavailable(
            f"SimpleXNG request failed ({exc.__class__.__name__})."
        ) from exc

    results = []
    for r in raw_results:
        title = r.get("title", "").strip()
        url_val = r.get("url", "")
        content = r.get("content", "").strip()
        if title and url_val:
            results.append({"title": title, "url": url_val, "snippet": content, "query": query})
        if len(results) >= 5:
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


async def _fetch_url(url: str, session: requests.Session | None = None) -> str:
    """Fetch a URL and return its plain text content, truncated to 3000 chars.

    Pass a shared ``session`` to reuse the connection pool (keep-alive and
    TLS handshake) across fetches; the caller owns its lifetime. Without one,
    a throwaway session is created and closed after the request.
    """
    own_session = session is None
    sess = session if session is not None else _proxied_session()

    def _fetch() -> str:
        r = sess.get(url, timeout=_HTTP_TIMEOUT)
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
        if own_session:
            sess.close()

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
    from cyrene.tooling.backends.searxng_manager import get_effective_search_proxy

    proxy_url = get_effective_search_proxy()
    async with httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            )
        },
        proxy=proxy_url or None,
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


def _normalized_api_results(raw_results: list[dict], query: str) -> list[dict]:
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
        if len(results) >= 8:
            break
    return results


async def _search_tavily(topic: str) -> str:
    api_key = provider_api_key("tavily")
    if not api_key:
        raise SearchBackendUnavailable("Tavily API key is not configured.")

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
                    "max_results": 8,
                    "include_answer": False,
                    "include_raw_content": False,
                },
                timeout=_HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("results") or []

    try:
        results = _normalized_api_results(await asyncio.to_thread(_request), topic)
    except Exception as exc:
        raise SearchBackendUnavailable(
            f"Tavily search request failed: {exc.__class__.__name__}."
        ) from exc
    if not results:
        raise SearchBackendUnavailable("Tavily returned no usable search content.")
    return _self_contained_search_result(results, [item["snippet"] for item in results])


async def _search_brave(topic: str) -> str:
    api_key = provider_api_key("brave")
    if not api_key:
        raise SearchBackendUnavailable("Brave Search API key is not configured.")

    def _request() -> list[dict]:
        with _proxied_session() as session:
            response = session.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": topic, "count": 8, "safesearch": "moderate"},
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
        results = _normalized_api_results(await asyncio.to_thread(_request), topic)
    except Exception as exc:
        raise SearchBackendUnavailable(
            f"Brave Search request failed: {exc.__class__.__name__}."
        ) from exc
    if not results:
        raise SearchBackendUnavailable("Brave Search returned no usable search content.")
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
            return await _search_simplexng(q)

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
        raise SearchBackendUnavailable("SimpleXNG returned no usable search results.")

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
                "SimpleXNG returned previews without usable content."
            )
        result = _preview_search_result(fetch_targets, preview_contents)
        logger.info(
            "WebSearch preview result generated (%d chars, %d sources)",
            len(result),
            len(fetch_targets),
        )
        return result

    # Content mode fetches page bodies using the existing shared session.
    fetch_session = _proxied_session()
    try:
        async def _limited_fetch(r: dict) -> str:
            url = r.get("url", "")
            if not url:
                return ""
            async with semaphore:
                return await _fetch_url(url, session=fetch_session)

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
    finally:
        fetch_session.close()

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
        raise SearchBackendUnavailable("SimpleXNG returned results without usable content.")
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
        raise DeepSeekWebSearchError("no official DeepSeek account configured")
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
            return await _search_tavily(topic)
        if provider == "brave":
            return await _search_brave(topic)
        raise SearchBackendUnavailable(f"Unknown search provider: {provider}.")
    except SearchBackendUnavailable:
        raise
    except DeepSeekWebSearchError as exc:
        raise SearchBackendUnavailable(str(exc)) from exc
    except Exception as exc:
        logger.exception("Search provider %s failed unexpectedly", provider)
        raise SearchBackendUnavailable(
            f"{provider} failed ({exc.__class__.__name__})."
        ) from exc


async def _deep_search_impl(
    topic: str,
    *,
    db_path: str = "",
    session_id: str = "",
    round_id: str = "",
    detail: str = "content",
    max_results: int = 5,
) -> str:
    """Try enabled search providers in user-configured order."""
    del db_path, session_id, round_id
    settings = runtime_settings()
    if not settings.enabled:
        raise SearchBackendUnavailable("Web search is disabled in Settings.")
    if not settings.providers:
        raise SearchBackendUnavailable("No search provider is enabled in Settings.")
    failures: list[str] = []
    for provider in settings.providers:
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
                raise SearchBackendUnavailable("provider returned empty content")
            return result
        except SearchBackendUnavailable as exc:
            failures.append(f"{provider}: {exc}")
            logger.warning("Search provider %s unavailable: %s", provider, exc)
    raise SearchBackendUnavailable(
        "All enabled search providers failed. " + " | ".join(failures)
    )


async def deep_search(
    topic: str,
    *,
    db_path: str = "",
    session_id: str = "",
    round_id: str = "",
    detail: str = "content",
    max_results: int = 5,
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
        )
        search_span.set_attribute("answer_chars", len(result))
        return result
