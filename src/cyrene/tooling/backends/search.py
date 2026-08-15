"""
Deep Search Pipeline -- search and parallel page fetching.

Architecture:
  SimpleXNG Searcher --> parallel Fetch --> Evidence
"""

import asyncio
import ipaddress
import logging
import re
import sys
import time
from urllib.parse import urlparse

import requests

from cyrene.config import SEARCH_PROXY, SEARXNG_URL
from cyrene.observability.trace import new_trace_id, trace_span
from cyrene.tooling.backends.deepseek_web_search import (
    DeepSeekWebSearchError,
    find_official_deepseek_search_candidate,
    search_with_deepseek,
)

logger = logging.getLogger(__name__)


def _proxied_session() -> requests.Session:
    """创建 requests Session，如果配置了代理则使用代理。"""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    if SEARCH_PROXY:
        s.proxies = {"http": SEARCH_PROXY, "https": SEARCH_PROXY}
    return s

_HTTP_TIMEOUT = 30.0
_MAX_CONCURRENT = 20
_EVIDENCE_EXCERPT_CHARS = 1_500

# Native DeepSeek web search is used only on Windows, where the locally
# managed SimpleXNG instance is unavailable. The adapter in
# deepseek_web_search.py recognizes only DeepSeek's official endpoint and
# never probes credentials; SimpleXNG remains the fallback on every platform.
_NATIVE_DEEPSEEK_SEARCH_ENABLED = sys.platform == "win32"

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


async def _search_simplexng(query: str) -> list[dict]:
    """Search via the built-in SimpleXNG SearXNG-compatible API."""
    base_url = _get_simplexng_url()
    if not base_url:
        return []
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
            r = sess.get(url, params={"q": query, "format": "json", "language": "zh-CN", "safesearch": "0"}, headers=headers, timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            return data.get("results", [])

    try:
        raw_results = await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.warning("SimpleXNG search failed: %s", exc)
        return []

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


# ---------------------------------------------------------------------------
# Main entry: deep_search
# ---------------------------------------------------------------------------


async def _deep_search_simplexng(topic: str) -> str:
    """Run the SimpleXNG search pipeline.

    Stages:
        1. Query selection: use the original user topic
        2. SimpleXNG search + fetch URL contents

    This remains the dependency-free fallback when DeepSeek native search is
    unavailable or the user has not configured an official DeepSeek account.
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
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    all_results: list[dict] = []
    for sr in search_results:
        if isinstance(sr, list):
            all_results.extend(sr)

    logger.info("Stage 2 search complete: %d raw results (SimpleXNG)", len(all_results))

    if not all_results:
        return f"Search returned no results for: {topic}"

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

    # Cap at 15 results
    deduped = deduped[:15]

    # Stage 2: Fetch page bodies for the top results. One shared session
    # reuses keep-alive connections (and the TLS handshake) across URLs.
    fetch_session = _proxied_session()
    try:
        async def _limited_fetch(r: dict) -> str:
            url = r.get("url", "")
            if not url:
                return ""
            async with semaphore:
                return await _fetch_url(url, session=fetch_session)

        fetch_tasks = [_limited_fetch(r) for r in deduped[:8]]
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
    for i, r in enumerate(deduped[:8]):
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
    result = _self_contained_search_result(deduped, fetched_contents)
    logger.info(
        "WebSearch evidence result generated (%d chars, %d sources)",
        len(result),
        len(deduped),
    )
    return result


async def _deep_search_deepseek(topic: str) -> str:
    """Native DeepSeek Responses-API web search (Windows only)."""
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


async def _deep_search_impl(
    topic: str,
    *,
    db_path: str = "",
    session_id: str = "",
    round_id: str = "",
) -> str:
    """Search via native DeepSeek on Windows, falling back to SimpleXNG."""
    del db_path, session_id, round_id
    logger.info(
        "Web search using native DeepSeek enabled=%s (platform=%s)",
        _NATIVE_DEEPSEEK_SEARCH_ENABLED,
        sys.platform,
    )
    if _NATIVE_DEEPSEEK_SEARCH_ENABLED:
        try:
            return await _deep_search_deepseek(topic)
        except DeepSeekWebSearchError as exc:
            logger.warning(
                "Native DeepSeek search unavailable, falling back to SimpleXNG: %s",
                exc,
            )
    async with trace_span(
        "search_stage",
        "simplexng_pipeline",
        attributes={"backend": "simplexng", "deepseek_enabled": _NATIVE_DEEPSEEK_SEARCH_ENABLED},
    ):
        return await _deep_search_simplexng(topic)


async def deep_search(
    topic: str,
    *,
    db_path: str = "",
    session_id: str = "",
    round_id: str = "",
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
        )
        search_span.set_attribute("answer_chars", len(result))
        return result
