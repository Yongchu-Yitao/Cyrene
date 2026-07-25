import base64
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_qr_image_data_uri_is_self_contained():
    from route.channels.wechat import _qr_image_data_uri

    data_uri = _qr_image_data_uri(
        "https://liteapp.weixin.qq.com/q/example?qrcode=test&bot_type=3"
    )

    prefix = "data:image/svg+xml;base64,"
    assert data_uri.startswith(prefix)
    svg = base64.b64decode(data_uri.removeprefix(prefix)).decode("utf-8")
    assert "<svg" in svg
    assert "path" in svg


def test_qr_login_route_returns_local_qr_image(monkeypatch):
    from cyrene.channels.wechat import auth
    from route.channels.wechat import register_wechat_routes

    async def fake_get_qr_code(self):
        return "qr-id", "https://liteapp.weixin.qq.com/q/example?qrcode=qr-id&bot_type=3"

    monkeypatch.setattr(auth.WeChatAuth, "get_qr_code", fake_get_qr_code)

    app = FastAPI()
    register_wechat_routes(app)
    response = TestClient(app).post("/api/wechat/qr-login")

    assert response.status_code == 200
    payload = response.json()
    assert payload["qrcode_id"] == "qr-id"
    assert payload["qrcode_img"].startswith("https://liteapp.weixin.qq.com/")
    assert payload["qrcode_image"].startswith("data:image/svg+xml;base64,")
