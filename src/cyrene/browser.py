"""Browser automation shared by Electron and optional Playwright runtimes.

Electron desktop uses its embedded Chromium through a local authenticated RPC
bridge, so the visible native tab and agent actions share one persistent profile.
Non-Electron runs use ``httpx`` for basic fetching and can opt into Playwright for
a persistent browser context, WebSocket screencast, and headed login takeover.

Tools exposed to the agent (see ``tools.py``):
  - ``browser_navigate`` — open a page in the shared session, return readable text
  - ``browser_snapshot`` — inspect visible elements with refs and boxes
  - ``browser_screenshot`` — screenshot the current page or a provided URL
  - ``browser_click`` / ``browser_click_ref`` / ``browser_click_at``
  - ``browser_type`` / ``browser_type_ref``
  - ``browser_wait`` / ``browser_network_log``

The ``_BrowserSession`` implementation is only used outside Electron. If optional
Playwright cannot be loaded, browser automation degrades to text-only HTTP fetching
where possible.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import ipaddress
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from cyrene.runtime.paths import TEMP_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),    # loopback
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("10.0.0.0/8"),      # private class A
    ipaddress.ip_network("172.16.0.0/12"),   # private class B
    ipaddress.ip_network("192.168.0.0/16"),  # private class C
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata (169.254.169.254)
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),        # IPv6 unique local
    ipaddress.ip_network("0.0.0.0/8"),       # "this" network
    ipaddress.ip_network("100.64.0.0/10"),   # shared address space (RFC 6598)
    ipaddress.ip_network("240.0.0.0/4"),     # reserved
)


class SSRFBlockedError(ValueError):
    """Raised when a URL is rejected by the SSRF protection policy."""


def _normalize_http_url(url: str) -> str:
    """Normalize user-entered browser URLs before validation/navigation."""
    value = str(url or "").strip()
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme:
        return value
    if value.startswith("//"):
        return "https:" + value
    return "https://" + value


_TEMPORARILY_UNAVAILABLE_MARKERS = (
    "暂时无法浏览", "暂时无法访问", "内容暂不可用",
    "temporarily unavailable", "content is not available",
)
_ALTERNATE_ACCESS_MARKERS = (
    "请打开app", "扫码查看", "登录后查看",
    "open in the app", "scan", "sign in", "log in to",
)


def _browser_page_signal(url: str, title: str = "", text: str = "") -> dict[str, Any]:
    """Classify a conservative, site-independent temporary access gate."""
    haystack = re.sub(r"\s+", "", f"{title}\n{text}").lower()
    unavailable = tuple(re.sub(r"\s+", "", marker).lower() for marker in _TEMPORARILY_UNAVAILABLE_MARKERS)
    alternate = tuple(re.sub(r"\s+", "", marker).lower() for marker in _ALTERNATE_ACCESS_MARKERS)
    if any(marker in haystack for marker in unavailable) and any(marker in haystack for marker in alternate):
        return {
            "kind": "access_gate",
            "requires_user_takeover": False,
            "retry_allowed": True,
            "max_retries": 1,
            "cooldown_ms": 10_000,
            "message": "页面内容暂不可用；允许一次有冷却时间的恢复尝试，仍失败时请求用户接管。",
        }
    return {
        "kind": "normal",
        "requires_user_takeover": False,
        "retry_allowed": True,
        "message": "",
    }


def _page_text_preview(text: str, max_chars: int = 2000) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:max_chars]


def _normalize_browser_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize Electron's camelCase observation fields for Python tools."""
    if isinstance(result, dict) and "page_signal" not in result and "pageSignal" in result:
        result["page_signal"] = result.pop("pageSignal")
    if isinstance(result, dict) and "snapshot_token" not in result and "snapshotToken" in result:
        result["snapshot_token"] = result.pop("snapshotToken")
    for camel, snake in (
        ("openedNewTab", "opened_new_tab"),
        ("activeTabId", "active_tab_id"),
        ("sourceTabId", "source_tab_id"),
        ("sourceUrl", "source_url"),
    ):
        if isinstance(result, dict) and snake not in result and camel in result:
            result[snake] = result.pop(camel)
    return result


