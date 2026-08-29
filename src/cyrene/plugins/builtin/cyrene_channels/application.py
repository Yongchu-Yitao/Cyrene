"""Routes, settings state, and polling lifecycle for messaging channels."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cyrene.plugins.context import PluginApplicationContext
from cyrene.localization import localized

logger = logging.getLogger(__name__)


class ChannelsApplicationService:
    """Own process-level Telegram and WeChat channel state."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._wechat_client: Any | None = None
        self._wechat_updater: Any | None = None
        self._wechat_lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        from .settings import get_env

        updater = self._wechat_updater
        return {
            "running": bool(updater is not None and updater._running),
            "connected": bool(get_env("WECHAT_BOT_TOKEN")),
            "owner_wxid": get_env("WECHAT_OWNER_ID"),
        }

    async def qr_login(self) -> dict[str, str]:
        from .routes import _qr_image_data_uri
        from .wechat.auth import WeChatAuth

        qrcode_id, qrcode_img = await WeChatAuth().get_qr_code()
        return {
            "qrcode_id": qrcode_id,
            "qrcode_img": qrcode_img,
            "qrcode_image": _qr_image_data_uri(qrcode_img),
        }

    async def poll_login(self, qrcode_id: str) -> dict[str, bool]:
        from .settings import write_env_keys
        from .wechat.auth import WeChatAuth

        token = await WeChatAuth().poll_login(str(qrcode_id or ""), timeout=120)
        if not token:
            return {"ok": False, "expired": True}
        write_env_keys({"WECHAT_BOT_TOKEN": token})
        return {"ok": True}

    async def startup(self) -> None:
        """Start polling automatically when a persisted token is present."""

        from .settings import get_env

        if get_env("WECHAT_BOT_TOKEN"):
            await self.start_wechat()
        else:
            logger.debug("WECHAT_BOT_TOKEN not set - WeChat channel disabled")

    async def start_wechat(self) -> dict[str, bool]:
        from .settings import get_env
        from .wechat import set_current_client
        from .wechat.bot import WeChatUpdater
        from .wechat.client import WeChatClient, WeChatConfig

        token = get_env("WECHAT_BOT_TOKEN")
        if not token:
            raise ValueError(localized(
                "WeChat is not configured. Sign in or provide WECHAT_BOT_TOKEN first.",
                "尚未配置微信。请先登录或提供 WECHAT_BOT_TOKEN。",
            ))
        async with self._wechat_lock:
            updater = self._wechat_updater
            if updater is not None and updater._running:
                return {"ok": True, "already_running": True}
            await self._stop_wechat_locked()
            client = WeChatClient(WeChatConfig(bot_token=token))
            updater = WeChatUpdater(client, self.db_path)
            self._wechat_client = client
            self._wechat_updater = updater
            set_current_client(client)
            try:
                await updater.start()
            except Exception:
                self._wechat_client = None
                self._wechat_updater = None
                set_current_client(None)
                await client.close()
                raise
            logger.info("WeChat polling started")
            return {"ok": True}

    async def _stop_wechat_locked(self) -> bool:
        from .wechat import get_current_client, set_current_client

        updater = self._wechat_updater
        client = self._wechat_client
        self._wechat_updater = None
        self._wechat_client = None
        if get_current_client() is client:
            set_current_client(None)
        try:
            if updater is not None:
                await updater.stop()
        finally:
            if client is not None:
                await client.close()
        return updater is not None or client is not None

    async def stop_wechat(self) -> dict[str, bool]:
        async with self._wechat_lock:
            stopped = await self._stop_wechat_locked()
        if stopped:
            logger.info("WeChat polling stopped")
            return {"ok": True}
        return {"ok": True, "already_stopped": True}

    async def shutdown(self) -> None:
        await self.stop_wechat()

    def owns_channel_bot(self, channel: str, bot: Any) -> bool:
        """Reject stale WeChat clients after this pack is stopped."""

        normalized = str(channel or "").strip().lower()
        if normalized == "wechat":
            return bot is not None and bot is self._wechat_client
        return normalized == "telegram" and callable(
            getattr(bot, "send_message", None)
        )

    async def notify_telegram(self, title: str, body: str) -> dict[str, Any]:
        import httpx

        from cyrene.localization import localized
        from cyrene.runtime.settings_store import get as get_setting
        from .settings import get_env, telegram_owner_id

        token = get_env("TELEGRAM_BOT_TOKEN")
        owner_id = telegram_owner_id()
        if not token or owner_id is None:
            return {"ok": False, "error": localized(
                "Telegram bot token or owner ID is not configured.",
                "尚未配置 Telegram bot token 或 owner ID。",
            )}
        if not get_setting("notify_telegram", True):
            return {"ok": False, "error": localized(
                "Telegram notifications are disabled in settings.",
                "设置中已禁用 Telegram 通知。",
            )}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": owner_id,
                        "text": f"*{title}*\n{body}",
                        "parse_mode": "Markdown",
                    },
                )
                response.raise_for_status()
            return {"ok": True}
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)
            return {"ok": False, "code": "telegram_notification_failed", "error": localized(
                "Telegram notification failed.",
                "Telegram 通知发送失败。",
            )}

    async def notify_wechat(self, title: str, body: str) -> dict[str, Any]:
        from cyrene.localization import localized
        from cyrene.runtime.settings_store import get as get_setting
        from .settings import get_env

        if not get_setting("notify_wechat", True):
            return {"ok": False, "error": localized(
                "WeChat notifications are disabled in settings.",
                "设置中已禁用微信通知。",
            )}
        client = self._wechat_client
        if client is None:
            return {"ok": False, "error": localized(
                "The WeChat client is not connected.", "微信客户端未连接。"
            )}
        owner_id = get_env("WECHAT_OWNER_ID") or client._config.owner_wxid
        if not owner_id:
            return {"ok": False, "error": localized(
                "The WeChat owner ID is not configured.", "尚未配置微信 owner ID。"
            )}
        try:
            await client.send_message(owner_id, f"📋 {title}\n{body}")
            return {"ok": True}
        except Exception as exc:
            logger.warning("WeChat notification failed: %s", exc)
            return {"ok": False, "code": "wechat_notification_failed", "error": localized(
                "WeChat notification failed.",
                "微信通知发送失败。",
            )}

    async def prepare_data_reset(self) -> dict[str, bool]:
        """Stop channel work and clear Plugin-owned credentials."""

        from .settings import write_env_keys

        await self.stop_wechat()
        write_env_keys({
            "TELEGRAM_BOT_TOKEN": "",
            "WECHAT_BOT_TOKEN": "",
            "WECHAT_OWNER_ID": "",
        })
        return {"channels": True}

    @staticmethod
    def editable_env_keys() -> dict[str, dict[str, Any]]:
        """Contribute channel credentials only while this pack is active."""

        from .settings import editable_env_keys

        return editable_env_keys()

    @staticmethod
    def setting_specs() -> tuple[Any, ...]:
        from cyrene.runtime.settings_service import SettingSpec

        return (
            SettingSpec(
                "notify_telegram", "runtime", "channels", "boolean", True,
                True, True, False, "R1", "immediate",
            ),
            SettingSpec(
                "notify_wechat", "runtime", "channels", "boolean", True,
                True, True, False, "R1", "immediate",
            ),
        )

    @staticmethod
    def setting_control_specs() -> tuple[Any, ...]:
        from cyrene.runtime.settings_service import SettingControlSpec

        return (
            SettingControlSpec(
                "channels.telegram_token", "channels", "user_ceremony",
                "cyrene.secret.input", "R3", secret=True,
            ),
            SettingControlSpec(
                "channels.wechat_login", "channels", "user_ceremony",
                "cyrene.ui.inspect", "R3",
            ),
            SettingControlSpec(
                "channels.wechat_runtime", "channels", "current_ui",
                "cyrene.ui.inspect", "R2",
            ),
        )

    @staticmethod
    def storage_paths() -> dict[str, tuple[Any, ...]]:
        """Channel credentials live in the core encrypted settings snapshot."""

        return {}

    @staticmethod
    def backup_sources() -> dict[str, tuple[Any, ...]]:
        """No channel files exist outside the portable settings snapshot."""

        return {}


def setup_application(context: PluginApplicationContext) -> None:
    from .routes import register_wechat_routes

    service = ChannelsApplicationService(context.db_path)
    register_wechat_routes(context.router, service)
    context.provide("channels", service)
    context.expose_frontend("channels")
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)


__all__ = ["ChannelsApplicationService", "setup_application"]
