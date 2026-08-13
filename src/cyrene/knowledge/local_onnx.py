"""In-process Qwen3 embedding inference using ONNX Runtime."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from cyrene.knowledge import local_models


MODEL_ID = "qwen3-embedding-0.6b"
_MODEL: tuple[Any, Any] | None = None
_MLX_MODEL: Any = None
_MODEL_LOCK = threading.Lock()
_INFERENCE_LIMIT = asyncio.Semaphore(2)


def reset_model() -> None:
    global _MODEL, _MLX_MODEL
    with _MODEL_LOCK:
        _MODEL = None
        _MLX_MODEL = None


local_models.register_resetter(MODEL_ID, reset_model)


def _runtime() -> str:
    return str(local_models.MODEL_CATALOG[MODEL_ID].get("runtime") or "onnx")


def _create_session(ort: Any, model_path: str, options: Any, providers: list[Any]) -> Any:
    """Create an ORT session, dropping optional accelerators that fail to load."""
    candidates = list(providers)
    while candidates:
        try:
            return ort.InferenceSession(
                model_path,
                sess_options=options,
                providers=candidates,
            )
        except Exception:
            optional_index = next(
                (
                    index for index, provider in enumerate(candidates)
                    if (provider[0] if isinstance(provider, tuple) else provider)
                    != "CPUExecutionProvider"
                ),
                None,
            )
            if optional_index is None:
                raise
            candidates.pop(optional_index)
    raise RuntimeError("no ONNX Runtime execution provider is available")


def _load() -> tuple[Any, Any]:
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    if not local_models.is_ready(MODEL_ID):
        raise RuntimeError("local embedding model is not downloaded")
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError("local inference dependencies are unavailable") from exc
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        root = local_models.model_dir(MODEL_ID)
        qnn_enabled = local_models.configure_qnn_session_options(options, ort)
        providers = local_models.onnx_execution_providers()
        if any(
            (provider[0] if isinstance(provider, tuple) else provider) == "DmlExecutionProvider"
            for provider in providers
        ):
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        model_path = str(root / "model.onnx")
        try:
            session = (
                ort.InferenceSession(model_path, sess_options=options)
                if qnn_enabled
                else _create_session(ort, model_path, options, providers)
            )
        except Exception:
            if not qnn_enabled:
                raise
            # QNN HTP requires a supported/QDQ graph. Retain a reliable native
            # ARM CPU fallback for models that have not been converted yet.
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
            options.inter_op_num_threads = 1
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = _create_session(ort, model_path, options, providers)
        tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=1024)
        tokenizer.enable_padding()
        _MODEL = (session, tokenizer)
        return _MODEL


def _load_mlx():
    global _MLX_MODEL
    if _MLX_MODEL is not None:
        return _MLX_MODEL
    if not local_models.is_ready(MODEL_ID):
        raise RuntimeError("local embedding model is not downloaded")
    with _MODEL_LOCK:
        if _MLX_MODEL is not None:
            return _MLX_MODEL
        try:
            from mlx_lm.utils import load_model
        except ImportError as exc:
            raise RuntimeError("MLX inference dependencies are unavailable") from exc
        root = local_models.model_dir(MODEL_ID)
        model, _config = load_model(root / "mlx", lazy=False)
        _MLX_MODEL = model
        return _MLX_MODEL


def _embed_mlx_sync(texts: list[str]) -> list[list[float]]:
    import mlx.core as mx
    import numpy as np
    from tokenizers import Tokenizer

    model = _load_mlx()
    tokenizer = Tokenizer.from_file(
        str(local_models.model_dir(MODEL_ID) / "tokenizer.json")
    )
    tokenizer.enable_truncation(max_length=1024)
    vectors: list[list[float]] = []
    for text in texts:
        ids = tokenizer.encode(text).ids
        inputs = mx.array([ids])
        hidden = model.model(inputs)
        pooled = hidden[0, -1]
        pooled = pooled / mx.maximum(mx.linalg.norm(pooled), 1e-12)
        mx.eval(pooled)
        vectors.append(np.asarray(pooled, dtype=np.float32).tolist())
    return vectors


def _embed_sync(texts: list[str]) -> list[list[float]]:
    import numpy as np

    session, tokenizer = _load()
    encodings = tokenizer.encode_batch(texts)
    input_ids = np.asarray([encoding.ids for encoding in encodings], dtype=np.int64)
    attention_mask = np.asarray([encoding.attention_mask for encoding in encodings], dtype=np.int64)
    model_inputs = session.get_inputs()
    feed: dict[str, Any] = {}
    for model_input in model_inputs:
        if model_input.name == "input_ids":
            feed[model_input.name] = input_ids
        elif model_input.name == "attention_mask":
            feed[model_input.name] = attention_mask
        elif model_input.name == "position_ids":
            feed[model_input.name] = np.maximum(np.cumsum(attention_mask, axis=1) - 1, 0).astype(np.int64)
        elif model_input.name == "token_type_ids":
            feed[model_input.name] = np.zeros_like(input_ids)
        elif model_input.name.startswith("past_key_values."):
            # Optimum's decoder-style export keeps KV-cache inputs even for
            # full-sequence feature extraction. An empty cache represents the
            # first (and only) forward pass used for embeddings.
            shape = model_input.shape
            heads = int(shape[1]) if isinstance(shape[1], int) else 8
            head_dim = int(shape[3]) if isinstance(shape[3], int) else 128
            feed[model_input.name] = np.empty(
                (input_ids.shape[0], heads, 0, head_dim),
                dtype=np.float32,
            )
    hidden = session.run(None, feed)[0]
    last_indices = np.maximum(attention_mask.sum(axis=1) - 1, 0)
    pooled = hidden[np.arange(hidden.shape[0]), last_indices]
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    pooled = pooled / np.maximum(norms, 1e-12)
    return pooled.astype(np.float32).tolist()


async def embed_texts(texts: list[str], *, query: bool = False) -> list[list[float]]:
    if query:
        instruction = "Given a web search query, retrieve relevant passages that answer the query"
        texts = [f"Instruct: {instruction}\nQuery: {text}" for text in texts]
    async with _INFERENCE_LIMIT:
        target = _embed_mlx_sync if _runtime() == "mlx" else _embed_sync
        return await asyncio.to_thread(target, texts)