def _check_url(url: str) -> None:
    """Validate *url* before any fetch or navigation.

    Raises SSRFBlockedError for non-http(s) schemes and destinations that
    resolve to loopback, private, link-local, or otherwise reserved IP ranges
    (including the cloud-metadata endpoint 169.254.169.254).
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise SSRFBlockedError(f"Malformed URL: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise SSRFBlockedError(
            f"Blocked scheme {parsed.scheme!r} — only http/https are allowed"
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError("URL has no hostname")

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"Cannot resolve {hostname!r}: {exc}") from exc

    for (_family, _type, _proto, _canonname, sockaddr) in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise SSRFBlockedError(
                    f"Navigation to {hostname!r} ({ip}) is blocked: matches reserved range {net}"
                )


async def _ssrf_redirect_hook(response: httpx.Response) -> None:
    """httpx response event hook — rejects redirects to blocked destinations."""
    if 300 <= response.status_code < 400:
        location = response.headers.get("location", "")
        if location:
            # DNS resolution in _check_url is synchronous on every supported
            # platform. Keep redirects from briefly blocking the shared agent
            # event loop.
            await asyncio.to_thread(
                _check_url,
                urljoin(str(response.url), location),
            )


_PLAYWRIGHT_AVAILABLE: bool | None = None

# Screencast tuning.
# Defaults below; each is overridable through the config store (env keys).
_DEFAULT_FRAME_QUALITY = 60
_DEFAULT_WIDTH = 1280
_DEFAULT_HEIGHT = 800
_DEFAULT_BROWSER_VERSION = "147.0.0.0"
_CHROME_VERSION_CACHE: str | None = None


def _cfg(key: str, default: str) -> str:
    try:
        from cyrene.runtime.config_store import get_env
        return str(get_env(key, default) or default)
    except Exception:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(_cfg(key, str(default)))
    except (ValueError, TypeError):
        return default


def _headless_default() -> bool:
    """Normal (non-takeover) headed/headless mode. Default headless; the takeover
    flow temporarily restarts headed regardless."""
    return _cfg("CYRENE_BROWSER_HEADLESS", "1").strip().lower() not in ("0", "false", "no", "off")


def _frame_quality() -> int:
    return _cfg_int("CYRENE_BROWSER_SCREENCAST_QUALITY", _DEFAULT_FRAME_QUALITY)


def _viewport() -> dict[str, int]:
    return {
        "width": _cfg_int("CYRENE_BROWSER_WIDTH", _DEFAULT_WIDTH),
        "height": _cfg_int("CYRENE_BROWSER_HEIGHT", _DEFAULT_HEIGHT),
    }


def _ua_platform() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "Windows NT 10.0; Win64; x64"
    if system == "linux":
        return "X11; Linux x86_64"
    return "Macintosh; Intel Mac OS X 10_15_7"


def _browser_user_agent(chromium_version: str | None = None) -> str:
    """Return a desktop Chrome UA instead of Playwright's HeadlessChrome UA.

    Some sites route ``HeadlessChrome/...`` to generic "upgrade your browser"
    pages even when the bundled Chromium version is current.
    """
    override = _cfg("CYRENE_BROWSER_USER_AGENT", "").strip()
    if override:
        return override
    version = (chromium_version or "").strip() or _DEFAULT_BROWSER_VERSION
    return (
        f"Mozilla/5.0 ({_ua_platform()}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{version} Safari/537.36"
    )


def _browser_locale() -> str:
    return _cfg("CYRENE_BROWSER_LOCALE", "zh-CN").strip() or "zh-CN"


def _browser_accept_language() -> str:
    return _cfg("CYRENE_BROWSER_ACCEPT_LANGUAGE", "zh-CN,zh;q=0.9,en;q=0.8").strip() or "zh-CN,zh;q=0.9,en;q=0.8"


def browser_runtime_unavailable_message(exc: Exception | str | None = None) -> str:
    """Return a concise browser-runtime error without install commands."""
    base = "Cyrene browser runtime is unavailable."
    if exc is None:
        return base
    text = str(exc).strip()
    if not text:
        return base
    cleaned: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if "playwright install" in lower or "pip install" in lower or "please run" in lower:
            continue
        if line.strip().startswith(("╔", "║", "╚")):
            continue
        cleaned.append(line)
    detail = " ".join(part.strip() for part in cleaned if part.strip())
    detail = re.sub(r"\s+", " ", detail).strip()
    if not detail:
        return base
    if len(detail) > 500:
        detail = detail[:500].rstrip() + "..."
    return f"{base} {detail}"


def electron_browser_available() -> bool:
    """Return True when the Electron host exposed its browser RPC server."""
    return bool(os.environ.get("CYRENE_ELECTRON_RPC_PORT") and os.environ.get("CYRENE_ELECTRON_RPC_TOKEN"))


def _electron_browser_failure(exc: Exception | str, **extra: Any) -> dict[str, Any]:
    """Return an Electron browser error without switching to another runtime."""
    detail = str(exc).strip() or "unknown error"
    return {
        "ok": False,
        "error": f"Electron desktop browser is unavailable: {detail}",
        **extra,
    }


async def _electron_browser_rpc(
    method: str,
    args: dict[str, Any] | None = None,
    *,
    timeout: float = 45.0,
    session_id: str | None = None,
    round_id: str | None = None,
) -> dict[str, Any]:
    port = str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
    token = str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
    if not port or not token:
        raise RuntimeError("Electron browser RPC is unavailable.")
    url = f"http://127.0.0.1:{port}/browser/rpc"
    try:
        from cyrene.agent.context import current_run_context

        run_context = current_run_context()
        current_session_id = run_context.session_id.strip()
        current_round_id = run_context.round_id.strip()
    except Exception:
        current_session_id = ""
        current_round_id = ""
    rpc_session_id = current_session_id if session_id is None else str(session_id or "").strip()
    rpc_round_id = current_round_id if round_id is None else str(round_id or "").strip()
    payload = {
        "method": method,
        "sessionId": rpc_session_id,
        "roundId": rpc_round_id,
        "args": args or {},
    }
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(
            url,
            headers={"X-Cyrene-Token": token, "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Electron browser RPC returned a non-object response.")
    return data


async def close_electron_browser_session(session_id: str) -> dict[str, Any]:
    """Close the Electron tabs owned by one conversation without clearing login data."""
    if not electron_browser_available():
        return {"ok": True, "sessionId": str(session_id or "").strip(), "closed": False}
    return await _electron_browser_rpc(
        "closeSession",
        {},
        timeout=10.0,
        session_id=str(session_id or "").strip(),
        round_id="",
    )


async def clear_browser_data() -> dict[str, Any]:
    """Close both browser implementations and erase their shared login state."""
    from cyrene.config import DATA_DIR

    electron_cleared = False
    if electron_browser_available():
        result = await _electron_browser_rpc(
            "clearStorage",
            {},
            timeout=20.0,
            session_id="",
            round_id="",
        )
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("error") or "Electron browser data reset failed"))
        electron_cleared = True

    await close_session()
    await asyncio.to_thread(shutil.rmtree, DATA_DIR / "browser_profile", True)
    return {"ok": True, "electron": electron_cleared, "playwright": True}


async def finish_electron_browser_round(session_id: str, round_id: str) -> dict[str, Any]:
    """Finalize tabs created by one agent run while preserving one reusable tab."""
    normalized_session_id = str(session_id or "").strip()
    normalized_round_id = str(round_id or "").strip()
    if not electron_browser_available() or not normalized_round_id:
        return {
            "ok": True,
            "sessionId": normalized_session_id,
            "roundId": normalized_round_id,
            "closedTabIds": [],
        }
    return await _electron_browser_rpc(
        "finishRound",
        {},
        timeout=10.0,
        session_id=normalized_session_id,
        round_id=normalized_round_id,
    )


async def electron_current_url() -> str:
    """Best-effort current URL for the Electron-hosted browser tab."""
    if not electron_browser_available():
        return ""
    state = await _electron_browser_rpc("state", {}, timeout=10.0)
    active = state.get("activeTab") if isinstance(state, dict) else None
    if isinstance(active, dict):
        return str(active.get("url") or "")
    return ""


def validate_screenshot_file(path: str) -> dict[str, int | str]:
    """Require a non-empty, decodable PNG before exposing a screenshot path."""
    screenshot_path = Path(path)
    if not screenshot_path.is_file():
        raise ValueError("screenshot file does not exist")
    size = screenshot_path.stat().st_size
    if size <= 0:
        raise ValueError("screenshot file is empty")
    try:
        image_module = importlib.import_module("PIL.Image")
        with image_module.open(screenshot_path) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            if image_format != "PNG":
                raise ValueError(f"expected PNG format, got {image_format or 'unknown'}")
            if width <= 0 or height <= 0:
                raise ValueError("screenshot has invalid dimensions")
            image.verify()
        with image_module.open(screenshot_path) as decoded:
            decoded.load()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"screenshot PNG cannot be decoded: {exc}") from exc
    return {"format": "PNG", "size": size, "width": width, "height": height}


_BROWSER_INSPECT_JS = r"""
(function(maxArg, textArg) {
  const maxElements = Math.max(1, Math.min(200, Number(maxArg) || 80));
  const textLimit = Math.max(20, Math.min(500, Number(textArg) || 160));
  const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
  // data-cyrene-ref is shared with text-links and visible_link_matches, which
  // number independently. Clear every previous stamp so each snapshot's refs
  // are unique; stale refs on off-viewport elements made click_ref resolve
  // the wrong (document-order-first) element.
  for (const el of document.querySelectorAll('[data-cyrene-ref]')) {
    el.removeAttribute('data-cyrene-ref');
  }
  const candidates = [
    ...Array.from(document.querySelectorAll('input,textarea,select,button,a[href],[contenteditable="true"],[role="textbox"],[role="searchbox"],[role="combobox"],[role="button"],[role="link"],[tabindex]')),
    ...Array.from(document.querySelectorAll('summary,label,[role],img,video,section,article,div,span')),
  ];
  const seen = new Set();
  const out = [];
  const clean = (value, limit = textLimit) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const cssEscape = (value) => {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  };
  const roleOf = (el, tag) => {
    const explicit = clean(el.getAttribute('role'), 60);
    if (explicit) return explicit;
    if (tag === 'a' && el.href) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const type = String(el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
      if (type === 'file') return 'file-upload';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'img') return 'img';
    if (el.isContentEditable) return 'textbox';
    return '';
  };
  const selectorFor = (el, tag, index) => {
    const id = clean(el.id, 120);
    if (id) return '#' + cssEscape(id);
    const testId = clean(el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy'), 120);
    if (testId) return tag + '[data-testid="' + testId.replace(/"/g, '\\"') + '"]';
    const href = clean(el.getAttribute('href'), 180);
    if (tag === 'a' && href) return 'a[href="' + href.replace(/"/g, '\\"') + '"]';
    return '[data-cyrene-ref="' + index + '"]';
  };
  const interactiveRect = (el) => {
    if (el.hidden || el.closest('[hidden],[inert],[aria-hidden="true"]')) return null;
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility({
      checkOpacity: true,
      checkVisibilityCSS: true,
      contentVisibilityAuto: true,
    })) return null;
    for (let node = el; node instanceof Element; node = node.parentElement) {
      if (node.hidden || node.hasAttribute('inert')
          || String(node.getAttribute('aria-hidden') || '').toLowerCase() === 'true') return null;
      const style = window.getComputedStyle(node);
      if (!style || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse'
          || style.contentVisibility === 'hidden' || Number(style.opacity) <= 0.001) return null;
    }
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    const left = Math.max(0, rect.left);
    const top = Math.max(0, rect.top);
    const right = Math.min(viewportW, rect.right);
    const bottom = Math.min(viewportH, rect.bottom);
    if (right <= left || bottom <= top) return null;
    const insetX = Math.min(1, (right - left) / 4);
    const insetY = Math.min(1, (bottom - top) / 4);
    const points = [
      [(left + right) / 2, (top + bottom) / 2],
      [left + insetX, top + insetY],
      [right - insetX, top + insetY],
      [left + insetX, bottom - insetY],
      [right - insetX, bottom - insetY],
    ];
    const hittable = points.some(([x, y]) => {
      const hits = typeof document.elementsFromPoint === 'function'
        ? document.elementsFromPoint(x, y)
        : [document.elementFromPoint(x, y)].filter(Boolean);
      return hits.some((hit) => hit === el || el.contains(hit));
    });
    return hittable ? rect : null;
  };
  for (const el of candidates) {
    if (!(el instanceof Element) || seen.has(el)) continue;
    seen.add(el);
    const rect = interactiveRect(el);
    if (!rect) continue;
    const tag = String(el.tagName || '').toLowerCase();
    const role = roleOf(el, tag);
    const disabled = el.matches(':disabled') || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
    const style = window.getComputedStyle(el);
    const interactive = !disabled && (
      ['a', 'button', 'input', 'textarea', 'select', 'summary'].includes(tag)
      || el.isContentEditable || el.tabIndex >= 0 || typeof el.onclick === 'function'
      || ['button', 'link', 'textbox', 'searchbox', 'combobox', 'checkbox', 'radio', 'switch', 'menuitem', 'tab'].includes(role)
      || (style && style.cursor === 'pointer')
    );
    const inputType = tag === 'input' ? clean(el.getAttribute('type') || 'text', 40).toLowerCase() : '';
    const text = tag === 'input' || tag === 'textarea'
      ? (inputType === 'password' ? '' : clean(el.value))
      : clean(el.innerText || el.textContent || el.getAttribute('value') || el.getAttribute('title') || el.getAttribute('alt'));
    const ariaLabel = clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('alt'));
    const placeholder = clean(el.getAttribute('placeholder'));
    const href = el.href ? String(el.href) : clean(el.getAttribute('href'), 300);
    const src = el.currentSrc || el.src || clean(el.getAttribute('src'), 300);
    const interesting = role || href || placeholder || ariaLabel || tag === 'img' || tag === 'input' || tag === 'textarea' || tag === 'select' || text.length >= 2;
    if (!interesting) continue;
    const ref = 'e' + (out.length + 1);
    el.setAttribute('data-cyrene-ref', String(out.length + 1));
    out.push({
      ref,
      tag,
      role,
      visible: true,
      interactive,
      disabled,
      inputType,
      accept: tag === 'input' ? clean(el.getAttribute('accept'), 240) : '',
      multiple: tag === 'input' && el.hasAttribute('multiple'),
      text,
      ariaLabel,
      placeholder,
      href,
      src: tag === 'img' ? src : '',
      alt: tag === 'img' ? clean(el.getAttribute('alt')) : '',
      selector: selectorFor(el, tag, out.length + 1),
      rect: { x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) },
    });
    if (out.length >= maxElements) break;
  }
  return {
    ok: true,
    url: location.href,
    title: document.title || '',
    text: clean(Array.from(new Set(out.map((item) => item.text).filter(Boolean))).join(' '), 2000),
    viewport: { width: viewportW, height: viewportH, scrollX: window.scrollX || 0, scrollY: window.scrollY || 0 },
    elements: out,
  };
})
"""


_BROWSER_FIND_JS = r"""
(function(modeArg, valueArg, exactArg, visibleOnlyArg) {
  const mode = String(modeArg || 'selector');
  const value = String(valueArg || '');
  const exact = exactArg === true;
  const visibleOnly = visibleOnlyArg !== false;
  const norm = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return !!r && r.width > 0 && r.height > 0;
  };
  let el = null;
  if (mode === 'ref') {
    const n = value.replace(/^e/i, '');
    el = document.querySelector('[data-cyrene-ref="' + n.replace(/"/g, '\\"') + '"]');
  } else if (mode === 'text') {
    const needle = norm(value).toLowerCase();
    const nodes = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role],[tabindex],label,summary,[contenteditable="true"],div,span,section,article'));
    el = nodes.find((node) => {
      if (visibleOnly && !isVisible(node)) return false;
      const hay = norm(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || node.getAttribute('placeholder') || node.getAttribute('value')).toLowerCase();
      return exact ? hay === needle : hay.includes(needle);
    }) || null;
  } else {
    el = document.querySelector(value);
  }
  if (!el) return { ok: false, error: 'nf' };
  if (visibleOnly && !isVisible(el)) return { ok: false, error: 'not visible' };
  el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  if (!r || r.width <= 0 || r.height <= 0) return { ok: false, error: 'not visible' };
  return {
    ok: true,
    x: Math.round(r.left + r.width / 2),
    y: Math.round(r.top + r.height / 2),
    box: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
    tag: String(el.tagName || '').toLowerCase(),
    inputType: String(el.getAttribute && el.getAttribute('type') || '').toLowerCase(),
    accept: String(el.getAttribute && el.getAttribute('accept') || ''),
    multiple: !!(el.hasAttribute && el.hasAttribute('multiple')),
  };
})
"""


_BROWSER_TEXT_LINKS_JS = r"""
(function(maxArg, textArg) {
  const maxLinks = Math.max(1, Math.min(200, Number(maxArg) || 120));
  const textLimit = Math.max(20, Math.min(500, Number(textArg) || 200));
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, textLimit);
  for (const el of document.querySelectorAll('[data-cyrene-ref]')) {
    el.removeAttribute('data-cyrene-ref');
  }
  const seen = new Set();
  const links = [];
  for (const el of Array.from(document.querySelectorAll('a[href]'))) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
    const imageAlt = Array.from(el.querySelectorAll('img[alt]')).map((img) => img.getAttribute('alt') || '').join(' ');
    const text = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || imageAlt);
    if (!text) continue;
    let url = '';
    try { url = new URL(el.getAttribute('href') || '', location.href).href; } catch (_) { continue; }
    if (!/^https?:/i.test(url)) continue;
    const key = text + '\n' + url;
    if (seen.has(key)) continue;
    seen.add(key);
    const ref = 'e' + (links.length + 1);
    el.setAttribute('data-cyrene-ref', String(links.length + 1));
    links.push({ ref, text, url });
    if (links.length >= maxLinks) break;
  }
  return links;
})
"""


async def _emit_electron_frame(action: str, result: dict[str, Any], *, target: str | None = None, box: Any = None) -> None:
    """Publish the same lightweight browser_frame metadata for Electron tabs."""
    try:
        from cyrene.observability import debug
        from cyrene.agent.context import current_run_context

        run_context = current_run_context()
        norm_box = None
        if isinstance(box, dict) and box:
            norm_box = {
                "x": box.get("x", 0),
                "y": box.get("y", 0),
                "w": box.get("w", box.get("width", 0)),
                "h": box.get("h", box.get("height", 0)),
            }
        await debug.publish_event({
            "type": "browser_frame",
            "session_id": run_context.session_id,
            "round_id": run_context.round_id,
            "url": str(result.get("url") or ""),
            "title": str(result.get("title") or ""),
            "action": action,
            "target": target,
            "box": norm_box,
            "ts": time.time(),
        })
    except Exception:
        logger.debug("electron browser_frame emit failed", exc_info=True)


async def _detect_chromium_version(chromium: Any) -> str:
    """Best-effort Chromium version lookup from the Playwright executable."""
    global _CHROME_VERSION_CACHE
    if _CHROME_VERSION_CACHE:
        return _CHROME_VERSION_CACHE
    executable = str(getattr(chromium, "executable_path", "") or "").strip()
    if not executable:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        text = (stdout or b"").decode("utf-8", errors="ignore") + " " + (stderr or b"").decode("utf-8", errors="ignore")
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", text)
        if match:
            _CHROME_VERSION_CACHE = match.group(1)
            return _CHROME_VERSION_CACHE
    except Exception:
        logger.debug("Chromium version detection failed", exc_info=True)
    return ""


# ---------------------------------------------------------------------------
# HTML → text extraction (stdlib, no external deps)
# ---------------------------------------------------------------------------

class _HTMLToText(HTMLParser):
    """Convert HTML to readable plain text."""

    def __init__(self) -> None:
        super().__init__()
        self._result: list[str] = []
        self._skip = False
        self._block_tags = {"p", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section", "blockquote", "pre"}

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True
        if tag in self._block_tags and self._result and not self._result[-1].endswith("\n"):
            self._result.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in self._block_tags and self._result and not self._result[-1].endswith("\n"):
            self._result.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self._result.append(text)

    def text(self) -> str:
        raw = "".join(self._result)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _html_to_text(html: str, max_chars: int = 8000) -> str:
    parser = _HTMLToText()
    parser.feed(html)
    text = parser.text()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[truncated]"
    return text


class _HTMLLinkExtractor(HTMLParser):
    """Extract readable anchor text and resolved HTTP(S) destinations."""

    def __init__(self, base_url: str, *, max_links: int = 120, text_limit: int = 200) -> None:
        super().__init__()
        self._base_url = base_url
        self._max_links = max(1, max_links)
        self._text_limit = max(20, text_limit)
        self._anchor: dict[str, Any] | None = None
        self._skip_depth = 0
        self._seen: set[tuple[str, str]] = set()
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
            return
        if tag == "a" and self._anchor is None:
            self._anchor = {
                "href": attrs_map.get("href", ""),
                "label": attrs_map.get("aria-label", "") or attrs_map.get("title", ""),
                "chunks": [],
                "image_alt": "",
            }
        elif tag == "img" and self._anchor is not None:
            self._anchor["image_alt"] = attrs_map.get("alt", "")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag != "a" or self._anchor is None:
            return
        href = str(self._anchor.get("href") or "").strip()
        inner_text = " ".join(self._anchor.get("chunks") or [])
        text = re.sub(
            r"\s+",
            " ",
            inner_text or str(self._anchor.get("label") or "") or str(self._anchor.get("image_alt") or ""),
        ).strip()
        self._anchor = None
        if not href or not text or len(self.links) >= self._max_links:
            return
        resolved = urljoin(self._base_url, href)
        try:
            parsed = urlparse(resolved)
        except Exception:
            return
        if parsed.scheme not in ("http", "https"):
            return
        text = text[: self._text_limit]
        key = (text, resolved)
        if key in self._seen:
            return
        self._seen.add(key)
        self.links.append({"text": text, "url": resolved})

    def handle_data(self, data: str) -> None:
        if self._anchor is not None and self._skip_depth == 0:
            self._anchor["chunks"].append(data)


def _html_links(html: str, base_url: str, *, max_links: int = 120) -> list[dict[str, str]]:
    parser = _HTMLLinkExtractor(base_url, max_links=max_links)
    parser.feed(html)
    return parser.links


# ---------------------------------------------------------------------------
# Persistent browser session
# ---------------------------------------------------------------------------


class _BrowserSession:
    """A single, lazily-launched persistent Playwright context shared by all browser
    tools. One context, one page (for now); access is serialized by ``_action_lock``.

    ``_mode_lock`` guards mode switches (M3 takeover restart) because a persistent
    ``user_data_dir`` may only back one Chromium instance at a time.
    """

    def __init__(self) -> None:
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self._mode: str = "headless"
        self._takeover_active = False
        self._takeover_session_id = ""
        self._closing_deliberately = False
        # User live-control (S3): the panel forwards mouse/keyboard over /ws/browser
        # and we inject it into the (headless) page via CDP. ``_user_window_open``
        # tracks a user-initiated native window (escape hatch for sites that block
        # headless) so closing it auto-returns to headless.
        self._user_control = False
        self._user_window_open = False
        # Set == agent is clear to act; cleared while the user holds live control,
        # so agent browser actions yield instead of fighting the user for the page.
        self._control_released = asyncio.Event()
        self._control_released.set()
        self._action_lock = asyncio.Lock()
        self._mode_lock = asyncio.Lock()
        self._last_agent_click_completed_at = 0.0
        self._latest_snapshot: dict[str, Any] | None = None
        # Screencast (M2): live JPEG frames fanned out to WebSocket subscribers.
        self._cdp: Any = None
        self._screencasting = False
        self._screencast_lock = asyncio.Lock()
        self._frame_subs: set[asyncio.Queue] = set()

    @property
    def profile_dir(self) -> str:
        from cyrene.config import DATA_DIR

        d = DATA_DIR / "browser_profile"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    async def _ensure_started(self, *, headless: bool | None = None) -> None:
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        if headless is None:
            headless = _headless_default()
        self._pw = await async_playwright().start()
        self._context = await self._launch_persistent_context(headless=headless)
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self._mode = "headless" if headless else "headed"

    async def _launch_persistent_context(self, *, headless: bool) -> Any:
        chromium_version = await _detect_chromium_version(self._pw.chromium)
        return await self._pw.chromium.launch_persistent_context(
            self.profile_dir,
            headless=headless,
            viewport=_viewport(),
            user_agent=_browser_user_agent(chromium_version),
            locale=_browser_locale(),
            extra_http_headers={"Accept-Language": _browser_accept_language()},
        )

    async def page(self) -> Any:
        if self._page is not None:
            return self._page
        await self._ensure_started()
        return self._page

    async def current_url(self) -> str:
        return self._safe_url()

    def _safe_url(self) -> str:
        """Read the current page URL without raising if the page/context is gone."""
        if self._page is None:
            return ""
        try:
            return self._page.url
        except Exception:
            return ""

    def _invalidate_snapshot(self) -> None:
        self._latest_snapshot = None

    # -- Login takeover (M3): headless <-> headed restart -------------------
    #
    # A persistent ``user_data_dir`` may only back one Chromium instance at a
    # time, so switching headless<->headed means fully closing the current
    # context before relaunching against the same profile. Cookies/localStorage
    # live on disk, so the agent stays authenticated after the user logs in.

    async def _relaunch(self, *, headless: bool, url: str = "") -> None:
        await self._teardown_screencast()
        if self._context is not None:
            self._closing_deliberately = True
            try:
                await self._context.close()
            except Exception:
                pass
            finally:
                self._closing_deliberately = False
            self._context = None
            self._page = None
        if self._pw is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
        self._context = await self._launch_persistent_context(headless=headless)
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self._mode = "headless" if headless else "headed"
        if url:
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                logger.debug("relaunch goto failed for %s", url, exc_info=True)

    async def switch_to_headed(self, url: str = "") -> None:
        """Bring up a real, visible browser window for the user to log in.

        Screencast is torn down (it resumes in headless after the takeover), so
        the chat panel shows the takeover prompt rather than the login pixels.
        """
        async with self._mode_lock:
            await self._ensure_started()
            target = url or self._safe_url()
            await self._relaunch(headless=False, url=target)
            self._takeover_active = True
            try:
                from cyrene.agent.context import current_session_id
                self._takeover_session_id = current_session_id().strip()
            except Exception:
                self._takeover_session_id = ""
            try:
                self._context.on("close", self._on_headed_close)
            except Exception:
                pass
            try:
                await self._page.bring_to_front()
            except Exception:
                pass
            await self._os_focus()

    def _on_headed_close(self, *_args: Any) -> None:
        """User closed the takeover window manually (vs. our deliberate relaunch)."""
        if self._closing_deliberately:
            return
        if self._user_window_open:
            # User-initiated escape-hatch window: silently return to headless so the
            # in-panel live view keeps working without a dangling pending question.
            self._user_window_open = False
            try:
                asyncio.create_task(self._auto_return_headless())
            except Exception:
                pass
        elif self._takeover_active:
            try:
                asyncio.create_task(self._publish_takeover_cancelled())
            except Exception:
                pass

    async def _auto_return_headless(self) -> None:
        try:
            await self.end_takeover("")
        except Exception:
            logger.debug("auto return-to-headless failed", exc_info=True)
        try:
            from cyrene.observability import debug
            event = {"type": "browser_takeover_cancelled"}
            if self._takeover_session_id:
                event["session_id"] = self._takeover_session_id
            await debug.publish_event(event)
        except Exception:
            pass

    async def _publish_takeover_cancelled(self) -> None:
        self._takeover_active = False
        try:
            from cyrene.observability import debug
            event = {"type": "browser_takeover_cancelled"}
            if self._takeover_session_id:
                event["session_id"] = self._takeover_session_id
            await debug.publish_event(event)
        except Exception:
            pass

    async def end_takeover(self, url: str = "") -> None:
        """Return to headless after the user finished logging in, same profile."""
        async with self._mode_lock:
            # During takeover the user may be redirected from the original login
            # URL to a post-verification page. Preserve that current headed-page
            # URL when returning to the embedded view; use the original URL only
            # as a fallback if the headed page is already gone.
            target = self._safe_url() or url
            await self._relaunch(headless=_headless_default(), url=target)
            self._takeover_active = False
            self._user_window_open = False
            if self._frame_subs:
                async with self._screencast_lock:
                    if not self._screencasting:
                        await self._attach_screencast()

    async def open_user_window(self, url: str = "") -> None:
        """User-initiated native window — escape hatch for sites that block headless
        (e.g. reCAPTCHA). Unlike the agent takeover, no pending question is created;
        the user returns via ``close_user_window`` (or by closing the window)."""
        await self.switch_to_headed(url)
        self._user_window_open = True

    async def close_user_window(self, url: str = "") -> None:
        """Return the user-initiated native window to the in-panel headless view."""
        self._user_window_open = False
        await self.end_takeover(url)

    async def _os_focus(self) -> None:
        """Best-effort: raise the Chromium app to the foreground (macOS only)."""
        import sys
        if sys.platform != "darwin":
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", 'tell application "Chromium" to activate',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass

    async def navigate(self, url: str, *, max_chars: int = 8000) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"url": url, "status": 0, "title": "", "text": "", "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            self._invalidate_snapshot()
            page = await self.page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            final_url = page.url
            # Guard against server-side redirects to blocked destinations.  The
            # browser already made the TCP connection, but we suppress the content
            # so the agent never reads internal service responses.
            try:
                _check_url(final_url)
            except SSRFBlockedError as exc:
                return {"url": final_url, "status": 0, "title": "", "text": "", "error": str(exc)}
            status = response.status if response else 0
            title = await page.title()
            html = await page.content()
            text = _html_to_text(html, max_chars=max_chars)
            try:
                evaluated_links = await page.evaluate(
                    f"([maxArg, textArg]) => ({_BROWSER_TEXT_LINKS_JS})(maxArg, textArg)",
                    [120, 200],
                )
                links = evaluated_links if isinstance(evaluated_links, list) else _html_links(html, final_url)
            except Exception:
                links = _html_links(html, final_url)
            await self._emit_frame("navigate", url=page.url, title=title)
            return {
                "url": page.url,
                "status": status,
                "title": title,
                "text": text,
                "links": links,
                "page_signal": _browser_page_signal(page.url, title, text),
                "error": None,
            }

    async def inspect(self, *, max_elements: int = 80, text_limit: int = 160) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG, "elements": []}
        async with self._action_lock:
            page = await self.page()
            result = await page.evaluate(
                f"([maxArg, textArg]) => ({_BROWSER_INSPECT_JS})(maxArg, textArg)",
                [max_elements, text_limit],
            )
            if isinstance(result, dict):
                snapshot_token = secrets.token_urlsafe(32)
                snapshot_url = str(result.get("url") or page.url)
                self._latest_snapshot = {
                    "token": snapshot_token,
                    "url": snapshot_url,
                    "issued_at": time.monotonic(),
                    "page": page,
                }
                result["snapshot_token"] = snapshot_token
                result["page_signal"] = _browser_page_signal(
                    str(result.get("url") or page.url),
                    str(result.get("title") or await page.title()),
                    str(result.get("text") or ""),
                )
                return result
            return {"ok": False, "url": page.url, "title": await page.title(), "error": "Unable to inspect page.", "elements": []}

    async def navigation_guard(self, target_url: str, reason: str, snapshot_token: str = "") -> dict[str, Any]:
        page = self._page
        if page is None:
            if reason == "ui_unreachable":
                return {
                    "ok": False,
                    "allowed": False,
                    "code": "SNAPSHOT_CREDENTIAL_REQUIRED",
                    "error": "ui_unreachable requires a fresh browser_snapshot credential.",
                }
            return {"ok": True, "allowed": True, "targetUrl": target_url}
        current_url = str(page.url or "")
        normalized = await page.evaluate(
            "([target, current]) => ({target: new URL(target, current).href, current: new URL(current).href})",
            [target_url, current_url],
        )
        normalized_target = str(normalized.get("target") or target_url) if isinstance(normalized, dict) else target_url
        normalized_current = str(normalized.get("current") or current_url) if isinstance(normalized, dict) else current_url
        if normalized_target == normalized_current:
            return {
                "ok": False,
                "allowed": False,
                "code": "ALREADY_AT_TARGET",
                "error": "The active browser tab is already at the requested URL; browser_navigate was not executed.",
                "url": normalized_current,
                "tabId": "playwright",
            }
        if reason == "user_exact_url":
            return {"ok": True, "allowed": True, "targetUrl": normalized_target}
        if reason == "ui_unreachable":
            credential = self._latest_snapshot
            valid = bool(
                credential
                and snapshot_token
                and secrets.compare_digest(snapshot_token, str(credential.get("token") or ""))
                and credential.get("page") is page
                and str(credential.get("url") or "") == current_url
                and time.monotonic() - float(credential.get("issued_at") or 0) <= 120
            )
            if not valid:
                return {
                    "ok": False,
                    "allowed": False,
                    "code": "SNAPSHOT_CREDENTIAL_INVALID",
                    "error": "ui_unreachable requires the unexpired token from the latest browser_snapshot of the active page.",
                }
            self._invalidate_snapshot()
            scan = await self.visible_link_matches(normalized_target)
            matches = scan.get("matches") if isinstance(scan.get("matches"), list) else []
            if matches:
                return {
                    "ok": False,
                    "allowed": False,
                    "code": "VISIBLE_LINK_AVAILABLE",
                    "error": "Target URL is available through visible page UI. Use browser_click_ref from a fresh browser_snapshot.",
                    "targetUrl": normalized_target,
                    "matches": matches,
                }
        return {"ok": True, "allowed": True, "targetUrl": normalized_target}

    async def visible_link_matches(self, target_url: str) -> dict[str, Any]:
        """Return rendered anchors whose resolved href equals *target_url*."""
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "error": _USER_CONTROL_MSG, "matches": []}
        async with self._action_lock:
            page = await self.page()
            result = await page.evaluate(
                r"""(target) => {
                    let normalizedTarget = '';
                    try { normalizedTarget = new URL(target, location.href).href; }
                    catch (_) { return {ok: false, error: 'Invalid target URL.', matches: []}; }
                    const clean = (value, limit = 200) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
                    // data-cyrene-ref is a shared namespace with browser_snapshot's
                    // inspect script, which numbers from 1 independently. Allocate
                    // past the current max so the two schemes never collide.
                    let nextRef = 1;
                    for (const el of document.querySelectorAll('[data-cyrene-ref]')) {
                        const n = Number(el.getAttribute('data-cyrene-ref') || 0);
                        if (Number.isInteger(n) && n >= nextRef) nextRef = n + 1;
                    }
                    const matches = [];
                    for (const el of Array.from(document.querySelectorAll('a[href]'))) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
                        if (!rect || rect.width <= 0 || rect.height <= 0) continue;
                        let href = '';
                        try { href = new URL(el.getAttribute('href') || '', location.href).href; } catch (_) { continue; }
                        if (href !== normalizedTarget) continue;
                        const imageAlt = Array.from(el.querySelectorAll('img[alt]')).map((img) => img.getAttribute('alt') || '').join(' ');
                        const text = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || imageAlt);
                        let refNumber = Number(el.getAttribute('data-cyrene-ref') || 0);
                        if (!Number.isInteger(refNumber) || refNumber < 1) {
                            refNumber = nextRef;
                            nextRef += 1;
                            el.setAttribute('data-cyrene-ref', String(refNumber));
                        }
                        matches.push({ref: 'e' + refNumber, text, url: href});
                    }
                    return {ok: true, url: location.href, targetUrl: normalizedTarget, matches};
                }""",
                target_url,
            )
            return result if isinstance(result, dict) else {"ok": False, "error": "Visible-link scan failed.", "matches": []}

    async def _find_target(self, mode: str, value: str, *, exact: bool = False) -> dict[str, Any]:
        page = await self.page()
        result = await page.evaluate(
            f"([modeArg, valueArg, exactArg]) => ({_BROWSER_FIND_JS})(modeArg, valueArg, exactArg, true)",
            [mode, value, exact],
        )
        return result if isinstance(result, dict) else {"ok": False, "error": "not found"}

    async def _semantic_content_state(self, page: Any) -> dict[str, str]:
        try:
            state = await page.evaluate(
                r"""() => {
                    const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const semantic = Array.from(document.querySelectorAll(
                        'h1,h2,[role="heading"],main,article,[role="dialog"]'
                    )).filter((el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    }).map((el) => clean(el.innerText || el.textContent).slice(0, 500))
                      .filter(Boolean).slice(0, 12).join('\n').slice(0, 3000);
                    return {url: location.href, title: document.title, semantic};
                }"""
            )
            if isinstance(state, dict):
                return {key: str(state.get(key) or "") for key in ("url", "title", "semantic")}
        except Exception:
            pass
        return {"url": str(page.url or ""), "title": "", "semantic": ""}

    async def _settle_after_interaction(
        self, page: Any, *, before: dict[str, str] | None = None, timeout_ms: int = 3000
    ) -> None:
        """Wait for semantic content, title, or route changes after a click."""
        if before is not None:
            deadline = time.monotonic() + max(0, timeout_ms) / 1000
            while time.monotonic() < deadline:
                current = await self._semantic_content_state(page)
                if any(current.get(key) != before.get(key) for key in ("url", "title", "semantic")):
                    await asyncio.sleep(0.4)
                    return
                await asyncio.sleep(0.1)
            return
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        await asyncio.sleep(0.4)

    def _click_debounced(self, debounce_seconds: float = 0.8) -> bool:
        return time.monotonic() - self._last_agent_click_completed_at < debounce_seconds

    def _click_debounced_result(self, page: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "url": str(page.url or ""),
            "title": "",
            "error": "Click suppressed: this tab received another agent click too recently.",
            "code": "CLICK_DEBOUNCED",
        }

    async def _interaction_observation(self, page: Any) -> dict[str, Any]:
        try:
            body_text = await page.locator("body").inner_text(timeout=1000)
        except Exception:
            body_text = ""
        title = await page.title()
        preview = _page_text_preview(body_text)
        return {
            "page_signal": _browser_page_signal(page.url, title, preview),
            "text": preview,
        }

    async def _adopt_popup_after_click(self, source_page: Any, pages_before: set[Any]) -> dict[str, Any] | None:
        if self._context is None:
            return None
        new_pages = [page for page in self._context.pages if page not in pages_before and not page.is_closed()]
        if not new_pages:
            return None
        popup = new_pages[-1]
        self._page = popup
        try:
            await popup.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        title = await popup.title()
        observation = await self._interaction_observation(popup)
        return {
            "ok": True,
            "url": popup.url,
            "title": title,
            "tabId": "playwright",
            "active_tab_id": "playwright",
            "opened_new_tab": True,
            "source_tab_id": "playwright-source",
            "source_url": source_page.url,
            **observation,
        }

    async def _mouse_click_target(self, mode: str, value: str, *, exact: bool = False, target_label: str = "") -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            self._invalidate_snapshot()
            page = await self.page()
            if self._click_debounced():
                return self._click_debounced_result(page)
            info = await self._find_target(mode, value, exact=exact)
            if not info.get("ok"):
                return {"ok": False, "url": page.url, "title": await page.title(), "error": "Element " + str(info.get("error") or "not found")}
            before = await self._semantic_content_state(page)
            pages_before = set(self._context.pages) if self._context is not None else {page}
            try:
                await page.mouse.click(float(info.get("x") or 0), float(info.get("y") or 0))
                await self._settle_after_interaction(page, before=before)
            finally:
                self._last_agent_click_completed_at = time.monotonic()
            popup_result = await self._adopt_popup_after_click(page, pages_before)
            if popup_result is not None:
                popup_result["box"] = info.get("box")
                await self._emit_frame("click", target=target_label or value, box=info.get("box"), url=popup_result["url"], title=popup_result["title"])
                return popup_result
            title = await page.title()
            observation = await self._interaction_observation(page)
            await self._emit_frame("click", target=target_label or value, box=info.get("box"), url=page.url, title=title)
            return {"ok": True, "url": page.url, "title": title, "box": info.get("box"), **observation}

    async def click(self, selector: str) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            self._invalidate_snapshot()
            from playwright.async_api import expect

            page = await self.page()
            if self._click_debounced():
                return self._click_debounced_result(page)
            el = page.locator(selector)
            await expect(el).to_be_visible(timeout=5000)
            box = await el.bounding_box()
            before = await self._semantic_content_state(page)
            pages_before = set(self._context.pages) if self._context is not None else {page}
            try:
                await el.click()
                await self._settle_after_interaction(page, before=before)
            finally:
                self._last_agent_click_completed_at = time.monotonic()
            popup_result = await self._adopt_popup_after_click(page, pages_before)
            if popup_result is not None:
                popup_result["box"] = box
                await self._emit_frame("click", target=selector, box=box, url=popup_result["url"], title=popup_result["title"])
                return popup_result
            title = await page.title()
            observation = await self._interaction_observation(page)
            await self._emit_frame("click", target=selector, box=box, url=page.url, title=title)
            return {"ok": True, "url": page.url, "title": title, **observation}

    async def click_ref(self, ref: str) -> dict[str, Any]:
        return await self._mouse_click_target("ref", ref, target_label=ref)

    async def click_text(self, text: str, *, exact: bool = False) -> dict[str, Any]:
        return await self._mouse_click_target("text", text, exact=exact, target_label=text)

    async def click_at(self, x: int, y: int) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            self._invalidate_snapshot()
            page = await self.page()
            if self._click_debounced():
                return self._click_debounced_result(page)
            before = await self._semantic_content_state(page)
            try:
                await page.mouse.click(x, y)
                await self._settle_after_interaction(page, before=before)
            finally:
                self._last_agent_click_completed_at = time.monotonic()
            title = await page.title()
            observation = await self._interaction_observation(page)
            box = {"x": x, "y": y, "w": 1, "h": 1}
            await self._emit_frame("click", target=f"{x},{y}", box=box, url=page.url, title=title)
            return {"ok": True, "url": page.url, "title": title, "box": box, **observation}

    async def type_text(self, selector: str, text: str, *, submit: bool = False) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            self._invalidate_snapshot()
            page = await self.page()
            el = page.locator(selector)
            box = await el.bounding_box()
            await el.fill(text)
            if submit:
                await el.press("Enter")
                await page.wait_for_load_state()
            title = await page.title()
            await self._emit_frame("type", target=selector, box=box, url=page.url, title=title)
            return {"ok": True, "url": page.url, "title": title}

    async def type_ref(self, ref: str, text: str, *, submit: bool = False) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            self._invalidate_snapshot()
            page = await self.page()
            await page.evaluate(
                f"([maxArg, textArg]) => ({_BROWSER_INSPECT_JS})(maxArg, textArg)",
                [120, 160],
            )
            selector = f'[data-cyrene-ref="{str(ref).removeprefix("e").removeprefix("E")}"]'
            el = page.locator(selector)
            box = await el.bounding_box()
            await el.fill(text)
            if submit:
                await el.press("Enter")
                try:
                    await page.wait_for_load_state(timeout=5000)
                except Exception:
                    pass
            title = await page.title()
            await self._emit_frame("type", target=ref, box=box, url=page.url, title=title)
            return {"ok": True, "url": page.url, "title": title}

    async def prepare_file_upload(self, ref: str) -> dict[str, Any]:
        """Resolve a Playwright file input without opening a native picker."""
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "error": _USER_CONTROL_MSG}
        normalized = str(ref or "").strip().removeprefix("e").removeprefix("E")
        if not normalized.isdigit():
            return {"ok": False, "error": "Invalid browser element ref."}
        async with self._action_lock:
            page = await self.page()
            locator = page.locator(f'[data-cyrene-ref="{normalized}"]')
            try:
                details = await locator.evaluate(
                    """el => ({
                        tag: String(el.tagName || '').toLowerCase(),
                        type: String(el.getAttribute('type') || '').toLowerCase(),
                        accept: String(el.getAttribute('accept') || ''),
                        multiple: el.hasAttribute('multiple'),
                        name: String(el.getAttribute('name') || ''),
                        ariaLabel: String(el.getAttribute('aria-label') || ''),
                        uploadId: (() => {
                            let value = String(el.getAttribute('data-cyrene-upload-id') || '');
                            if (!/^[a-zA-Z0-9_-]{16,100}$/.test(value)) {
                                const random = globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function'
                                    ? globalThis.crypto.randomUUID()
                                    : Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
                                value = 'cyrene_' + random;
                                el.setAttribute('data-cyrene-upload-id', value);
                            }
                            return value;
                        })()
                    })"""
                )
            except Exception as exc:
                return {"ok": False, "error": f"Browser file input ref was not found: {exc}"}
            if not isinstance(details, dict) or details.get("tag") != "input" or details.get("type") != "file":
                return {"ok": False, "error": "The browser ref is not a file input."}
            top_url = str(page.url or "")
            upload_id = str(details.get("uploadId") or "")
            target_id = "upload_" + hashlib.sha256(f"{top_url}\n{upload_id}".encode("utf-8")).hexdigest()[:24]
            parsed = urlparse(top_url)
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
            return {
                "ok": True,
                "target": {
                    "id": target_id,
                    "ref": f"e{normalized}",
                    "uploadId": upload_id,
                    "tabId": "playwright",
                    "chooserId": "",
                    "mode": "selectMultiple" if details.get("multiple") else "selectSingle",
                    "multiple": bool(details.get("multiple")),
                    "accept": str(details.get("accept") or ""),
                    "name": str(details.get("name") or ""),
                    "ariaLabel": str(details.get("ariaLabel") or ""),
                    "topUrl": top_url,
                    "frameUrl": top_url,
                    "origin": origin,
                },
            }

    async def set_input_files(self, target: dict[str, Any], file_paths: list[str]) -> dict[str, Any]:
        """Set an already-approved Playwright file input."""
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            page = await self.page()
            if str(page.url or "") != str(target.get("topUrl") or ""):
                return {"ok": False, "error": "The page changed after approval. Upload was cancelled.", "code": "UPLOAD_PAGE_CHANGED"}
            upload_id = str(target.get("uploadId") or "")
            if not upload_id:
                return {"ok": False, "error": "The approved file input is no longer available."}
            locator = page.locator(f'[data-cyrene-upload-id="{upload_id}"]')
            try:
                details = await locator.evaluate(
                    """el => ({
                        tag: String(el.tagName || '').toLowerCase(),
                        type: String(el.getAttribute('type') || '').toLowerCase(),
                        accept: String(el.getAttribute('accept') || ''),
                        multiple: el.hasAttribute('multiple'),
                        name: String(el.getAttribute('name') || ''),
                        ariaLabel: String(el.getAttribute('aria-label') || '')
                    })"""
                )
                if (
                    details.get("tag") != "input"
                    or details.get("type") != "file"
                    or str(details.get("accept") or "") != str(target.get("accept") or "")
                    or bool(details.get("multiple")) != bool(target.get("multiple"))
                    or str(details.get("name") or "") != str(target.get("name") or "")
                    or str(details.get("ariaLabel") or "") != str(target.get("ariaLabel") or "")
                ):
                    return {"ok": False, "error": "The approved file input changed. Upload was cancelled.", "code": "UPLOAD_TARGET_CHANGED"}
                await locator.set_input_files(file_paths)
            except Exception as exc:
                return {"ok": False, "error": f"Failed to set browser file input: {exc}", "code": "SET_INPUT_FILES_FAILED"}
            return {
                "ok": True,
                "target": dict(target),
                "files": [{"name": os.path.basename(item)} for item in file_paths],
                "url": str(page.url or ""),
                "title": await page.title(),
                "tabId": "playwright",
            }

    async def wait_for(self, *, selector: str = "", text: str = "", url_contains: str = "", timeout_ms: int = 5000) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG}
        async with self._action_lock:
            page = await self.page()
            deadline = time.monotonic() + max(0.1, min(30.0, float(timeout_ms or 5000) / 1000.0))
            while time.monotonic() < deadline:
                url_ok = not url_contains or url_contains in page.url
                selector_ok = True
                text_ok = True
                if selector:
                    try:
                        selector_ok = await page.locator(selector).count() > 0
                    except Exception:
                        selector_ok = False
                if text:
                    try:
                        body_text = await page.locator("body").inner_text(timeout=500)
                    except Exception:
                        body_text = ""
                    text_ok = text in body_text
                if url_ok and selector_ok and text_ok:
                    return {"ok": True, "url": page.url, "title": await page.title()}
                await asyncio.sleep(0.15)
            return {"ok": False, "url": page.url, "title": await page.title(), "error": "Timed out waiting for page condition."}

    async def network_log(self, *, max_entries: int = 40) -> dict[str, Any]:
        if not await self._wait_for_control():
            return {"ok": False, "url": self._safe_url(), "title": "", "error": _USER_CONTROL_MSG, "entries": []}
        async with self._action_lock:
            page = await self.page()
            result = await page.evaluate(
                """(maxArg) => {
                    const max = Math.max(1, Math.min(200, Number(maxArg) || 40));
                    const entries = performance.getEntriesByType('resource').slice(-max).map((e) => ({
                        name: String(e.name || ''),
                        type: String(e.initiatorType || ''),
                        durationMs: Math.round(Number(e.duration || 0)),
                        transferSize: Number(e.transferSize || 0),
                    }));
                    return { ok: true, url: location.href, title: document.title || '', entries };
                }""",
                max_entries,
            )
            return result if isinstance(result, dict) else {"ok": False, "url": page.url, "title": await page.title(), "entries": []}

    async def screenshot_path(self, *, full_page: bool = True) -> str:
        page = await self.page()
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", dir=TEMP_DIR, delete=False)
        tmp.close()  # Playwright writes via path; release fd immediately
        try:
            await page.screenshot(path=tmp.name, full_page=full_page)
            validate_screenshot_file(tmp.name)
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
        return tmp.name

    async def _emit_frame(self, action: str, *, target: str | None = None, box: Any = None, url: str = "", title: str = "") -> None:
        """Publish a lightweight ``browser_frame`` SSE event with action metadata.

        Pixel frames stream over ``/ws/browser``. This SSE event intentionally
        avoids screenshots/base64 payloads so browser activity cannot clog the
        shared notification/chat SSE bus.
        """
        try:
            from cyrene.observability import debug
            from cyrene.agent.context import current_run_context

            page = self._page
            if page is None:
                return
            run_context = current_run_context()
            norm_box = None
            if isinstance(box, dict) and box:
                norm_box = {
                    "x": box.get("x", 0),
                    "y": box.get("y", 0),
                    "w": box.get("width", 0),
                    "h": box.get("height", 0),
                }
            await debug.publish_event({
                "type": "browser_frame",
                "session_id": run_context.session_id,
                "round_id": run_context.round_id,
                "url": url or page.url,
                "title": title,
                "action": action,
                "target": target,
                "box": norm_box,
                "ts": time.time(),
            })
        except Exception:
            logger.debug("browser_frame emit failed", exc_info=True)

    # -- Screencast (M2): continuous live frames over WebSocket --------------

    async def start_screencast(self, queue: "asyncio.Queue") -> None:
        """Register *queue* as a frame subscriber; start CDP screencast on demand."""
        async with self._screencast_lock:
            self._frame_subs.add(queue)
            # During a login takeover the live view is intentionally paused, and
            # the page is mid-restart — defer attaching until end_takeover.
            if not self._screencasting and not self._takeover_active:
                await self._attach_screencast()

    async def _attach_screencast(self) -> None:
        """(Re)attach a CDP screencast to the current page. Caller guards concurrency."""
        await self._ensure_started()
        self._cdp = await self._context.new_cdp_session(self._page)
        self._cdp.on("Page.screencastFrame", self._on_screencast_frame)
        vp = _viewport()
        await self._cdp.send("Page.startScreencast", {
            "format": "jpeg",
            "quality": _frame_quality(),
            "maxWidth": vp["width"],
            "maxHeight": vp["height"],
            "everyNthFrame": 1,
        })
        self._screencasting = True

    async def stop_screencast(self, queue: "asyncio.Queue") -> None:
        """Unregister *queue*; tear the CDP screencast down when the last one leaves."""
        async with self._screencast_lock:
            self._frame_subs.discard(queue)
            if self._frame_subs or not self._screencasting:
                return
            await self._teardown_screencast()

    async def _teardown_screencast(self) -> None:
        if self._cdp is not None:
            try:
                await self._cdp.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                await self._cdp.detach()
            except Exception:
                pass
        self._cdp = None
        self._screencasting = False

    def _on_screencast_frame(self, params: dict[str, Any]) -> None:
        """CDP callback (sync): ack the frame and fan it out to subscriber queues.

        Slow consumers simply drop frames (bounded queues) rather than apply
        backpressure to the browser.
        """
        session_id = params.get("sessionId")
        if self._cdp is not None and session_id is not None:
            asyncio.create_task(self._safe_ack(session_id))
        try:
            data = base64.b64decode(str(params.get("data") or ""), validate=False)
        except Exception:
            return
        frame = {
            "data": data,
            "url": self._page.url if self._page is not None else "",
            "content_type": "image/jpeg",
        }
        for queue in list(self._frame_subs):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    async def _safe_ack(self, session_id: str) -> None:
        try:
            if self._cdp is not None:
                await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception:
            pass

    # -- User live-control (S3): mouse/keyboard injection over CDP -----------
    #
    # When the user "takes control" of the live view, the panel forwards input
    # here. CDP ``Input.*`` events drive the headless page directly, so the user
    # operates the same authenticated session the agent uses — no native window.
    # Sites that fingerprint headless still need ``open_user_window`` instead.

    def set_user_control(self, on: bool) -> None:
        self._user_control = bool(on)
        # Pause/resume agent browser actions: cleared blocks them, set lets them run.
        if self._user_control:
            self._control_released.clear()
        else:
            self._control_released.set()

    @property
    def user_control(self) -> bool:
        return self._user_control

    async def _wait_for_control(self, timeout: float = 600.0) -> bool:
        """Block while the user holds live control so an agent action doesn't fight
        them for the page. Returns True when clear to proceed, or False if the user
        still controls after *timeout* (the action should be skipped, not forced)."""
        if not self._user_control:
            return True
        try:
            await asyncio.wait_for(self._control_released.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return not self._user_control

    async def _ensure_input_cdp(self) -> Any:
        """Return a CDP session usable for Input.* events.

        Reuses the screencast session when present (the panel is normally
        streaming while the user controls); otherwise attaches a fresh one.
        """
        if self._cdp is not None:
            return self._cdp
        await self._ensure_started()
        self._cdp = await self._context.new_cdp_session(self._page)
        return self._cdp

    async def dispatch_mouse(
        self,
        *,
        type: str,
        x: float,
        y: float,
        button: str = "none",
        click_count: int = 0,
        delta_x: float = 0.0,
        delta_y: float = 0.0,
        modifiers: int = 0,
    ) -> None:
        """Inject a mouse event. ``x``/``y`` are viewport CSS pixels."""
        if not self._user_control:
            return
        cdp = await self._ensure_input_cdp()
        params: dict[str, Any] = {"type": type, "x": float(x), "y": float(y), "modifiers": int(modifiers)}
        if type in ("mousePressed", "mouseReleased"):
            params["button"] = button or "left"
            params["clickCount"] = click_count or 1
        elif type == "mouseMoved":
            params["button"] = button or "none"
        elif type == "mouseWheel":
            params["button"] = "none"
            params["deltaX"] = float(delta_x)
            params["deltaY"] = float(delta_y)
        await cdp.send("Input.dispatchMouseEvent", params)

    async def dispatch_key(
        self,
        *,
        type: str,
        key: str = "",
        code: str = "",
        text: str = "",
        key_code: int = 0,
        modifiers: int = 0,
    ) -> None:
        """Inject a keyboard event (``type`` is keyDown/keyUp/char)."""
        if not self._user_control:
            return
        cdp = await self._ensure_input_cdp()
        params: dict[str, Any] = {"type": type, "modifiers": int(modifiers)}
        if key:
            params["key"] = key
        if code:
            params["code"] = code
        if key_code:
            params["windowsVirtualKeyCode"] = int(key_code)
            params["nativeVirtualKeyCode"] = int(key_code)
        if text and type in ("keyDown", "char"):
            params["text"] = text
        await cdp.send("Input.dispatchKeyEvent", params)

    async def insert_text(self, text: str) -> None:
        """Insert a finished string (IME composition result / paste) at the caret.

        ``Input.insertText`` commits text directly, which is how CJK/IME input is
        delivered — the per-keystroke ``dispatch_key`` path only carries the Latin
        composition keys, not the composed characters.
        """
        if not self._user_control or not text:
            return
        cdp = await self._ensure_input_cdp()
        await cdp.send("Input.insertText", {"text": text})

    async def close(self) -> None:
        try:
            await self._teardown_screencast()
        except Exception:
            pass
        self._frame_subs.clear()
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._pw = None
        self._user_control = False
        self._user_window_open = False
        self._control_released.set()


# Returned to the agent when it tries a browser action while the user is driving.
_USER_CONTROL_MSG = (
    "用户正在手动操作浏览器（已接管控制权），agent 的浏览器操作已暂停。"
    "请等待用户交还控制权后再继续，不要反复重试。"
)


_session: _BrowserSession | None = None


def _get_session() -> _BrowserSession:
    global _session
    if _session is None:
        _session = _BrowserSession()
    return _session


async def get_session() -> _BrowserSession:
    """Return the started, ready-to-use shared browser session."""
    session = _get_session()
    await session._ensure_started()
    return session


async def close_session() -> None:
    """Shut the shared browser session down (call on app shutdown)."""
    global _session
    if _session is not None:
        await _session.close()
        _session = None


async def end_browser_takeover(url: str = "") -> None:
    """Return the shared session to headless after a login takeover (M3 resume hook)."""
    if electron_browser_available():
        try:
            from cyrene.observability import debug
            from cyrene.agent.context import current_session_id
            event = {"type": "browser_takeover_cancelled"}
            session_id = current_session_id().strip()
            if session_id:
                event["session_id"] = session_id
            await debug.publish_event(event)
        except Exception:
            pass
        return
    session = _get_session()
    if session._context is not None:
        await session.end_takeover(url)
    # Clear the panel's "waiting for login" placeholder so the live view returns.
    try:
        from cyrene.observability import debug
        from cyrene.agent.context import current_session_id
        event = {"type": "browser_takeover_cancelled"}
        session_id = str(current_session_id() or getattr(session, "_takeover_session_id", "") or "").strip()
        if session_id:
            event["session_id"] = session_id
        await debug.publish_event(event)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API (stable signatures consumed by tools.py)
# ---------------------------------------------------------------------------


async def navigate(
    url: str,
    *,
    extract_text: bool = True,
    max_chars: int = 8000,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Open *url* in the shared browser session and return structured page data.

    Electron desktop calls are strict: an RPC failure is returned to the caller
    instead of silently opening a separate Playwright browser/profile. Outside
    Electron, Playwright and then plain ``httpx`` remain available as fallbacks.

    Returns::
        {"url": str, "status": int, "title": str, "text": str, "error": str | None}
    """
    url = _normalize_http_url(url)
    try:
        _check_url(url)
    except SSRFBlockedError as exc:
        return {"url": url, "status": 0, "title": "", "text": "", "error": str(exc)}
    if electron_browser_available():
        try:
            result = _normalize_browser_result(await _electron_browser_rpc(
                "navigate",
                {"url": url, "maxChars": max_chars, "extractText": extract_text},
            ))
            if result.get("ok") is False:
                return {
                    "url": str(result.get("url") or url),
                    "status": int(result.get("status") or 0),
                    "title": str(result.get("title") or ""),
                    "text": str(result.get("text") or ""),
                    "links": result.get("links") if isinstance(result.get("links"), list) else [],
                    "error": str(result.get("error") or "Electron desktop browser navigation failed."),
                }
            await _emit_electron_frame("navigate", result)
            ret = {
                "url": str(result.get("url") or url),
                "status": int(result.get("status") or 0),
                "title": str(result.get("title") or ""),
                "text": str(result.get("text") or ""),
                "links": result.get("links") if isinstance(result.get("links"), list) else [],
                "error": None,
            }
            if isinstance(result.get("page_signal"), dict):
                ret["page_signal"] = result["page_signal"]
            tid = result.get("tabId")
            if tid:
                ret["tabId"] = str(tid)
            return ret
        except Exception as exc:
            logger.warning("Electron browser navigate failed (%s)", exc)
            return {
                "url": url,
                "status": 0,
                "title": "",
                "text": "",
                "links": [],
                "error": _electron_browser_failure(exc)["error"],
            }
    if _ensure_playwright() is not None:
        try:
            session = await get_session()
            return await session.navigate(url, max_chars=max_chars)
        except Exception as exc:
            logger.warning("Playwright navigate failed (%s); falling back to httpx", exc)
    result = await _httpx_navigate(url, extract_text=extract_text, max_chars=max_chars, headers=headers)
    if electron_browser_available():
        await _emit_electron_frame("navigate", result)
    return result


