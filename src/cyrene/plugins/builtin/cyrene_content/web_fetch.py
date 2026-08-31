"""Tool implementation for WebFetch."""

from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Any

from cyrene.core.plugin import PluginContext
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from .definitions import get_native_tool_def
from cyrene.model.messages import truncate

TOOL_NAME = 'WebFetch'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


class _ReadableHTMLExtractor(HTMLParser):
    """Extract visible, reasonably structured text from an HTML document."""

    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
    _HIDDEN_TAGS = {"script", "style", "noscript", "template", "svg"}

    def __init__(self, base_url: str = "", *, max_links: int = 100) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._hidden_depth = 0
        self._base_url = str(base_url or "")
        self._max_links = max(0, int(max_links))
        self._links_emitted = 0
        self._seen_links: set[str] = set()
        self._anchor: dict[str, Any] | None = None

    def _newline(self) -> None:
        if self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            self._hidden_depth += 1
            return
        if not self._hidden_depth and tag == "a":
            href = next((value for name, value in attrs if name.lower() == "href"), None)
            self._anchor = {"href": str(href or "").strip(), "start": len(self._parts)}
        if not self._hidden_depth and tag in self._BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in self._HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
            return
        if not self._hidden_depth and tag == "a" and self._anchor is not None:
            anchor = self._anchor
            self._anchor = None
            url = self._normalize_link(str(anchor["href"]))
            label = "".join(self._parts[int(anchor["start"]):]).strip()
            if (
                url
                and url not in self._seen_links
                and self._links_emitted < self._max_links
                and label.rstrip("/") != url.rstrip("/")
            ):
                self._parts.append(f" ({url})")
                self._seen_links.add(url)
                self._links_emitted += 1
        if not self._hidden_depth and tag in self._BLOCK_TAGS:
            self._newline()

    def _normalize_link(self, href: str) -> str:
        if not href or href.startswith("#"):
            return ""
        resolved, _fragment = urldefrag(urljoin(self._base_url, href))
        parsed = urlparse(resolved)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        return resolved

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._parts and self._parts[-1] != "\n" and not self._parts[-1].endswith(" "):
            self._parts.append(" ")
        self._parts.append(text)

    def text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _is_html_response(content_type: str, body: str) -> bool:
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if media_type in {"text/html", "application/xhtml+xml"}:
        return True
    if media_type and media_type not in {"text/plain", "application/octet-stream"}:
        return False
    prefix = str(body or "").lstrip()[:512].lower()
    return prefix.startswith("<!doctype html") or prefix.startswith("<html")


def _extract_response_text(
    body: str,
    content_type: str = "",
    base_url: str = "",
    *,
    max_links: int = 100,
) -> str:
    if not _is_html_response(content_type, body):
        return body
    parser = _ReadableHTMLExtractor(base_url, max_links=max_links)
    parser.feed(body)
    parser.close()
    return parser.text()


async def _tool_webfetch(args: dict[str, Any], context: PluginContext) -> str:
    url = str(args["url"])
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    return truncate(_extract_response_text(response.text, content_type, str(response.url)))


handler = _tool_webfetch

__all__ = [
    "TOOL_NAME", "TOOL_DEF", "handler", "_tool_webfetch",
    "_extract_response_text", "_is_html_response",
]
