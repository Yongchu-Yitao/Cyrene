"""Knowledge-owned Zotero and local-model settings routes."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request

from cyrene.localization import localized
from cyrene.workbench.http.errors import localized_error_response

from . import zotero_settings

logger = logging.getLogger(__name__)


def _download_error(raw: Any, *, ocr: bool = False) -> tuple[str, str]:
    lower = str(raw or "").lower()
    if "checksum" in lower or "sha256" in lower or "validation failed" in lower:
        return (
            localized(
                "Downloaded file integrity check failed. Retry the download.",
                "下载文件完整性校验失败，请重试。",
            ),
            "download_integrity_failed",
        )
    if "archive" in lower or "extract" in lower:
        return (
            localized(
                "Downloaded archive is invalid. Retry the download.",
                "下载的压缩包无效，请重试。",
            ),
            "download_extract_failed",
        )
    return (
        localized(
            "OCR runtime download failed. Retry the download."
            if ocr
            else "Local model download failed. Retry the download.",
            "OCR 运行时下载失败，请重试。"
            if ocr
            else "本地模型下载失败，请重试。",
        ),
        "ocr_runtime_download_failed" if ocr else "local_model_download_failed",
    )


def _public_local_model_status(value: Any) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, dict) else {}
    models: list[dict[str, Any]] = []
    for raw_model in payload.get("models") or []:
        if not isinstance(raw_model, dict):
            continue
        model = dict(raw_model)
        if model.get("error"):
            model["error"], model["error_code"] = _download_error(model["error"])
        models.append(model)
    if "models" in payload:
        payload["models"] = models
    runtime = payload.get("cv2_runtime")
    if isinstance(runtime, dict):
        runtime = dict(runtime)
        if runtime.get("error"):
            runtime["error"], runtime["error_code"] = _download_error(
                runtime["error"], ocr=True
            )
        payload["cv2_runtime"] = runtime
    if payload.get("error"):
        payload["error"], payload["error_code"] = _download_error(
            payload["error"], ocr=True
        )
    return payload


async def _object_body(request: Request) -> dict[str, Any] | None:
    try:
        value = await request.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def register_settings_routes(router: APIRouter, service: Any) -> None:
    @router.get("/api/settings/integrations")
    async def api_get_integration_settings():
        return zotero_settings.public_settings()

    @router.put("/api/settings/integrations")
    async def api_update_integration_settings(request: Request):
        body = await _object_body(request)
        if body is None:
            return localized_error_response(
                "integration settings must be an object",
                "集成设置必须是对象。",
                400,
                "invalid_integration_settings",
            )
        try:
            return {"ok": True, **zotero_settings.update_settings(body)}
        except (TypeError, ValueError):
            logger.info("Invalid Zotero settings", exc_info=True)
            return localized_error_response(
                "Invalid integration settings.",
                "集成设置无效。",
                400,
                "invalid_integration_settings",
            )

    @router.post("/api/settings/integrations/test")
    async def api_test_integration(request: Request):
        body = await _object_body(request)
        if body is None:
            return localized_error_response(
                "integration test request must be an object",
                "集成测试请求必须是对象。",
                400,
                "invalid_integration_test",
            )
        try:
            name = str(body.get("service") or "").strip().lower()
            config = zotero_settings.merged_test_config(name, body.get("config"))
            return await zotero_settings.test_zotero(config)
        except (TypeError, ValueError):
            return localized_error_response(
                "Invalid integration test request.",
                "集成测试请求无效。",
                400,
                "invalid_integration_test",
            )
        except httpx.HTTPStatusError:
            logger.info("Zotero returned an HTTP error", exc_info=True)
            return localized_error_response(
                "The integration returned an error.",
                "集成服务返回错误。",
                502,
                "integration_test_failed",
            )
        except httpx.RequestError:
            logger.info("Zotero is unreachable", exc_info=True)
            return localized_error_response(
                "Could not reach the configured integration.",
                "无法连接已配置的集成服务。",
                503,
                "integration_test_failed",
            )
        except Exception:
            logger.info("Zotero integration test failed", exc_info=True)
            return localized_error_response(
                "The integration returned an invalid response.",
                "集成服务返回了无效响应。",
                502,
                "integration_test_failed",
            )

    @router.get("/api/settings/local-models/status")
    async def api_local_models_status():
        return _public_local_model_status(service.local_model_status())

    @router.post("/api/settings/local-models/ocr-runtime/download")
    async def api_download_ocr_runtime():
        from . import opencv_runtime

        try:
            return _public_local_model_status(
                {"ok": True, **opencv_runtime.start_download()}
            )
        except Exception:
            logger.info("OCR runtime download could not start", exc_info=True)
            return localized_error_response(
                "OCR runtime download could not be started.",
                "无法启动 OCR 运行时下载。",
                503,
                "ocr_runtime_download_failed",
            )

    @router.post("/api/settings/local-models/{model_id}/download")
    async def api_download_local_model(model_id: str):
        try:
            return _public_local_model_status(
                {"ok": True, **service.start_local_model_download(model_id)}
            )
        except ValueError:
            return localized_error_response(
                "Local model was not found.",
                "未找到本地模型。",
                404,
                "local_model_not_found",
            )
        except Exception:
            logger.info("Local model download could not start", exc_info=True)
            return localized_error_response(
                "Local model download could not be started.",
                "无法启动本地模型下载。",
                503,
                "local_model_download_failed",
            )

    @router.delete("/api/settings/local-models/{model_id}")
    async def api_delete_local_model(model_id: str):
        try:
            return _public_local_model_status(
                {"ok": True, **(await service.delete_local_model(model_id))}
            )
        except ValueError:
            return localized_error_response(
                "Local model was not found.",
                "未找到本地模型。",
                404,
                "local_model_not_found",
            )
        except Exception:
            logger.info("Local model could not be deleted", exc_info=True)
            return localized_error_response(
                "Local model could not be deleted.",
                "无法删除本地模型。",
                503,
                "local_model_delete_failed",
            )


__all__ = ["register_settings_routes"]