async def _httpx_navigate(
    url: str,
    *,
    extract_text: bool = True,
    max_chars: int = 8000,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"url": url, "status": 0, "title": "", "text": "", "error": None}
    try:
        req_headers = {
            "User-Agent": _browser_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if headers:
            req_headers.update(headers)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            event_hooks={"response": [_ssrf_redirect_hook]},
        ) as client:
            response = await client.get(url, headers=req_headers)
            result["status"] = response.status_code
            result["url"] = str(response.url)
            response.raise_for_status()
            html = response.text

            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if title_match:
                result["title"] = re.sub(r"\s+", " ", title_match.group(1)).strip()

            if extract_text:
                result["text"] = _html_to_text(html, max_chars=max_chars)
            result["links"] = _html_links(html, str(response.url))
            result["page_signal"] = _browser_page_signal(
                str(response.url), str(result.get("title") or ""), str(result.get("text") or "")
            )
    except SSRFBlockedError as exc:
        result["error"] = str(exc)
    except httpx.TimeoutException:
        result["error"] = f"Request timed out: {url}"
    except httpx.HTTPError as exc:
        result["error"] = f"HTTP error: {exc}"
    except Exception as exc:
        result["error"] = f"Failed to fetch {url}: {exc}"
        logger.exception("browser_navigate failed for %s", url)
    return result


