"""Application composition for the Remote Desktop PluginPack."""

from __future__ import annotations

import base64
from typing import Any

from cyrene.plugins.context import PluginApplicationContext

from .routes import register_routes
from .service import RemoteDesktopService


def _object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def setup_application(context: PluginApplicationContext) -> None:
    remote = context.services.get("remote")
    if remote is None:
        raise RuntimeError("cyrene_remote_desktop requires the cyrene_remote PluginPack")
    service = RemoteDesktopService(
        context.db_path,
        context.data_directory,
        remote_service=remote,
    )
    context.provide("remote_desktop", service)
    context.expose_frontend("remote_desktop")
    register_routes(context.router, service)

    async def cards_list(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.cards()

    async def prepare(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.prepare(str(_object(arguments).get("device_id") or ""))

    async def connect(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.connect(_object(arguments))

    async def reconnect(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        return await service.reconnect(str(values.get("session_id") or ""), dict(values.get("offer") or {}))

    async def disconnect(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.disconnect(str(_object(arguments).get("session_id") or ""))

    async def session_get(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return service.get_session(str(_object(arguments).get("session_id") or ""))

    async def displays(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.displays(str(_object(arguments).get("session_id") or ""))

    async def display_select(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        return await service.select_display(str(values.get("session_id") or ""), str(values.get("display_id") or ""))

    async def quality_set(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        return await service.set_quality(str(values.get("session_id") or ""), str(values.get("quality_mode") or ""))

    async def microphone_set(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        return await service.set_microphone(str(values.get("session_id") or ""), bool(values.get("enabled")))

    async def security_get(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.security_state(
            str(_object(arguments).get("session_id") or "")
        )

    async def credentials_request(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.request_credentials(str(_object(arguments).get("session_id") or ""))

    async def clipboard_image_send(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        encoded = str(values.get("png_base64") or "")
        if not encoded or len(encoded) > 48 * 1024 * 1024:
            raise ValueError("desktop clipboard image payload is invalid")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("desktop clipboard image payload is invalid") from exc
        return await service.send_clipboard_image(
            str(values.get("session_id") or ""),
            raw,
        )

    async def clipboard_image_receive(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        return await service.receive_clipboard_image(
            str(values.get("session_id") or ""),
            str(values.get("offer_id") or ""),
            expected_size=max(0, int(values.get("size") or 0)),
            expected_sha256=str(values.get("sha256") or ""),
        )

    async def clipboard_files_send(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        raw_entries = values.get("entries") if isinstance(values.get("entries"), list) else []
        if not raw_entries or len(raw_entries) > 512:
            raise ValueError("desktop clipboard file manifest is invalid")
        entries: list[dict[str, Any]] = []
        encoded_total = 0
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise ValueError("desktop clipboard file manifest is invalid")
            encoded = str(raw.get("content_base64") or "")
            encoded_total += len(encoded)
            if encoded_total > 90 * 1024 * 1024:
                raise ValueError("desktop clipboard file payload is too large")
            try:
                data = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ValueError("desktop clipboard file payload is invalid") from exc
            entries.append(
                {
                    "relative_path": str(raw.get("relative_path") or ""),
                    "data": data,
                }
            )
        return await service.send_clipboard_files(
            str(values.get("session_id") or ""),
            entries,
        )

    async def clipboard_files_upload_begin(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        entries = values.get("entries") if isinstance(values.get("entries"), list) else []
        return service.begin_local_clipboard_files(
            str(values.get("session_id") or ""), entries
        )

    async def clipboard_files_upload_chunk(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        encoded = str(values.get("content_base64") or "")
        if not encoded or len(encoded) > 384 * 1024:
            raise ValueError("desktop clipboard file chunk is invalid")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("desktop clipboard file chunk is invalid") from exc
        return service.append_local_clipboard_file(
            str(values.get("upload_id") or ""),
            str(values.get("relative_path") or ""),
            int(values.get("offset") or 0),
            raw,
            str(values.get("chunk_sha256") or ""),
        )

    async def clipboard_files_upload_commit(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.commit_local_clipboard_files(
            str(_object(arguments).get("upload_id") or "")
        )

    async def clipboard_files_upload_abort(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return service.abort_local_clipboard_files(
            str(_object(arguments).get("upload_id") or "")
        )

    async def clipboard_files_receive(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        return await service.receive_clipboard_files(
            str(values.get("session_id") or ""),
            str(values.get("offer_id") or ""),
        )

    async def observations(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.pending_observations(str(_object(arguments).get("session_id") or ""))

    async def observation_submit(arguments: Any, _metadata: Any) -> dict[str, Any]:
        values = _object(arguments)
        encoded = str(values.get("png_base64") or "")
        if len(encoded) > 24 * 1024 * 1024:
            raise ValueError("desktop snapshot payload is too large")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("desktop snapshot payload is invalid") from exc
        return await service.submit_observation_frame(
            str(values.get("observation_id") or ""),
            raw,
        )

    async def layout_project(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.project_layout(_object(arguments))

    async def diagnostics(arguments: Any, _metadata: Any) -> dict[str, Any]:
        return await service.diagnostics(str(_object(arguments).get("device_id") or ""))

    for name, handler in {
        "remoteDesktop.cards.list": cards_list,
        "remoteDesktop.session.prepare": prepare,
        "remoteDesktop.session.connect": connect,
        "remoteDesktop.session.reconnect": reconnect,
        "remoteDesktop.session.disconnect": disconnect,
        "remoteDesktop.session.get": session_get,
        "remoteDesktop.display.list": displays,
        "remoteDesktop.display.select": display_select,
        "remoteDesktop.quality.set": quality_set,
        "remoteDesktop.microphone.set": microphone_set,
        "remoteDesktop.security.get": security_get,
        "remoteDesktop.credentials.request": credentials_request,
        "remoteDesktop.clipboard.image.send": clipboard_image_send,
        "remoteDesktop.clipboard.image.receive": clipboard_image_receive,
        "remoteDesktop.clipboard.files.send": clipboard_files_send,
        "remoteDesktop.clipboard.files.upload.begin": clipboard_files_upload_begin,
        "remoteDesktop.clipboard.files.upload.chunk": clipboard_files_upload_chunk,
        "remoteDesktop.clipboard.files.upload.commit": clipboard_files_upload_commit,
        "remoteDesktop.clipboard.files.upload.abort": clipboard_files_upload_abort,
        "remoteDesktop.clipboard.files.receive": clipboard_files_receive,
        "remoteDesktop.observations.list": observations,
        "remoteDesktop.observation.submit": observation_submit,
        "remoteDesktop.layout.project": layout_project,
        "remoteDesktop.diagnostics": diagnostics,
    }.items():
        context.provide_frontend_method(name, handler)

    context.on_startup(service.start)
    context.on_shutdown(service.stop)


__all__ = ["setup_application"]
