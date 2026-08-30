"""Notification system — desktop notifications, webhook alerts, and in-app SSE events.

Supports three delivery channels:
  1. **Desktop native** — macOS (``terminal-notifier`` with app icon), Windows (VBScript popup), Linux (notify-send).
  2. **Webhook** — POST to Discord, Slack, or generic webhook URLs.
  3. **In-app SSE** — pushes through the existing ``debug.publish_event`` bus.

Configure via ``.env``:
  - ``NOTIFICATION_WEBHOOK_URL`` — optional webhook endpoint (Discord/Slack/Generic).
  - ``NOTIFICATION_WEBHOOK_TYPE`` — ``discord``, ``slack``, or ``generic``.

Agent tool ``send_notification`` lets the agent send notifications directly.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

import httpx

from cyrene.localization import localized
from cyrene.platform.paths import TEMP_DIR

logger = logging.getLogger(__name__)

_WEBHOOK_URL = os.getenv("NOTIFICATION_WEBHOOK_URL", "").strip()
_WEBHOOK_TYPE = os.getenv("NOTIFICATION_WEBHOOK_TYPE", "generic").strip().lower()
_NOTIFICATION_ENABLED = os.getenv("NOTIFICATION_ENABLED", "1") not in ("0", "false", "no")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_ALL_CHANNELS = ("desktop", "webhook", "telegram", "wechat", "sse")


async def notify(
    title: str,
    body: str,
    *,
    channel: str = "auto",
    webhook_url: str | None = None,
    webhook_type: str | None = None,
) -> dict[str, Any]:
    """Send a notification through one or more channels.

    Args:
        title: Notification title (short).
        body: Notification body text.
        channel: delivery mode —
            ``"auto"`` tries channels in order (desktop → webhook → telegram →
            wechat → sse) and **stops after the first success**, so a delivered
            desktop notification never fans out to external messengers (#45);
            ``"broadcast"`` delivers through *every* configured channel; or a
            single channel name (``"desktop"``, ``"webhook"``, ``"telegram"``,
            ``"wechat"``, ``"sse"``).
        webhook_url: Override the configured webhook URL.
        webhook_type: Override the configured webhook type.

    Returns:
        ``{"ok": bool, "channels": {name: {"ok": bool, ...}}}``.
    """
    if not _NOTIFICATION_ENABLED:
        return {"ok": False, "error": localized(
            "Notifications are disabled.", "通知已禁用。"
        )}

    if channel == "auto":
        order, stop_after_first = list(_ALL_CHANNELS), True
    elif channel == "broadcast":
        order, stop_after_first = list(_ALL_CHANNELS), False
    elif channel in _ALL_CHANNELS:
        order, stop_after_first = [channel], False
    else:
        return {"ok": False, "error": localized(
            "Unknown notification channel: {channel}",
            "未知通知渠道：{channel}",
            channel=channel,
        )}

    results: dict[str, Any] = {}
    for ch in order:
        res = await _dispatch_channel(ch, title, body, webhook_url, webhook_type)
        if res is None:
            # Channel not applicable (e.g. no webhook URL). Surface it as an
            # error only when that channel was explicitly requested.
            if len(order) == 1:
                results[ch] = {"ok": False, "error": localized(
                    "{channel} is not configured.",
                    "尚未配置 {channel}。",
                    channel=ch,
                )}
            continue
        results[ch] = res
        if stop_after_first and res.get("ok"):
            break

    any_ok = any(r.get("ok") for r in results.values())
    return {"ok": any_ok, "channels": results}


async def _dispatch_channel(
    ch: str,
    title: str,
    body: str,
    webhook_url: str | None,
    webhook_type: str | None,
) -> dict[str, Any] | None:
    """Deliver through a single channel. Returns the per-channel result, or
    ``None`` when the channel is not applicable (e.g. no webhook configured)."""
    if ch == "desktop":
        return await _notify_desktop(title, body)
    if ch == "webhook":
        wh_url = webhook_url or _WEBHOOK_URL
        if not wh_url:
            return None
        return await _notify_webhook(wh_url, webhook_type or _WEBHOOK_TYPE, title, body)
    if ch == "telegram":
        return await _notify_telegram(title, body)
    if ch == "wechat":
        return await _notify_wechat(title, body)
    if ch == "sse":
        return await _publish_sse(title, body)
    return {"ok": False, "error": localized(
        "Unknown notification channel: {channel}",
        "未知通知渠道：{channel}",
        channel=ch,
    )}


async def _publish_sse(title: str, body: str) -> dict[str, Any]:
    """Publish a ``notification`` event on the in-app SSE bus."""
    try:
        from cyrene.observability import debug as cy_debug

        await cy_debug.publish_event({
            "type": "notification",
            "title": title,
            "body": body,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return {"ok": True}
    except Exception:
        logger.warning("In-app notification publish failed", exc_info=True)
        return {
            "ok": False,
            "error": localized(
                "The in-app notification could not be delivered.",
                "应用内通知发送失败。",
            ),
            "code": "notification_publish_failed",
        }


# ---------------------------------------------------------------------------
# Desktop — cross-platform (macOS, Windows, Linux)
# ---------------------------------------------------------------------------


async def _notify_desktop(title: str, body: str) -> dict[str, Any]:
    system = platform.system()
    try:
        if system == "Darwin":
            return _notify_macos(title, body)
        elif system == "Windows":
            return _notify_windows(title, body)
        elif system == "Linux":
            return _notify_linux(title, body)
        else:
            return {"ok": False, "error": localized(
                "Unsupported platform: {platform}",
                "不支持的平台：{platform}",
                platform=system,
            )}
    except Exception:
        logger.warning("Desktop notification failed", exc_info=True)
        return {
            "ok": False,
            "error": localized(
                "The desktop notification could not be delivered.",
                "桌面通知发送失败。",
            ),
            "code": "desktop_notification_failed",
        }


def _notify_macos(title: str, body: str) -> dict[str, Any]:
    """macOS native notification via ``terminal-notifier``.

    ``terminal-notifier`` is a small CLI tool (``brew install terminal-notifier``)
    that sends real Notification Center alerts from any process — no bundle ID,
    no running NSApplication, no AppleScript required.  It fires even when no
    Web UI or Electron window is open, which is the whole point for scheduled-task
    reminders (#12).

    Three-tier layout so each piece of information has its own line::

        [Cyrene icon]  Cyrene                ← ASSISTANT_NAME (always)
                       Scheduled task done   ← title arg  (event/task label)
                       Backed up 42 files    ← body arg   (execution detail)

    When ``terminal-notifier`` is not installed the channel reports failure so
    ``auto`` mode can fall through to the next available channel (SSE, WeChat,
    Telegram) rather than silently dropping the notification.
    """
    import shutil
    from cyrene.config import ASSISTANT_NAME

    binary = shutil.which("terminal-notifier")
    if not binary:
        return {
            "ok": False,
            "error": localized(
                "terminal-notifier was not found; install it with: brew install terminal-notifier",
                "未找到 terminal-notifier；请运行 brew install terminal-notifier 安装。",
            ),
        }

    # Use the installed Cyrene.app as the notification sender so the left icon
    # shows Cyrene's own app icon on all macOS versions (the -appIcon flag was
    # restricted by Apple on macOS 12+, but -sender is reliable).  If the
    # Electron app is not installed, terminal-notifier falls back to its own
    # icon gracefully — no error, no crash.
    #
    # Three-tier layout:
    #   -title    → ASSISTANT_NAME ("Cyrene")  — always the app/agent name
    #   -subtitle → title arg                  — task type / event label
    #   -message  → body arg                   — execution detail / content
    cmd = [
        binary,
        "-sender",   "com.cyrene.app",
        "-title",    ASSISTANT_NAME,
        "-subtitle", title,
        "-message",  body,
        "-sound",    "default",
    ]

    proc = subprocess.run(cmd, capture_output=True, timeout=10)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip() or f"terminal-notifier exited {proc.returncode}"
        return {"ok": False, "error": err}
    return {"ok": True}


def _notify_windows(title: str, body: str) -> dict[str, Any]:
    """Windows toast popup via VBScript (auto-dismisses after 5s)."""
    safe_title = title.replace('"', '""')
    safe_body = body.replace('"', '""')
    vbs = f'CreateObject("Wscript.Shell").Popup "{safe_body}", 5, "{safe_title}", 64'
    tmp = None
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".vbs", mode="w", dir=TEMP_DIR, delete=False) as f:
            f.write(vbs)
            tmp = f.name
        subprocess.run(["cscript", "//NoLogo", tmp], capture_output=True, timeout=10)
        return {"ok": True}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _notify_linux(title: str, body: str) -> dict[str, Any]:
    """Linux desktop notification via notify-send (libnotify)."""
    subprocess.run(
        ["notify-send", title, body],
        capture_output=True, timeout=10,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


async def _notify_telegram(title: str, body: str) -> dict[str, Any]:
    """Send a Telegram message to the configured owner."""
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("channels")
    if service is None:
        return {"ok": False, "error": localized(
            "The messaging channels Plugin is not available.",
            "消息通道 Plugin 当前不可用。",
        )}
    return await service.notify_telegram(title, body)


# ---------------------------------------------------------------------------
# WeChat
# ---------------------------------------------------------------------------


async def _notify_wechat(title: str, body: str) -> dict[str, Any]:
    """Send a WeChat message to the configured owner."""
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("channels")
    if service is None:
        return {"ok": False, "error": localized(
            "The messaging channels Plugin is not available.",
            "消息通道 Plugin 当前不可用。",
        )}
    return await service.notify_wechat(title, body)


# ---------------------------------------------------------------------------
# Webhook (Discord / Slack / Generic)
# ---------------------------------------------------------------------------


async def _notify_webhook(url: str, wh_type: str, title: str, body: str) -> dict[str, Any]:
    try:
        payload = _webhook_payload(wh_type, title, body)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        return {"ok": True}
    except Exception as exc:
        logger.warning("Webhook notification failed: %s", exc)
        return {
            "ok": False,
            "error": localized(
                "Webhook notification failed.",
                "Webhook 通知发送失败。",
            ),
        }


def _webhook_payload(wh_type: str, title: str, body: str) -> dict[str, Any]:
    if wh_type == "discord":
        return {
            "content": f"**{title}**\n{body}",
            "username": "Cyrene",
        }
    if wh_type == "slack":
        return {
            "text": f"*{title}*\n{body}",
            "username": "Cyrene",
        }
    # Generic
    return {"title": title, "body": body, "source": "cyrene"}
