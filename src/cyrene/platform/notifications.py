"""Notification system — desktop notifications, webhook alerts, and in-app SSE events.

Supports three delivery channels:
  1. **Desktop native** — Electron's native operating-system notification API.
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
from datetime import datetime, timezone
from typing import Any

import httpx

from cyrene.localization import localized
from cyrene.platform.host_bridge import HostBridgeError, call_host

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
# Desktop — Electron native host
# ---------------------------------------------------------------------------


async def _notify_desktop(title: str, body: str) -> dict[str, Any]:
    """Deliver through the authenticated Electron main-process bridge.

    Desktop notifications belong to the desktop host. Keeping their lifecycle
    in Electron avoids launching platform helper processes from the Python
    event loop and works even when every renderer window is hidden.
    """
    try:
        result = await call_host(
            "notification.show",
            {"title": str(title), "body": str(body)},
            timeout=3.0,
        )
    except HostBridgeError as exc:
        return {
            "ok": False,
            "error": localized(
                "The Electron desktop host is unavailable.",
                "Electron 桌面宿主当前不可用。",
            ),
            "code": exc.code,
        }
    if result.get("ok") is not True:
        return {
            "ok": False,
            "error": str(result.get("detail") or result.get("error") or localized(
                "The desktop host rejected the notification.",
                "桌面宿主拒绝了这条通知。",
            )),
            "code": str(result.get("error") or "desktop_notification_failed"),
        }
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