async def screenshot(
    url: str = "",
    *,
    full_page: bool = True,
    session_id: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Screenshot *url* or the current shared browser page.

    Returns ``{"ok": True, "path": "/tmp/…png"}`` or ``{"ok": False, "error": "..."}``.
    """
    url = str(url or "").strip()
    if read_only and url:
        return {"ok": False, "error": "Read-only browser screenshots cannot navigate."}
    if url:
        url = _normalize_http_url(url)
        try:
            _check_url(url)
        except SSRFBlockedError as exc:
            return {"ok": False, "error": str(exc)}
    if electron_browser_available():
        try:
            nav = {"ok": True, "title": ""}
            if url:
                nav = await _electron_browser_rpc(
                    "navigate",
                    {"url": url, "maxChars": 0},
                    session_id=session_id,
                )
            if nav.get("ok") is not True:
                return {"ok": False, "error": str(nav.get("error") or "Electron desktop browser navigation failed.")}
            result = await _electron_browser_rpc(
                "screenshot",
                {},
                session_id=session_id,
                round_id="" if read_only else None,
            )
            if result.get("ok") is not True:
                return {"ok": False, "error": str(result.get("error") or "Electron desktop browser screenshot failed.")}
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", dir=TEMP_DIR, delete=False)
            tmp.close()
            try:
                data = base64.b64decode(str(result.get("pngBase64") or ""), validate=True)
                with open(tmp.name, "wb") as fh:
                    fh.write(data)
                validate_screenshot_file(tmp.name)
            except Exception as exc:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
                raise ValueError(f"Browser screenshot validation failed: {exc}") from exc
            await _emit_electron_frame("screenshot", result)
            return {"ok": True, "path": tmp.name, "title": str(result.get("title") or nav.get("title") or "")}
        except Exception as exc:
            logger.warning("Electron screenshot failed (%s)", exc)
            return {"ok": False, "error": str(exc)}
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    try:
        session = await get_session()
        if url:
            await session.navigate(url)
        elif session._page is None:
            return {"ok": False, "error": "No page open. Call browser_navigate first."}
        path = await session.screenshot_path(full_page=full_page)
        validate_screenshot_file(path)
        title = await (await session.page()).title()
        return {"ok": True, "path": path, "title": title}
    except Exception as exc:
        logger.exception("screenshot failed for %s", url)
        return {"ok": False, "error": f"Browser screenshot failed: {exc}"}


async def inspect_page(
    *,
    max_elements: int = 80,
    text_limit: int = 160,
    session_id: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Return a structured snapshot of visible, actionable elements on the current page."""
    if electron_browser_available():
        try:
            result = _normalize_browser_result(await _electron_browser_rpc(
                "inspect",
                {"maxElements": max_elements, "textLimit": text_limit},
                session_id=session_id,
                round_id="" if read_only else None,
            ))
            if result.get("ok") is True:
                result.setdefault(
                    "page_signal",
                    _browser_page_signal(
                        str(result.get("url") or ""),
                        str(result.get("title") or ""),
                        str(result.get("text") or ""),
                    ),
                )
                await _emit_electron_frame("inspect", result)
            return result
        except Exception as exc:
            logger.warning("Electron inspect failed (%s)", exc)
            return _electron_browser_failure(exc, elements=[])
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message(), "elements": []}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first.", "elements": []}
    try:
        return await session.inspect(max_elements=max_elements, text_limit=text_limit)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc), "elements": []}


