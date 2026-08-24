"""Loopback HTTPS host for the PowerPoint Office.js add-in."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response

from cyrene.config import DATA_DIR
from cyrene.office.service import OfficeBridgeError, get_office_bridge
from cyrene.office.protocol import expected_handshake

logger = logging.getLogger(__name__)

DEFAULT_OFFICE_PORT = 4243
ADDIN_ID = "92f1df3d-9a70-48fa-bc2b-270cc79fd75b"
_STATIC_DIR = Path(__file__).with_name("static")
_ICON_REVISION = hashlib.sha256((_STATIC_DIR / "icon-1024.png").read_bytes()).hexdigest()[:12]


class OfficeGatewayFiles:
    def __init__(self, root: Path | None = None, port: int | None = None) -> None:
        self.root = Path(root or (DATA_DIR / "office_gateway"))
        self.port = int(port or os.environ.get("CYRENE_OFFICE_PORT") or DEFAULT_OFFICE_PORT)
        self.secret_path = self.root / "bridge_secret"
        self.cert_path = self.root / "localhost.crt"
        self.key_path = self.root / "localhost.key"
        self.manifest_path = self.root / "cyrene-powerpoint-addin.xml"
        self._ensured = False

    def ensure(self) -> None:
        if self._ensured:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.secret_path.exists():
            self.secret_path.write_text(secrets.token_urlsafe(36), encoding="utf-8")
            try:
                self.secret_path.chmod(0o600)
            except OSError:
                pass
        if not self.cert_path.exists() or not self.key_path.exists():
            self._generate_certificate()
        self.manifest_path.write_text(self.manifest_xml(), encoding="utf-8")
        try:
            self.manifest_path.chmod(0o600)
        except OSError:
            pass
        self._ensured = True

    @property
    def secret(self) -> str:
        self.ensure()
        return self.secret_path.read_text(encoding="utf-8").strip()

    @property
    def base_url(self) -> str:
        return f"https://localhost:{self.port}"

    def _generate_certificate(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Cyrene Office localhost"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cyrene Local Development"),
        ])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        self.key_path.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        self.cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass

    def manifest_xml(self) -> str:
        token = escape(self.secret_path.read_text(encoding="utf-8").strip()) if self.secret_path.exists() else ""
        taskpane_url = f"{self.base_url}/taskpane.html?token={token}"
        icon16 = f"{self.base_url}/assets/icon-16.png?token={token}&amp;v={_ICON_REVISION}"
        icon32 = f"{self.base_url}/assets/icon-32.png?token={token}&amp;v={_ICON_REVISION}"
        icon80 = f"{self.base_url}/assets/icon-80.png?token={token}&amp;v={_ICON_REVISION}"
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0" xmlns:ov="http://schemas.microsoft.com/office/taskpaneappversionoverrides" xsi:type="TaskPaneApp">
  <Id>{ADDIN_ID}</Id>
  <Version>1.3.1.0</Version>
  <ProviderName>Cyrene</ProviderName>
  <DefaultLocale>zh-CN</DefaultLocale>
  <DisplayName DefaultValue="Cyrene Live PowerPoint"/>
  <Description DefaultValue="Let Cyrene inspect and edit the presentation that is open in PowerPoint."/>
  <IconUrl DefaultValue="{icon32}"/>
  <HighResolutionIconUrl DefaultValue="{icon80}"/>
  <SupportUrl DefaultValue="{self.base_url}/health"/>
  <AppDomains><AppDomain>{self.base_url}</AppDomain></AppDomains>
  <Hosts><Host Name="Presentation"/></Hosts>
  <Requirements><Sets DefaultMinVersion="1.1"><Set Name="PowerPointApi" MinVersion="1.5"/><Set Name="SharedRuntime" MinVersion="1.1"/></Sets></Requirements>
  <DefaultSettings><SourceLocation DefaultValue="{taskpane_url}"/></DefaultSettings>
  <Permissions>ReadWriteDocument</Permissions>
  <VersionOverrides xmlns="http://schemas.microsoft.com/office/taskpaneappversionoverrides" xsi:type="VersionOverridesV1_0">
    <Hosts>
      <Host xsi:type="Presentation">
        <Runtimes><Runtime resid="Taskpane.Url" lifetime="long"/></Runtimes>
        <DesktopFormFactor>
          <GetStarted><Title resid="GetStarted.Title"/><Description resid="GetStarted.Description"/><LearnMoreUrl resid="Taskpane.Url"/></GetStarted>
          <FunctionFile resid="Taskpane.Url"/>
          <ExtensionPoint xsi:type="PrimaryCommandSurface">
            <OfficeTab id="TabHome"><Group id="Cyrene.Group"><Label resid="Group.Label"/>
              <Icon><bt:Image size="16" resid="Icon.16"/><bt:Image size="32" resid="Icon.32"/><bt:Image size="80" resid="Icon.80"/></Icon>
              <Control xsi:type="Button" id="Cyrene.Open"><Label resid="Button.Label"/><Supertip><Title resid="Button.Label"/><Description resid="Button.Tooltip"/></Supertip>
                <Icon><bt:Image size="16" resid="Icon.16"/><bt:Image size="32" resid="Icon.32"/><bt:Image size="80" resid="Icon.80"/></Icon>
                <Action xsi:type="ShowTaskpane"><TaskpaneId>Cyrene.Live.PowerPoint</TaskpaneId><SourceLocation resid="Taskpane.Url"/></Action>
              </Control>
            </Group></OfficeTab>
          </ExtensionPoint>
        </DesktopFormFactor>
      </Host>
    </Hosts>
    <Resources>
      <bt:Images><bt:Image id="Icon.16" DefaultValue="{icon16}"/><bt:Image id="Icon.32" DefaultValue="{icon32}"/><bt:Image id="Icon.80" DefaultValue="{icon80}"/></bt:Images>
      <bt:Urls><bt:Url id="Taskpane.Url" DefaultValue="{taskpane_url}"/></bt:Urls>
      <bt:ShortStrings><bt:String id="GetStarted.Title" DefaultValue="Cyrene is ready"/><bt:String id="Group.Label" DefaultValue="Cyrene"/><bt:String id="Button.Label" DefaultValue="Connect Cyrene"/></bt:ShortStrings>
      <bt:LongStrings><bt:String id="GetStarted.Description" DefaultValue="Open the Cyrene pane to let the agent edit this presentation live."/><bt:String id="Button.Tooltip" DefaultValue="Connect this presentation to the local Cyrene agent."/></bt:LongStrings>
    </Resources>
  </VersionOverrides>
</OfficeApp>
'''

    def public_info(self, *, running: bool) -> dict[str, Any]:
        return {
            "running": running,
            "url": self.base_url,
            "manifest_path": str(self.manifest_path.resolve()),
            "certificate_path": str(self.cert_path.resolve()),
            "install_command": "uv run python -m cyrene.office.install --trust",
            "requirement_sets": {"PowerPointApi": "1.5 (core), 1.8 (render/undo/table/group/z-order)", "ImageCoercion": "1.1 (pictures/visual charts)", "SharedRuntime": "1.1"},
        }


def _authorized(request: Request, files: OfficeGatewayFiles) -> bool:
    return secrets.compare_digest(str(request.query_params.get("token") or ""), files.secret)


def _register_document_routes(app: FastAPI, material: OfficeGatewayFiles) -> None:
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "cyrene-office-gateway", "sessions": len(get_office_bridge().list_sessions()), "agentKit": expected_handshake(_STATIC_DIR)}

    @app.post("/benchmark/invoke")
    async def benchmark_invoke(request: Request) -> dict[str, Any]:
        """Run the fixed model-free benchmark workload inside the gateway process."""
        _require_authorized(request, material)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid JSON payload") from exc
        method = str(payload.get("method") or "") if isinstance(payload, dict) else ""
        allowed = {
            "ppt.get_context", "ppt.create_slide", "ppt.list_shapes",
            "ppt.apply_batch", "ppt.read_text", "ppt.delete_slide",
        }
        if method not in allowed:
            raise HTTPException(status_code=400, detail="method is not part of the PowerPoint benchmark workload")
        args = payload.get("arguments") if isinstance(payload, dict) else None
        if not isinstance(args, dict):
            raise HTTPException(status_code=400, detail="arguments must be an object")
        from cyrene.tool_impl.office import kit

        raw = await kit._method_handler(method, args)
        try:
            result = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail="benchmark tool returned invalid JSON") from exc
        return result

    @app.get("/taskpane.html")
    async def taskpane(request: Request) -> Response:
        if not _authorized(request, material):
            raise HTTPException(status_code=401, detail="invalid bridge token")
        html = (_STATIC_DIR / "taskpane.html").read_text(encoding="utf-8")
        html = html.replace("__CYRENE_OFFICE_TOKEN__", material.secret)
        html = html.replace("__CYRENE_OFFICE_BUILD_HASH__", str(expected_handshake(_STATIC_DIR)["buildHash"]))
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/taskpane.js")
    async def taskpane_js(request: Request) -> Response:
        if not _authorized(request, material):
            raise HTTPException(status_code=401, detail="invalid bridge token")
        contract = expected_handshake(_STATIC_DIR)
        source = (_STATIC_DIR / "taskpane.js").read_text(encoding="utf-8")
        for key, placeholder in {
            "protocolVersion": "__CYRENE_OFFICE_PROTOCOL_VERSION__",
            "kitVersion": "__CYRENE_OFFICE_KIT_VERSION__",
            "schemaHash": "__CYRENE_OFFICE_SCHEMA_HASH__",
            "buildHash": "__CYRENE_OFFICE_BUILD_HASH__",
        }.items():
            source = source.replace(placeholder, str(contract[key]))
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/taskpane.css")
    async def taskpane_css(request: Request) -> Response:
        if not _authorized(request, material):
            raise HTTPException(status_code=401, detail="invalid bridge token")
        css = (_STATIC_DIR / "taskpane.css").read_text(encoding="utf-8")
        css = css.replace("__CYRENE_OFFICE_TOKEN__", material.secret)
        return Response(css, media_type="text/css", headers={"Cache-Control": "no-store"})


def _webui_static_path(*parts: str) -> Path:
    import webui

    return Path(webui.__file__).parent.joinpath("static", "app", *parts)


def _require_authorized(request: Request, material: OfficeGatewayFiles) -> None:
    if not _authorized(request, material):
        raise HTTPException(status_code=401, detail="invalid bridge token")


def _register_asset_routes(app: FastAPI, material: OfficeGatewayFiles) -> None:
    @app.get("/assets/icon-{size}.png")
    async def icon(size: int, request: Request) -> Response:
        _require_authorized(request, material)
        if size not in {16, 32, 80}:
            raise HTTPException(status_code=404, detail="icon unavailable")
        icon_path = _STATIC_DIR / f"icon-{size}.png"
        if not icon_path.is_file():
            raise HTTPException(status_code=404, detail="icon unavailable")
        return FileResponse(icon_path, media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.get("/assets/cyrene-theme.css")
    async def cyrene_theme(request: Request) -> Response:
        _require_authorized(request, material)
        try:
            theme_path = _webui_static_path("shared", "theme", "base.css")
        except Exception as exc:
            raise HTTPException(status_code=404, detail="theme unavailable") from exc
        if not theme_path.is_file():
            raise HTTPException(status_code=404, detail="theme unavailable")
        return FileResponse(theme_path, media_type="text/css", headers={"Cache-Control": "no-cache"})

    @app.get("/assets/cyrene-fonts.css")
    async def cyrene_fonts(request: Request) -> Response:
        _require_authorized(request, material)
        try:
            fonts_path = _webui_static_path("fonts.css")
            css = fonts_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise HTTPException(status_code=404, detail="font styles unavailable") from exc
        for font_name in {
            "manrope-variable.woff2",
            "noto-sans-sc-variable.woff2",
            "ibm-plex-mono-regular.woff2",
            "ibm-plex-mono-medium.woff2",
            "ibm-plex-mono-semibold.woff2",
        }:
            css = css.replace(
                f'assets/fonts/{font_name}',
                f'/assets/fonts/{font_name}?token={material.secret}',
            )
        return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})

    @app.get("/appearance")
    async def appearance(request: Request) -> dict[str, Any]:
        _require_authorized(request, material)
        from cyrene.runtime.settings_service import read_public

        snapshot, runtime_snapshot = await asyncio.gather(
            asyncio.to_thread(read_public, "appearance"),
            asyncio.to_thread(read_public, "runtime"),
        )
        values = snapshot.get("values") or {}
        allowed = {"theme", "accent", "background_light", "background_dark", "text_size"}
        public_values = {key: values.get(key) for key in allowed}
        public_values["language"] = (runtime_snapshot.get("values") or {}).get("app_language") or ""
        return {
            "revision": snapshot.get("revision"),
            "values": public_values,
        }

    @app.get("/assets/fonts/{font_name}")
    async def font(font_name: str, request: Request) -> Response:
        _require_authorized(request, material)
        if font_name not in {
            "manrope-variable.woff2",
            "noto-sans-sc-variable.woff2",
            "ibm-plex-mono-regular.woff2",
            "ibm-plex-mono-medium.woff2",
            "ibm-plex-mono-semibold.woff2",
        }:
            raise HTTPException(status_code=404, detail="font unavailable")
        try:
            font_path = _webui_static_path("assets", "fonts", font_name)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="font unavailable") from exc
        if not font_path.is_file():
            raise HTTPException(status_code=404, detail="font unavailable")
        return FileResponse(
            font_path,
            media_type="font/woff2",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )


def _register_socket_route(app: FastAPI, material: OfficeGatewayFiles) -> None:
    @app.websocket("/ws")
    async def office_socket(websocket: WebSocket) -> None:
        token = str(websocket.query_params.get("token") or "")
        if not secrets.compare_digest(token, material.secret):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        session = None
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            if not isinstance(hello, dict) or hello.get("type") != "hello":
                await websocket.close(code=1008, reason="hello required")
                return
            session = await get_office_bridge().register(websocket, hello)
            await websocket.send_json({
                "type": "hello_ack",
                "sessionId": session.session_id,
                "revision": session.revision,
                "compatible": session.compatible,
                "agentKit": session.agent_kit,
                "error": None if session.compatible else {
                    "code": "addin_outdated",
                    "message": "The PowerPoint task pane build does not match the running Cyrene gateway.",
                },
            })
            while True:
                payload = await websocket.receive_json()
                if isinstance(payload, dict):
                    get_office_bridge().receive(session, payload)
        except (WebSocketDisconnect, TimeoutError):
            pass
        except OfficeBridgeError as exc:
            try:
                await websocket.send_json({"type": "fatal", "error": {"code": exc.code, "message": str(exc)}})
            except Exception:
                pass
        finally:
            if session is not None:
                await get_office_bridge().unregister(session.session_id, websocket)


def create_office_gateway_app(files: OfficeGatewayFiles | None = None) -> FastAPI:
    material = files or OfficeGatewayFiles()
    material.ensure()
    app = FastAPI(title="Cyrene Office Gateway", docs_url=None, redoc_url=None)
    _register_document_routes(app, material)
    _register_asset_routes(app, material)
    _register_socket_route(app, material)

    return app


class OfficeGatewayRuntime:
    def __init__(self) -> None:
        self.files = OfficeGatewayFiles()
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return bool(self.task is not None and not self.task.done() and self.server and self.server.started)

    async def start(self) -> None:
        if os.environ.get("CYRENE_OFFICE_ENABLED", "1").lower() in {"0", "false", "no"}:
            return
        if self.task is not None and not self.task.done():
            return
        self.files.ensure()
        config = uvicorn.Config(
            create_office_gateway_app(self.files),
            host="127.0.0.1",
            port=self.files.port,
            log_level="warning",
            lifespan="off",
            ssl_certfile=str(self.files.cert_path),
            ssl_keyfile=str(self.files.key_path),
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve(), name="cyrene-office-gateway")
        for _ in range(60):
            if self.server.started or self.task.done():
                break
            await asyncio.sleep(0.05)
        if self.task.done() and not self.server.started:
            try:
                await self.task
            except Exception:
                logger.warning("Office gateway failed to start", exc_info=True)

    async def stop(self) -> None:
        await get_office_bridge().close()
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self.task.cancel()
        self.task = None
        self.server = None

    def info(self) -> dict[str, Any]:
        self.files.ensure()
        return self.files.public_info(running=self.running)


_RUNTIME = OfficeGatewayRuntime()


def get_office_gateway_runtime() -> OfficeGatewayRuntime:
    return _RUNTIME


__all__ = ["OfficeGatewayFiles", "OfficeGatewayRuntime", "create_office_gateway_app", "get_office_gateway_runtime"]
