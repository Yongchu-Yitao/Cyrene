"""FastAPI routes for WeChat QR login, status, and lifecycle control."""

from __future__ import annotations

import base64
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException


class WeChatChannelService(Protocol):
    def status(self) -> dict[str, Any]: ...

    async def qr_login(self) -> dict[str, str]: ...

    async def poll_login(self, qrcode_id: str) -> dict[str, bool]: ...

    async def start_wechat(self) -> dict[str, bool]: ...

    async def stop_wechat(self) -> dict[str, bool]: ...


def _qr_image_data_uri(content: str, size: int = 280) -> str:
    """Render QR content locally so login does not depend on a third-party image service."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    qr = QrCodeWidget(content)
    x1, y1, x2, y2 = qr.getBounds()
    quiet_zone = 16
    scale = (size - quiet_zone * 2) / max(x2 - x1, y2 - y1)
    drawing = Drawing(
        size,
        size,
        transform=[
            scale,
            0,
            0,
            scale,
            quiet_zone - x1 * scale,
            quiet_zone - y1 * scale,
        ],
    )
    drawing.add(qr)
    svg = renderSVG.drawToString(drawing)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def register_wechat_routes(
    router: APIRouter,
    service: WeChatChannelService,
) -> None:
    """Register activation-guarded ``/api/wechat/*`` adapters."""

    @router.get("/api/wechat/status")
    async def wechat_status():
        return service.status()

    @router.post("/api/wechat/qr-login")
    async def wechat_qr_login():
        return await service.qr_login()

    @router.post("/api/wechat/poll-login")
    async def wechat_poll_login(data: dict):
        return await service.poll_login(str(data.get("qrcode_id") or ""))

    @router.post("/api/wechat/start")
    async def wechat_start():
        try:
            return await service.start_wechat()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/api/wechat/stop")
    async def wechat_stop():
        return await service.stop_wechat()


__all__ = ["WeChatChannelService", "_qr_image_data_uri", "register_wechat_routes"]