async def visible_link_matches(target_url: str) -> dict[str, Any]:
    """Scan the current page for rendered anchors resolving to *target_url*."""
    target_url = _normalize_http_url(target_url)
    try:
        _check_url(target_url)
    except SSRFBlockedError as exc:
        return {"ok": False, "error": str(exc), "matches": []}
    if electron_browser_available():
        try:
            result = await _electron_browser_rpc("visibleLinkMatches", {"url": target_url}, timeout=10.0)
            if not isinstance(result.get("matches"), list):
                result["matches"] = []
            return result
        except Exception as exc:
            return _electron_browser_failure(exc, matches=[])
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message(), "matches": []}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first.", "matches": []}
    try:
        return await session.visible_link_matches(target_url)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc), "matches": []}


async def navigation_guard(target_url: str, reason: str, snapshot_token: str = "") -> dict[str, Any]:
    """Authorize direct navigation against current browser state and snapshot evidence."""
    target_url = _normalize_http_url(target_url)
    try:
        _check_url(target_url)
    except SSRFBlockedError as exc:
        return {"ok": False, "allowed": False, "code": "NAVIGATION_URL_BLOCKED", "error": str(exc)}
    if electron_browser_available():
        try:
            return await _electron_browser_rpc(
                "navigationGuard",
                {"url": target_url, "reason": reason, "snapshotToken": snapshot_token},
                timeout=10.0,
            )
        except Exception as exc:
            return {
                "ok": False,
                "allowed": False,
                "code": "NAVIGATION_GUARD_UNAVAILABLE",
                "error": browser_runtime_unavailable_message(exc),
            }
    if _ensure_playwright() is None:
        if reason == "ui_unreachable":
            return {
                "ok": False,
                "allowed": False,
                "code": "SNAPSHOT_CREDENTIAL_UNAVAILABLE",
                "error": "ui_unreachable requires an interactive browser snapshot.",
            }
        return {"ok": True, "allowed": True, "targetUrl": target_url}
    session = _get_session()
    try:
        return await session.navigation_guard(target_url, reason, snapshot_token)
    except Exception as exc:
        return {
            "ok": False,
            "allowed": False,
            "code": "NAVIGATION_GUARD_UNAVAILABLE",
            "error": browser_runtime_unavailable_message(exc),
        }


