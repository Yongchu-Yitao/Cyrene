"""User-managed local inference model packs.

Models are never downloaded implicitly.  Downloads are resumable, validated,
and become visible to inference only after an atomic ready marker is written.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import httpx

from cyrene.config import CACHE_DIR


MODEL_ROOT = Path(CACHE_DIR) / "knowledge_models"

MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "qwen3-embedding-0.6b": {
        "name": "Qwen3 Embedding 0.6B (INT8)",
        "kind": "embedding",
        "description": "1024-dimensional multilingual local embeddings",
        "dimensions": 1024,
        "files": [
            {
                "path": "tokenizer.json",
                "sources": [
                    {"url": "https://www.modelscope.cn/models/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/master/tokenizer.json", "resume_key": "qwen-tokenizer"},
                    {"url": "https://hf-mirror.com/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/tokenizer.json", "resume_key": "qwen-tokenizer"},
                    {"url": "https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/tokenizer.json", "resume_key": "qwen-tokenizer"},
                ],
                "min_bytes": 10_000_000,
            },
            {
                "path": "config.json",
                "sources": [
                    {"url": "https://www.modelscope.cn/models/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/master/config.json", "resume_key": "qwen-config"},
                    {"url": "https://hf-mirror.com/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/config.json", "resume_key": "qwen-config"},
                    {"url": "https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/config.json", "resume_key": "qwen-config"},
                ],
                "min_bytes": 1_000,
            },
            {
                "path": "model.onnx",
                "sources": [
                    {"url": "https://www.modelscope.cn/models/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/master/onnx/model_int8.onnx", "resume_key": "qwen-int8"},
                    {"url": "https://hf-mirror.com/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/onnx/model_int8.onnx", "resume_key": "qwen-int8"},
                    {"url": "https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX/resolve/main/onnx/model_int8.onnx", "resume_key": "qwen-int8"},
                ],
                "min_bytes": 600_000_000,
            },
        ],
    },
    "pp-ocrv6-medium": {
        "name": "PP-OCRv6 Medium",
        "kind": "ocr",
        "description": "Local Chinese, English, Japanese and Latin-script OCR",
        "files": [
            {
                "path": "det.onnx",
                "sources": [
                    {"url": "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/det/PP-OCRv6_det_medium.onnx", "resume_key": "rapidocr-det", "sha256": "92078b7355007ccfffcd4c8cd441a3afd4538904d06881b29a155e1e679907c2"},
                    {"url": "https://hf-mirror.com/PaddlePaddle/PP-OCRv6_medium_det_onnx/resolve/main/inference.onnx", "resume_key": "paddle-det", "sha256": "eb13b44b25bb36f89528b68720af8a61d9cf381176107f465db1757b65d086e1"},
                    {"url": "https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx/resolve/main/inference.onnx", "resume_key": "paddle-det", "sha256": "eb13b44b25bb36f89528b68720af8a61d9cf381176107f465db1757b65d086e1"},
                ],
                "min_bytes": 50_000_000,
                "sha256": ["92078b7355007ccfffcd4c8cd441a3afd4538904d06881b29a155e1e679907c2", "eb13b44b25bb36f89528b68720af8a61d9cf381176107f465db1757b65d086e1"],
            },
            {
                "path": "rec.onnx",
                "sources": [
                    {"url": "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_medium.onnx", "resume_key": "rapidocr-rec", "sha256": "eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b"},
                    {"url": "https://hf-mirror.com/PaddlePaddle/PP-OCRv6_medium_rec_onnx/resolve/main/inference.onnx", "resume_key": "paddle-rec", "sha256": "9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba"},
                    {"url": "https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx/resolve/main/inference.onnx", "resume_key": "paddle-rec", "sha256": "9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba"},
                ],
                "min_bytes": 65_000_000,
                "sha256": ["eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b", "9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba"],
            },
            {
                "path": "ppocrv6_dict.txt",
                "sources": [
                    {"url": "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/paddle/PP-OCRv6/rec/PP-OCRv6_rec_medium/ppocrv6_dict.txt", "resume_key": "ppocrv6-dict"},
                    {"url": "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/ppocr/utils/dict/ppocrv6_dict.txt", "resume_key": "ppocrv6-dict"},
                ],
                "min_bytes": 10_000,
            },
        ],
    },
}

# The same public model identity can have hardware-specific packs. Apple
# Silicon uses native MLX weights; CUDA-capable hosts use the ONNX pack and let
# ONNX Runtime select CUDA, with CPU as the portable fallback.
if sys.platform == "darwin" and platform.machine().lower() == "arm64":
    _qwen = MODEL_CATALOG["qwen3-embedding-0.6b"]
    _qwen["name"] = "Qwen3 Embedding 0.6B (MLX 8-bit)"
    _qwen["runtime"] = "mlx"
    _qwen["files"] = _qwen["files"][:2] + [
        {
            "path": "mlx/config.json",
            "sources": [
                {"url": "https://hf-mirror.com/mlx-community/Qwen3-Embedding-0.6B-8bit/resolve/main/config.json", "resume_key": "qwen-mlx-config"},
                {"url": "https://huggingface.co/mlx-community/Qwen3-Embedding-0.6B-8bit/resolve/main/config.json", "resume_key": "qwen-mlx-config"},
            ],
            "min_bytes": 800,
        },
        {
            "path": "mlx/model.safetensors",
            "sources": [
                {"url": "https://hf-mirror.com/mlx-community/Qwen3-Embedding-0.6B-8bit/resolve/main/model.safetensors", "resume_key": "qwen-mlx-8bit", "sha256": "fe956e8d346b4f08215a3cfc48a874354f900c20a59e965b75df0d9d77c54b28"},
                {"url": "https://huggingface.co/mlx-community/Qwen3-Embedding-0.6B-8bit/resolve/main/model.safetensors", "resume_key": "qwen-mlx-8bit", "sha256": "fe956e8d346b4f08215a3cfc48a874354f900c20a59e965b75df0d9d77c54b28"},
            ],
            "min_bytes": 630_000_000,
            "sha256": "fe956e8d346b4f08215a3cfc48a874354f900c20a59e965b75df0d9d77c54b28",
        },
    ]
else:
    MODEL_CATALOG["qwen3-embedding-0.6b"]["runtime"] = "onnx"

_TASKS: dict[str, asyncio.Task] = {}
_PROGRESS: dict[str, dict[str, Any]] = {}
_VALIDATED: set[str] = set()
_RESETTERS: dict[str, Callable[[], None]] = {}


def register_resetter(model_id: str, resetter: Callable[[], None]) -> None:
    """Register cleanup for an inference adapter that has been loaded."""
    if model_id not in MODEL_CATALOG:
        raise ValueError("unknown local model")
    _RESETTERS[model_id] = resetter


def model_dir(model_id: str) -> Path:
    if model_id not in MODEL_CATALOG:
        raise ValueError("unknown local model")
    return MODEL_ROOT / model_id


def _file_valid(path: Path, spec: dict[str, Any]) -> bool:
    try:
        if path.stat().st_size < int(spec.get("min_bytes") or 1):
            return False
        expected_raw = spec.get("sha256") or []
        expected = {str(value) for value in (expected_raw if isinstance(expected_raw, list) else [expected_raw]) if value}
        if expected:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest() in expected
        return True
    except OSError:
        return False


def is_ready(model_id: str) -> bool:
    spec = MODEL_CATALOG.get(model_id)
    if not spec:
        return False
    root = model_dir(model_id)
    if not (root / ".ready.json").is_file():
        _VALIDATED.discard(model_id)
        return False
    if model_id in _VALIDATED:
        return all(
            (root / item["path"]).is_file()
            and (root / item["path"]).stat().st_size >= int(item.get("min_bytes") or 1)
            for item in spec["files"]
        )
    valid = all(_file_valid(root / item["path"], item) for item in spec["files"])
    if valid:
        _VALIDATED.add(model_id)
    return valid


def status() -> dict[str, Any]:
    models = []
    for model_id, spec in MODEL_CATALOG.items():
        progress = _PROGRESS.get(model_id, {})
        task = _TASKS.get(model_id)
        runtime = str(spec.get("runtime") or "onnx")
        if model_id == "qwen3-embedding-0.6b" and runtime == "onnx":
            try:
                import onnxruntime as ort

                runtime = (
                    "cuda"
                    if "CUDAExecutionProvider" in ort.get_available_providers()
                    else "onnx-cpu"
                )
            except Exception:
                runtime = "onnx-cpu"
        models.append({
            "id": model_id,
            "name": spec["name"],
            "kind": spec["kind"],
            "description": spec["description"],
            "dimensions": int(spec.get("dimensions") or 0),
            "runtime": runtime,
            "ready": is_ready(model_id),
            "downloading": bool(task and not task.done()),
            "downloaded_bytes": int(progress.get("downloaded_bytes") or 0),
            "total_bytes": int(progress.get("total_bytes") or 0),
            "error": str(progress.get("error") or ""),
        })
    return {"models": models}


async def _download_file(client: httpx.AsyncClient, item: dict[str, Any], destination: Path, model_id: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    source_marker = part.with_suffix(part.suffix + ".source")
    sources = item.get("sources") or [{"url": item.get("url"), "resume_key": item["path"]}]
    errors: list[str] = []
    for source in sources:
        resume_key = str(source.get("resume_key") or source["url"])
        previous_key = source_marker.read_text(encoding="utf-8") if source_marker.exists() else ""
        if part.exists() and previous_key and previous_key != resume_key:
            part.unlink(missing_ok=True)
        source_marker.write_text(resume_key, encoding="utf-8")
        existing = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        _PROGRESS[model_id]["source"] = source["url"].split("/", 3)[2]
        try:
            async with client.stream("GET", source["url"], headers=headers, follow_redirects=True, timeout=None) as response:
                if existing and response.status_code != 206:
                    existing = 0
                    part.unlink(missing_ok=True)
                response.raise_for_status()
                content_length = int(response.headers.get("content-length") or 0)
                progress = _PROGRESS[model_id]
                progress["total_bytes"] += existing + content_length
                progress["downloaded_bytes"] += existing
                mode = "ab" if existing else "wb"
                with part.open(mode) as handle:
                    async for block in response.aiter_bytes(1024 * 1024):
                        handle.write(block)
                        progress["downloaded_bytes"] += len(block)
            validation = {**item, "sha256": source.get("sha256") or item.get("sha256")}
            if not _file_valid(part, validation):
                raise RuntimeError("checksum or size validation failed")
            os.replace(part, destination)
            source_marker.unlink(missing_ok=True)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(f"{_PROGRESS[model_id]['source']}: {exc}")
    raise RuntimeError(f"all mirrors failed for {item['path']}: {'; '.join(errors)}")


async def _download(model_id: str) -> None:
    spec = MODEL_CATALOG[model_id]
    root = model_dir(model_id)
    root.mkdir(parents=True, exist_ok=True)
    _PROGRESS[model_id] = {"downloaded_bytes": 0, "total_bytes": 0, "error": ""}
    try:
        async with httpx.AsyncClient() as client:
            for item in spec["files"]:
                destination = root / item["path"]
                if _file_valid(destination, item):
                    size = destination.stat().st_size
                    _PROGRESS[model_id]["downloaded_bytes"] += size
                    _PROGRESS[model_id]["total_bytes"] += size
                    continue
                await _download_file(client, item, destination, model_id)
        marker = root / ".ready.json"
        marker.write_text(json.dumps({"id": model_id, "version": 1}), encoding="utf-8")
        _VALIDATED.add(model_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _PROGRESS[model_id]["error"] = str(exc)
        raise


def start_download(model_id: str) -> dict[str, Any]:
    if model_id not in MODEL_CATALOG:
        raise ValueError("unknown local model")
    task = _TASKS.get(model_id)
    if task and not task.done():
        return status()
    if is_ready(model_id):
        return status()
    task = asyncio.create_task(_download(model_id))
    task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
    _TASKS[model_id] = task
    return status()


async def delete_model(model_id: str) -> dict[str, Any]:
    if model_id not in MODEL_CATALOG:
        raise ValueError("unknown local model")
    task = _TASKS.get(model_id)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    resetter = _RESETTERS.get(model_id)
    if resetter is not None:
        resetter()
    await asyncio.to_thread(shutil.rmtree, model_dir(model_id), True)
    _PROGRESS.pop(model_id, None)
    _VALIDATED.discard(model_id)
    return status()
