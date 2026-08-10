"""Optional local PP-OCRv6 adapter and content-addressed OCR cache."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from cyrene.config import CACHE_DIR
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
        _ENGINE = RapidOCR(params={
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


def _recognize_sync(image: Any) -> str:
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
