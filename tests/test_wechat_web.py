import base64
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent.plugin import PluginContext


def test_qr_image_data_uri_is_self_contained():
    from agent.plugin.plugin_impl.cyrene_channels.routes import _qr_image_data_uri

    data_uri = _qr_image_data_uri(
        "https://liteapp.weixin.qq.com/q/example?qrcode=test&bot_type=3"
    )

    prefix = "data:image/svg+xml;base64,"
    assert data_uri.startswith(prefix)
    svg = base64.b64decode(data_uri.removeprefix(prefix)).decode("utf-8")
    assert "<svg" in svg
    assert "path" in svg


def test_channels_pack_exposes_one_fixed_runtime_plugin():
    from agent.plugin.plugin_impl.cyrene_channels import plugin_pack

    assert [plugin.name for plugin in plugin_pack.plugins] == [
        "cyrene_channels.runtime"
    ]
    assert plugin_pack.plugins[0].model_visible is False
    assert plugin_pack.plugins[0].metadata["required"] is True

    result = plugin_pack.plugins[0].handler(
        {},
        PluginContext(
            services={"channels": SimpleNamespace(
                status=lambda: {"running": True, "connected": True}
            )},
        ),
    )
    assert result == {"ok": True, "running": True, "connected": True}


def test_qr_login_route_returns_local_qr_image(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_channels.application import (
        ChannelsApplicationService,
    )
    from agent.plugin.plugin_impl.cyrene_channels import wechat
    from agent.plugin.plugin_impl.cyrene_channels.routes import register_wechat_routes

    async def fake_get_qr_code(self):
        return "qr-id", "https://liteapp.weixin.qq.com/q/example?qrcode=qr-id&bot_type=3"

    monkeypatch.setattr(wechat.WeChatAuth, "get_qr_code", fake_get_qr_code)

    app = FastAPI()
    router = APIRouter()
    register_wechat_routes(router, ChannelsApplicationService(":memory:"))
    app.include_router(router)
    response = TestClient(app).post("/api/wechat/qr-login")

    assert response.status_code == 200
    payload = response.json()
    assert payload["qrcode_id"] == "qr-id"
    assert payload["qrcode_img"].startswith("https://liteapp.weixin.qq.com/")
    assert payload["qrcode_image"].startswith("data:image/svg+xml;base64,")


def test_channels_pack_routes_and_polling_follow_activation(tmp_path, monkeypatch):
    import asyncio

    from agent.plugin import (
        PluginApplicationHost,
        PluginRegistry,
        set_active_plugin_application_host,
    )
    from agent.plugin.plugin_impl.cyrene_channels import plugin_pack
    from agent.plugin.plugin_impl.cyrene_channels.application import (
        ChannelsApplicationService,
    )

    events: list[str] = []

    async def startup(self):
        events.append("startup")

    async def shutdown(self):
        events.append("shutdown")

    monkeypatch.setattr(ChannelsApplicationService, "startup", startup)
    monkeypatch.setattr(ChannelsApplicationService, "shutdown", shutdown)

    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test")
    registry.configure_activation(plugins={}, packs={"cyrene_channels": False})
    app = FastAPI()
    host = PluginApplicationHost(
        app=app,
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()
    host.attach(router)
    app.include_router(router)
    set_active_plugin_application_host(host)

    try:
        from cyrene.runtime import settings_service

        asyncio.run(host.startup())
        with TestClient(app) as client:
            assert client.get("/api/wechat/status").status_code == 404
        assert host.service("channels") is None
        assert "channels" not in host.frontend_modules
        assert "channels" in settings_service.describe()["excluded_tabs"]
        from cyrene import config

        assert "TELEGRAM_BOT_TOKEN" not in config.editable_env_keys()
        assert "WECHAT_BOT_TOKEN" not in config.editable_env_keys()
        assert events == []

        registry.configure_activation(plugins={}, packs={"cyrene_channels": True})
        asyncio.run(host.reconcile_activation())
        with TestClient(app) as client:
            assert client.get("/api/wechat/status").status_code == 404
        assert host.service("channels") is None
        assert host.restart_required_packs == ("cyrene_channels",)
        assert events == []
    finally:
        asyncio.run(host.shutdown())
        set_active_plugin_application_host(None)

    enabled_registry = PluginRegistry(include_core=False)
    enabled_registry.register_pack(plugin_pack, source="test-enabled")
    enabled_registry.configure_activation(plugins={}, packs={"cyrene_channels": True})
    enabled_app = FastAPI()
    enabled_host = PluginApplicationHost(
        app=enabled_app,
        registry=enabled_registry,
        bot=None,
        db_path=str(tmp_path / "enabled.db"),
        data_directory=tmp_path / "enabled-data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    enabled_router = APIRouter()
    enabled_host.attach(enabled_router)
    enabled_app.include_router(enabled_router)
    set_active_plugin_application_host(enabled_host)
    try:
        from cyrene.runtime import settings_service

        asyncio.run(enabled_host.startup())
        with TestClient(enabled_app) as client:
            assert client.get("/api/wechat/status").status_code == 200
        assert enabled_host.service("channels") is not None
        assert enabled_host.frontend_modules == ["channels"]
        assert "channels" in settings_service.describe()["covered_tabs"]
        assert {item["key"] for item in settings_service.describe()["settings"]} >= {
            "notify_telegram", "notify_wechat",
        }
        from cyrene import config

        assert {"TELEGRAM_BOT_TOKEN", "WECHAT_BOT_TOKEN"} <= set(
            config.editable_env_keys()
        )
        assert events == ["startup"]

        enabled_registry.configure_activation(
            plugins={}, packs={"cyrene_channels": False}
        )
        asyncio.run(enabled_host.reconcile_activation())
        assert events == ["startup", "shutdown"]
        assert "channels" in settings_service.describe()["excluded_tabs"]
        assert "TELEGRAM_BOT_TOKEN" not in config.editable_env_keys()
    finally:
        asyncio.run(enabled_host.shutdown())
        set_active_plugin_application_host(None)