async def click(selector: str) -> dict[str, Any]:
    """Click an element on the current page by CSS selector."""
    if electron_browser_available():
        try:
            result = _normalize_browser_result(await _electron_browser_rpc("click", {"selector": selector}))
            if result.get("ok") is True:
                await _emit_electron_frame("click", result, target=selector, box=result.get("box"))
                return result
            logger.warning("Electron click ok:false (%s)", result.get("error"))
            return result
        except Exception as exc:
            logger.warning("Electron click failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        return await session.click(selector)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def click_ref(ref: str) -> dict[str, Any]:
    """Click an element from browser_snapshot by its stable ref (e.g. e12)."""
    if electron_browser_available():
        try:
            result = _normalize_browser_result(await _electron_browser_rpc("clickRef", {"ref": ref}))
            if result.get("ok") is True:
                await _emit_electron_frame("click", result, target=ref, box=result.get("box"))
            return result
        except Exception as exc:
            logger.warning("Electron click_ref failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        return await session.click_ref(ref)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def click_text(text: str, *, exact: bool = False) -> dict[str, Any]:
    """Click a visible element whose accessible/text content matches *text*."""
    if electron_browser_available():
        try:
            result = _normalize_browser_result(await _electron_browser_rpc("clickText", {"text": text, "exact": exact}))
            if result.get("ok") is True:
                await _emit_electron_frame("click", result, target=text, box=result.get("box"))
            return result
        except Exception as exc:
            logger.warning("Electron click_text failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        return await session.click_text(text, exact=exact)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def click_at(x: int, y: int) -> dict[str, Any]:
    """Click the current page at viewport coordinates."""
    if electron_browser_available():
        try:
            result = _normalize_browser_result(await _electron_browser_rpc("clickAt", {"x": x, "y": y}))
            if result.get("ok") is True:
                await _emit_electron_frame("click", result, target=f"{x},{y}", box=result.get("box"))
            return result
        except Exception as exc:
            logger.warning("Electron click_at failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        return await session.click_at(int(x), int(y))
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def type_text(selector: str, text: str, *, submit: bool = False) -> dict[str, Any]:
    """Type *text* into an element and optionally submit."""
    if electron_browser_available():
        try:
            result = await _electron_browser_rpc("type", {"selector": selector, "text": text, "submit": submit})
            if result.get("ok") is True:
                await _emit_electron_frame("type", result, target=selector)
                return result
            logger.warning("Electron type_text ok:false (%s)", result.get("error"))
            return result
        except Exception as exc:
            logger.warning("Electron type_text failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        return await session.type_text(selector, text, submit=submit)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def type_ref(ref: str, text: str, *, submit: bool = False) -> dict[str, Any]:
    """Type into an editable element from browser_snapshot by ref."""
    if electron_browser_available():
        try:
            result = await _electron_browser_rpc("typeRef", {"ref": ref, "text": text, "submit": submit})
            if result.get("ok") is True:
                await _emit_electron_frame("type", result, target=ref, box=result.get("box"))
            return result
        except Exception as exc:
            logger.warning("Electron type_ref failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        return await session.type_ref(ref, text, submit=submit)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def prepare_file_upload(*, chooser_id: str = "", ref: str = "") -> dict[str, Any]:
    """Resolve a browser file-input target without opening a native picker."""
    chooser_id = str(chooser_id or "").strip()
    ref = str(ref or "").strip()
    if electron_browser_available():
        try:
            return await _electron_browser_rpc(
                "prepareUpload",
                {"chooserId": chooser_id, "ref": ref},
                timeout=15.0,
            )
        except Exception as exc:
            logger.warning("Electron prepare_file_upload failed (%s)", exc)
            return _electron_browser_failure(exc)
    if chooser_id:
        return {"ok": False, "error": "chooser_id is supported only by the Electron desktop browser."}
    if not ref:
        return {"ok": False, "error": "ref is required outside Electron."}
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    try:
        return await _get_session().prepare_file_upload(ref)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def set_input_files(target: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply files to an approved browser input after transport-side revalidation."""
    if electron_browser_available():
        try:
            return await _electron_browser_rpc(
                "setInputFiles",
                {
                    "targetId": str(target.get("id") or ""),
                    "tabId": str(target.get("tabId") or ""),
                    "files": files,
                },
                timeout=90.0,
            )
        except Exception as exc:
            logger.warning("Electron set_input_files failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    try:
        return await _get_session().set_input_files(
            target,
            [str(item.get("path") or "") for item in files],
        )
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def wait_for_page(*, selector: str = "", text: str = "", url_contains: str = "", timeout_ms: int = 5000) -> dict[str, Any]:
    """Wait until a current-page condition is true."""
    if electron_browser_available():
        try:
            return await _electron_browser_rpc(
                "waitFor",
                {"selector": selector, "text": text, "urlContains": url_contains, "timeoutMs": timeout_ms},
                timeout=max(2.0, min(35.0, float(timeout_ms or 5000) / 1000.0 + 5.0)),
            )
        except Exception as exc:
            logger.warning("Electron wait_for failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        return await session.wait_for(selector=selector, text=text, url_contains=url_contains, timeout_ms=timeout_ms)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def network_log(*, max_entries: int = 40) -> dict[str, Any]:
    """Return recent resource/XHR/fetch URLs visible to the current page."""
    if electron_browser_available():
        try:
            return await _electron_browser_rpc("networkLog", {"maxEntries": max_entries}, timeout=10.0)
        except Exception as exc:
            logger.warning("Electron network_log failed (%s)", exc)
            return _electron_browser_failure(exc, entries=[])
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message(), "entries": []}
    session = _get_session()
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first.", "entries": []}
    try:
        return await session.network_log(max_entries=max_entries)
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc), "entries": []}


async def scroll_page(
    *,
    delta_x: int = 0,
    delta_y: int = 500,
    x: int | None = None,
    y: int | None = None,
    ref: str = "",
) -> dict[str, Any]:
    """Scroll the current page by *delta_x* / *delta_y* pixels."""
    if electron_browser_available():
        try:
            payload: dict[str, Any] = {"deltaX": delta_x, "deltaY": delta_y}
            if x is not None:
                payload["x"] = x
            if y is not None:
                payload["y"] = y
            if ref:
                payload["ref"] = ref
            return await _electron_browser_rpc("scroll", payload)
        except Exception as exc:
            logger.warning("Electron scroll failed (%s)", exc)
            return _electron_browser_failure(exc)
    if _ensure_playwright() is None:
        return {"ok": False, "error": browser_runtime_unavailable_message()}
    session = _get_session()
    if not await session._wait_for_control():
        return {"ok": False, "error": _USER_CONTROL_MSG}
    if session._page is None:
        return {"ok": False, "error": "No page open. Call browser_navigate first."}
    try:
        session._invalidate_snapshot()
        page = await session.page()
        px = x
        py = y
        if ref:
            box = await page.locator(f'[data-cyrene-ref="{ref.removeprefix("e")}"]').bounding_box()
            if box is None:
                return {"ok": False, "error": f"Scroll target {ref} not found."}
            px = round(box["x"] + box["width"] / 2)
            py = round(box["y"] + box["height"] / 2)
        if px is None or py is None:
            viewport = page.viewport_size or {"width": 1280, "height": 720}
            px = round(viewport["width"] / 2) if px is None else px
            py = round(viewport["height"] / 2) if py is None else py
        probe_id = f"cyrene-scroll-{time.monotonic_ns()}"
        before = await page.evaluate(
            """([x, y, dx, dy, probeId]) => {
                const root = document.scrollingElement || document.documentElement;
                const canMove = (el) => {
                    if (!(el instanceof Element)) return false;
                    const style = getComputedStyle(el);
                    const overflowX = style.overflowX || style.overflow;
                    const overflowY = style.overflowY || style.overflow;
                    const scrollableX = el === root || /^(auto|scroll|overlay)$/.test(overflowX);
                    const scrollableY = el === root || /^(auto|scroll|overlay)$/.test(overflowY);
                    const canX = dx > 0
                        ? scrollableX && el.scrollLeft + el.clientWidth < el.scrollWidth - 1
                        : dx < 0 && scrollableX && el.scrollLeft > 1;
                    const canY = dy > 0
                        ? scrollableY && el.scrollTop + el.clientHeight < el.scrollHeight - 1
                        : dy < 0 && scrollableY && el.scrollTop > 1;
                    return canX || canY;
                };
                const parentOf = (el) => el.parentElement || (el.getRootNode && el.getRootNode().host) || null;
                let target = document.elementFromPoint(x, y);
                while (target && !canMove(target)) target = parentOf(target);
                if (!target && canMove(root)) target = root;
                if (!target) return {found: false};
                target.setAttribute('data-cyrene-scroll-probe', probeId);
                return {
                    found: true,
                    tag: String(target.tagName || '').toLowerCase(),
                    id: String(target.id || ''),
                    ref: String(target.getAttribute('data-cyrene-ref') || ''),
                    scrollLeft: Number(target.scrollLeft || 0),
                    scrollTop: Number(target.scrollTop || 0),
                };
            }""",
            [px, py, delta_x, delta_y, probe_id],
        )
        await page.mouse.move(px, py)
        await page.mouse.wheel(delta_x, delta_y)
        await page.wait_for_timeout(100)
        after = await page.evaluate(
            """(probeId) => {
                const target = document.querySelector(`[data-cyrene-scroll-probe="${CSS.escape(probeId)}"]`);
                if (!target) return {found: false};
                const result = {
                    found: true,
                    scrollLeft: Number(target.scrollLeft || 0),
                    scrollTop: Number(target.scrollTop || 0),
                };
                target.removeAttribute('data-cyrene-scroll-probe');
                return result;
            }""",
            probe_id,
        )
        actual_delta_x = after.get("scrollLeft", 0) - before.get("scrollLeft", 0) if before.get("found") and after.get("found") else 0
        actual_delta_y = after.get("scrollTop", 0) - before.get("scrollTop", 0) if before.get("found") and after.get("found") else 0
        return {
            "ok": True,
            "moved": actual_delta_x != 0 or actual_delta_y != 0,
            "actualDeltaX": actual_delta_x,
            "actualDeltaY": actual_delta_y,
            "target": {key: before.get(key, "") for key in ("tag", "id", "ref")} if before.get("found") else None,
            "x": px,
            "y": py,
        }
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def list_tabs() -> dict[str, Any]:
    """List Electron browser tabs. Playwright fallback exposes only one page."""
    if electron_browser_available():
        try:
            return await _electron_browser_rpc("state", {}, timeout=10.0)
        except Exception as exc:
            return {"ok": False, "error": browser_runtime_unavailable_message(exc), "tabs": []}
    session = _get_session()
    if session._page is None:
        return {"ok": True, "tabs": [], "activeTabId": "", "activeTab": None}
    tab = {
        "id": "playwright",
        "title": "",
        "url": session._safe_url(),
        "active": True,
        "loading": False,
        "canGoBack": False,
        "canGoForward": False,
        "muted": False,
        "audible": False,
    }
    return {"ok": True, "tabs": [tab], "activeTabId": "playwright", "activeTab": tab}


async def new_tab(url: str = "about:blank") -> dict[str, Any]:
    """Create and activate a new Electron browser tab."""
    if not electron_browser_available():
        return {"ok": False, "error": "Multiple browser tabs are only available in the Electron desktop browser."}
    target = normalize_url_for_browser_tab(url)
    if target != "about:blank":
        try:
            _check_url(target)
        except SSRFBlockedError as exc:
            return {"ok": False, "error": str(exc)}
    try:
        result = await _electron_browser_rpc("createTab", {"url": target, "activate": True})
        active = result.get("activeTab") if isinstance(result, dict) else None
        if isinstance(active, dict):
            await _emit_electron_frame("new_tab", active)
        return result
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


def normalize_url_for_browser_tab(url: str) -> str:
    value = str(url or "").strip() or "about:blank"
    if value == "about:blank":
        return value
    return _normalize_http_url(value)


async def select_tab(tab_id: str) -> dict[str, Any]:
    """Activate an Electron browser tab by id."""
    if not electron_browser_available():
        return {"ok": False, "error": "Multiple browser tabs are only available in the Electron desktop browser."}
    try:
        result = await _electron_browser_rpc("activateTab", {"tabId": str(tab_id or "")})
        active = result.get("activeTab") if isinstance(result, dict) else None
        if isinstance(active, dict):
            await _emit_electron_frame("select_tab", active)
        return result
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


async def close_tab(tab_id: str = "") -> dict[str, Any]:
    """Close an Electron browser tab by id, or the active tab when omitted."""
    if not electron_browser_available():
        return {"ok": False, "error": "Multiple browser tabs are only available in the Electron desktop browser."}
    try:
        return await _electron_browser_rpc("closeTab", {"tabId": str(tab_id or "")})
    except Exception as exc:
        return {"ok": False, "error": browser_runtime_unavailable_message(exc)}


# ---------------------------------------------------------------------------
# Playwright availability
# ---------------------------------------------------------------------------


def _ensure_playwright() -> Any:
    """Lazy-check if Playwright is importable."""
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is False:
        return None
    if _PLAYWRIGHT_AVAILABLE is None:
        try:
            import playwright  # noqa: F401
            _PLAYWRIGHT_AVAILABLE = True
        except ImportError:
            _PLAYWRIGHT_AVAILABLE = False
            return None
    return True
