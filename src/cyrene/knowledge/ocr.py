"""Optional local PP-OCRv6 adapter and content-addressed OCR cache."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from cyrene.config import CACHE_DIR
from cyrene.config import INSTALL_RESOURCES_DIR
from cyrene.knowledge import local_models


MODEL_ID = "pp-ocrv6-medium"
OCR_CACHE = Path(CACHE_DIR) / "knowledge_ocr"
_ENGINE: Any = None
_LOCK = threading.Lock()
_INFERENCE_LIMIT = asyncio.Semaphore(2)


def reset_engine() -> None:
    global _ENGINE
    with _LOCK:
        _ENGINE = None


local_models.register_resetter(MODEL_ID, reset_engine)


def _load_engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    if not local_models.is_ready(MODEL_ID):
        raise RuntimeError("local OCR model is not downloaded")
    with _LOCK:
        if _ENGINE is not None:
            return _ENGINE
        try:
            from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR
        except ImportError as exc:
            raise RuntimeError("RapidOCR is unavailable") from exc
        root = local_models.model_dir(MODEL_ID)
        use_dml = "DmlExecutionProvider" in set(__import__("onnxruntime").get_available_providers())
        _ENGINE = RapidOCR(params={
            "EngineConfig.onnxruntime.use_dml": use_dml,
            "EngineConfig.onnxruntime.intra_op_num_threads": max(1, min(4, (os.cpu_count() or 2) // 2)),
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.model_type": ModelType.MEDIUM,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Det.model_path": str(root / "det.onnx"),
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.model_type": ModelType.MEDIUM,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Rec.model_path": str(root / "rec.onnx"),
            "Rec.rec_keys_path": str(root / "ppocrv6_dict.txt"),
        })
        return _ENGINE


def _is_windows_arm() -> bool:
    return sys.platform == "win32" and platform.machine().lower() in {"arm64", "aarch64"}


def _woa_x64_sidecar() -> Path | None:
    if not _is_windows_arm():
        return None
    override = os.environ.get("CYRENE_X64_OCR_SIDECAR", "").strip()
    candidates = [
        Path(override) if override else None,
        Path(INSTALL_RESOURCES_DIR) / "x64-sidecars" / "ocr" / "CyreneOcr.exe",
    ]
    return next((candidate for candidate in candidates if candidate and candidate.is_file()), None)


def _recognize_with_sidecar(image: Any, sidecar: Path) -> str:
    temporary_image: Path | None = None
    if not isinstance(image, (str, os.PathLike)):
        from PIL import Image

        if not isinstance(image, Image.Image):
            raise RuntimeError("WoA x64 OCR sidecar received an unsupported image")
        import tempfile

        handle, name = tempfile.mkstemp(prefix="cyrene-ocr-", suffix=".png")
        os.close(handle)
        temporary_image = Path(name)
        image.save(temporary_image, format="PNG")
    request = {
        "image": str((temporary_image or Path(image)).resolve()),
        "model_root": str(local_models.model_dir(MODEL_ID)),
    }
    try:
        completed = subprocess.run(
            [str(sidecar)],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        if temporary_image is not None:
            temporary_image.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "WoA OCR sidecar failed").strip())
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1] if lines else "{}")
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "WoA OCR sidecar failed"))
    return str(payload.get("text") or "")


def _recognize_sync(image: Any) -> str:
    sidecar = _woa_x64_sidecar()
    if sidecar is not None:
        return _recognize_with_sidecar(image, sidecar)
    if _is_windows_arm():
        raise RuntimeError("Windows ARM x64 OCR sidecar is missing")
    result = _load_engine()(image)
    texts = getattr(result, "txts", None) or []
    return "\n".join(str(text).strip() for text in texts if str(text).strip())


async def recognize(image: Any) -> str:
    async with _INFERENCE_LIMIT:
        return await asyncio.to_thread(_recognize_sync, image)


def read_cache(content_hash: str) -> dict[str, Any] | None:
    if not content_hash:
        return None
    path = OCR_CACHE / f"{content_hash}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def write_cache(content_hash: str, pages: list[str]) -> None:
    if not content_hash:
        return
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    path = OCR_CACHE / f"{content_hash}.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "model": MODEL_ID, "pages": pages}, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
