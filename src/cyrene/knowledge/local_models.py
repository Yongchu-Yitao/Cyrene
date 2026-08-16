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
import tarfile
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from cyrene.config import CACHE_DIR
from cyrene.model_runtime import opencv_runtime


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
    "fireredasr2-aed-int8": {
        "name": "FireRedASR2 AED (INT8)",
        "kind": "asr",
        "description": "Chinese, English and 20+ Chinese dialects with local punctuation and VAD",
        "runtime": "sherpa-onnx",
        "download_bytes": 903_950_678,
        "files": [
            {
                "path": ".downloads/fireredasr2-aed-int8.tar.bz2",
                "sources": [
                    {"url": "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26.tar.bz2", "resume_key": "fireredasr2-aed-int8"},
                    {"url": "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26.tar.bz2", "resume_key": "fireredasr2-aed-int8"},
                    {"url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26.tar.bz2", "resume_key": "fireredasr2-aed-int8"},
                ],
                "min_bytes": 838_000_000,
                "download_bytes": 838_589_068,
                "sha256": "43015b3f1643a5688b4821e8ed323473d38b798c4ec291471fe00df1bcfc4f1c",
                "extract": {
                    "root": "sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26",
                    "outputs": [
                        {"source": "encoder.int8.onnx", "path": "encoder.int8.onnx", "min_bytes": 700_000_000},
                        {"source": "decoder.int8.onnx", "path": "decoder.int8.onnx", "min_bytes": 350_000_000},
                        {"source": "tokens.txt", "path": "tokens.txt", "min_bytes": 50_000},
                    ],
                },
            },
            {
                "path": ".downloads/punctuation-int8.tar.bz2",
                "sources": [
                    {"url": "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2", "resume_key": "sherpa-punctuation-int8"},
                    {"url": "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2", "resume_key": "sherpa-punctuation-int8"},
                    {"url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2", "resume_key": "sherpa-punctuation-int8"},
                ],
                "min_bytes": 64_000_000,
                "download_bytes": 64_717_756,
                "sha256": "c0d5aa5f8eeb686032345e180bedf39319dc2e0556781c6264bcadba8328a6e1",
                "extract": {
                    "root": "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8",
                    "outputs": [
                        {"source": "model.int8.onnx", "path": "punctuation.int8.onnx", "min_bytes": 60_000_000},
                    ],
                },
            },
            {
                "path": "silero_vad.onnx",
                "sources": [
                    {"url": "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx", "resume_key": "silero-vad-v5"},
                    {"url": "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx", "resume_key": "silero-vad-v5"},
                    {"url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx", "resume_key": "silero-vad-v5"},
                ],
                "min_bytes": 600_000,
                "download_bytes": 643_854,
                "sha256": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
            },
        ],
    },
    "kokoro-zh-en": {
        "name": "Kokoro 82M Chinese-English (FP32)",
        "kind": "tts",
        "description": "Natural Chinese-English preset speech with 103 bundled voices",
        "runtime": "sherpa-onnx",
        "download_bytes": 364_816_464,
        "files": [
            {
                "path": ".downloads/kokoro-multi-lang-v1_1.tar.bz2",
                "sources": [
                    {"url": "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_1.tar.bz2", "resume_key": "kokoro-multi-lang-v1_1"},
                    {"url": "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_1.tar.bz2", "resume_key": "kokoro-multi-lang-v1_1"},
                    {"url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_1.tar.bz2", "resume_key": "kokoro-multi-lang-v1_1"},
                ],
                "min_bytes": 360_000_000,
                "download_bytes": 364_816_464,
                "sha256": "a3f4c73d043860e3fd2e5b06f36795eb81de0fc8e8de6df703245edddd87dbad",
                "extract": {
                    "root": "kokoro-multi-lang-v1_1",
                    "outputs": [
                        {"source": "model.onnx", "path": "model.onnx", "min_bytes": 300_000_000},
                        {"source": "voices.bin", "path": "voices.bin", "min_bytes": 20_000_000},
                        {"source": "tokens.txt", "path": "tokens.txt", "min_bytes": 500},
                        {"source": "lexicon-us-en.txt", "path": "lexicon-us-en.txt", "min_bytes": 1_000_000},
                        {"source": "lexicon-zh.txt", "path": "lexicon-zh.txt", "min_bytes": 1_000_000},
                        {"source": "espeak-ng-data", "path": "espeak-ng-data", "type": "dir"},
                    ],
                },
            },
        ],
    },
    "zipvoice-zh-en": {
        "name": "ZipVoice Distill Chinese-English (FP32)",
        "kind": "tts",
        "description": "Local Chinese-English custom voice cloning without INT8 quantization",
        "runtime": "sherpa-onnx",
        "download_bytes": 531_957_872,
        "obsolete_paths": [
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "preset.wav",
            ".downloads/zipvoice-zh-en.tar.bz2.part",
            ".downloads/zipvoice-zh-en.tar.bz2.part.source",
        ],
        "files": [
            {
                "path": ".downloads/zipvoice-distill-fp32.tar.bz2",
                "sources": [
                    {"url": "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-zipvoice-distill-fp32-zh-en-emilia.tar.bz2", "resume_key": "zipvoice-distill-fp32"},
                    {"url": "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-zipvoice-distill-fp32-zh-en-emilia.tar.bz2", "resume_key": "zipvoice-distill-fp32"},
                    {"url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-zipvoice-distill-fp32-zh-en-emilia.tar.bz2", "resume_key": "zipvoice-distill-fp32"},
                ],
                "min_bytes": 477_000_000,
                "download_bytes": 477_800_463,
                "sha256": "3b6729d03bf4ba64deeec113048bdbbe55dbc30580b609be76342e0099fd23a8",
                "extract": {
                    "root": "sherpa-onnx-zipvoice-distill-fp32-zh-en-emilia",
                    "outputs": [
                        {"source": "encoder.onnx", "path": "encoder.onnx", "min_bytes": 17_000_000},
                        {"source": "decoder.onnx", "path": "decoder.onnx", "min_bytes": 450_000_000},
                        {"source": "tokens.txt", "path": "tokens.txt", "min_bytes": 1_000},
                        {"source": "lexicon.txt", "path": "lexicon.txt", "min_bytes": 1_000_000},
                        {"source": "test_wavs/leijun-1.wav", "path": "preset-default.wav", "min_bytes": 100_000},
                        {"source": "espeak-ng-data", "path": "espeak-ng-data", "type": "dir"},
                    ],
                },
            },
            {
                "path": "vocos_24khz.onnx",
                "sources": [
                    {"url": "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos_24khz.onnx", "resume_key": "vocos-24khz"},
                    {"url": "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos_24khz.onnx", "resume_key": "vocos-24khz"},
                    {"url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos_24khz.onnx", "resume_key": "vocos-24khz"},
                ],
                "min_bytes": 54_000_000,
                "download_bytes": 54_157_409,
                "sha256": "bcb3b970e384161c4d634f0bb9e999ff1c471b34c9bc0b1049a5014065ed3cc0",
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
_QNN_REGISTERED = False


def _register_qnn_plugin(ort: Any) -> bool:
    global _QNN_REGISTERED
    if _QNN_REGISTERED:
        return True
    try:
        import onnxruntime_qnn as qnn

        ort.register_execution_provider_library(
            "QNNExecutionProvider", qnn.get_library_path()
        )
        _QNN_REGISTERED = True
        return True
    except Exception:
        return False


def configure_qnn_session_options(options: Any, ort: Any) -> bool:
    """Attach the WoA QNN HTP/NPU plugin to an ORT SessionOptions object."""
    if not (
        sys.platform == "win32"
        and platform.machine().lower() in {"arm64", "aarch64"}
        and _register_qnn_plugin(ort)
    ):
        return False
    try:
        import onnxruntime_qnn as qnn

        devices = [
            device for device in ort.get_ep_devices()
            if device.ep_name == "QNNExecutionProvider"
        ]
        npu_devices = [
            device for device in devices
            if "NPU" in str(getattr(getattr(device, "device", None), "type", "")).upper()
        ]
        selected = npu_devices or devices
        if not selected:
            return False
        options.add_provider_for_devices(selected, {
            "backend_path": qnn.get_qnn_htp_path(),
            "enable_htp_fp16_precision": "1",
        })
        return True
    except Exception:
        return False


def onnx_execution_providers() -> list[Any]:
    """Return the fastest safe ONNX Runtime providers for this host.

    Windows ARM prefers Qualcomm's QNN HTP/NPU provider when the packaged
    runtime exposes it. CUDA is preferred when its runtime is available;
    DirectML and CPU remain safe fallbacks.
    """
    try:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
    except Exception:
        available = set()

    providers: list[Any] = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "DmlExecutionProvider" in available:
        providers.append("DmlExecutionProvider")
    if "CoreMLExecutionProvider" in available:
        providers.append("CoreMLExecutionProvider")
    providers.append("CPUExecutionProvider")
    return providers


def sherpa_provider(model_id: str = "") -> str:
    """Return the fastest compatible sherpa-onnx provider for a model."""
    try:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
    except Exception:
        available = set()
    if "CUDAExecutionProvider" in available:
        return "cuda"
    if (
        model_id == "zipvoice-zh-en"
        and sys.platform == "darwin"
        and platform.machine().lower() == "arm64"
        and "CoreMLExecutionProvider" in available
    ):
        return "coreml"
    return "cpu"


def sherpa_runtime(model_id: str = "") -> str:
    provider = sherpa_provider(model_id)
    return provider if provider != "cpu" else "onnx-cpu"


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


def _output_valid(root: Path, spec: dict[str, Any]) -> bool:
    path = root / str(spec["path"])
    if spec.get("type") == "dir":
        try:
            return path.is_dir() and any(path.iterdir())
        except OSError:
            return False
    return _file_valid(path, spec)


def _item_ready(root: Path, item: dict[str, Any]) -> bool:
    extract = item.get("extract")
    if isinstance(extract, dict):
        outputs = extract.get("outputs") or []
        return bool(outputs) and all(_output_valid(root, output) for output in outputs)
    return _file_valid(root / item["path"], item)


def _extract_archive(archive: Path, root: Path, item: dict[str, Any]) -> None:
    """Safely unpack a validated tar bundle and publish only declared outputs."""
    extract = item.get("extract") or {}
    outputs = extract.get("outputs") or []
    if not outputs:
        raise RuntimeError("archive has no declared outputs")
    staging = root / f".extract-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(archive, mode="r:bz2") as bundle:
            bundle.extractall(staging, filter="data")
        source_root = staging / str(extract.get("root") or "")
        for output in outputs:
            source = source_root / str(output.get("source") or output["path"])
            if not _output_valid(source.parent, {**output, "path": source.name}):
                raise RuntimeError(f"archive output is missing or invalid: {output['path']}")
            destination = root / str(output["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink(missing_ok=True)
            os.replace(source, destination)
        if not all(_output_valid(root, output) for output in outputs):
            raise RuntimeError("extracted model validation failed")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _remove_obsolete_paths(root: Path, spec: dict[str, Any]) -> None:
    for value in spec.get("obsolete_paths") or []:
        relative = Path(str(value))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("invalid obsolete model path")
        target = root / relative
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)


def is_ready(model_id: str) -> bool:
    spec = MODEL_CATALOG.get(model_id)
    if not spec:
        return False
    root = model_dir(model_id)
    if not (root / ".ready.json").is_file():
        _VALIDATED.discard(model_id)
        return False
    if model_id in _VALIDATED:
        return all(_item_ready(root, item) for item in spec["files"])
    valid = all(_item_ready(root, item) for item in spec["files"])
    if valid:
        _VALIDATED.add(model_id)
    return valid


def status() -> dict[str, Any]:
    models = []
    for model_id, spec in MODEL_CATALOG.items():
        progress = _PROGRESS.get(model_id, {})
        task = _TASKS.get(model_id)
        runtime = str(spec.get("runtime") or "onnx")
        if runtime == "sherpa-onnx":
            runtime = sherpa_runtime(model_id)
        elif model_id == "qwen3-embedding-0.6b" and runtime == "onnx":
            try:
                import onnxruntime as ort

                available = set(ort.get_available_providers())
                if (
                    sys.platform == "win32"
                    and platform.machine().lower() in {"arm64", "aarch64"}
                    and _register_qnn_plugin(ort)
                    and any(device.ep_name == "QNNExecutionProvider" for device in ort.get_ep_devices())
                ):
                    runtime = "qnn-npu"
                elif "CUDAExecutionProvider" in available:
                    runtime = "cuda"
                elif "DmlExecutionProvider" in available:
                    runtime = "directml"
                else:
                    runtime = "onnx-cpu"
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
            "download_bytes": int(spec.get("download_bytes") or 0),
            "error": str(progress.get("error") or ""),
        })
    return {"models": models, "cv2_runtime": opencv_runtime.status()}


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
                if not progress.get("planned_total"):
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
    planned_total = int(spec.get("download_bytes") or 0)
    _PROGRESS[model_id] = {
        "downloaded_bytes": 0,
        "total_bytes": planned_total,
        "planned_total": planned_total,
        "error": "",
    }
    try:
        async with httpx.AsyncClient() as client:
            for item in spec["files"]:
                destination = root / item["path"]
                if _item_ready(root, item):
                    size = int(item.get("download_bytes") or 0)
                    _PROGRESS[model_id]["downloaded_bytes"] += size
                    if not planned_total:
                        _PROGRESS[model_id]["total_bytes"] += size
                    continue
                reusable_archive = bool(
                    item.get("extract")
                    and destination.is_file()
                    and _file_valid(destination, item)
                )
                if reusable_archive:
                    _PROGRESS[model_id]["downloaded_bytes"] += int(item.get("download_bytes") or 0)
                else:
                    await _download_file(client, item, destination, model_id)
                if item.get("extract"):
                    await asyncio.to_thread(_extract_archive, destination, root, item)
                    destination.unlink(missing_ok=True)
                    part = destination.with_suffix(destination.suffix + ".part")
                    part.unlink(missing_ok=True)
                    part.with_suffix(part.suffix + ".source").unlink(missing_ok=True)
        resetter = _RESETTERS.get(model_id)
        if resetter is not None:
            await asyncio.to_thread(resetter)
        _remove_obsolete_paths(root, spec)
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


async def delete_all_models() -> dict[str, Any]:
    """Cancel downloads, unload adapters, and remove every local model pack."""
    active = [task for task in _TASKS.values() if not task.done()]
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    for resetter in list(_RESETTERS.values()):
        try:
            resetter()
        except Exception:
            # Deleting the on-disk pack must still proceed if an optional
            # inference adapter has already partially torn itself down.
            pass
    await asyncio.to_thread(shutil.rmtree, MODEL_ROOT, True)
    _TASKS.clear()
    _PROGRESS.clear()
    _VALIDATED.clear()
    return status()
