import base64
import hashlib
from importlib import import_module
import io
import json
import logging
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader

from cyrene.config import DATA_DIR
from cyrene.model_runtime.messages import assistant_text, truncate
from cyrene.runtime.file_hashing import sha256_file

logger = logging.getLogger(__name__)

UPLOADS_DIR = DATA_DIR / "webui_uploads"
EXPORTS_DIR = DATA_DIR / "webui_exports"
# Analysis results are cached under the app data dir — never next to the source
# file — so read-only analysis can't mutate the user's workspace (issue #44).
ANALYSIS_CACHE_DIR = DATA_DIR / "attachment_cache"

# Bump when the parsing/analysis logic changes in a way that should invalidate
# every previously cached result.
_ANALYSIS_PARSER_VERSION = "4"

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
_PDF_EXTENSIONS = {".pdf"}
_MAP_EXTENSIONS = {".geojson", ".topojson"}
_MAP_CONTENT_TYPES = {"application/geo+json", "application/vnd.geo+json"}
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".json", ".md", ".yaml", ".yml",
    ".toml", ".xml", ".sql", ".sh", ".bash", ".rs", ".go", ".java", ".c", ".cpp",
    ".h", ".rb", ".php", ".swift", ".kt", ".txt", ".csv", ".ini", ".cfg", ".env",
}
_MULTIMODAL_MODEL_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-5",
    "gemini",
    "claude-3",
    "claude-4",
    "qwen",
    "qwen-vl",
    "vl",
    "vision",
    "glm-4v",
    "internvl",
    "minicpm-v",
)


def _file_content_hash(path: Path) -> str:
    """Return a digest derived exclusively from the file's bytes."""
    return sha256_file(path)


def _vision_model_fingerprint() -> str:
    """A stable string that changes whenever the configured vision model(s) change."""
    try:
        from cyrene.runtime.config_store import get_vision_models
        return json.dumps(get_vision_models() or [], sort_keys=True, ensure_ascii=False)
    except Exception:
        return ""


def _local_ocr_fingerprint() -> str:
    """Include local OCR availability in attachment-analysis cache identity."""
    try:
        from cyrene.knowledge import local_models, ocr

        return f"{ocr.MODEL_ID}:{int(local_models.is_ready(ocr.MODEL_ID))}"
    except Exception:
        return "pp-ocrv6-medium:0"


def _analysis_cache_key(path: Path, prompt: str) -> str:
    """Cache key from file content, prompt, vision-model config, and parser version.

    Any change to one of these yields a different key, so a stale analysis is
    never reused after the file, prompt, model, or parser changes (issue #44).
    """
    parts = "\x00".join([
        _ANALYSIS_PARSER_VERSION,
        _file_content_hash(path),
        prompt or "",
        _vision_model_fingerprint(),
        _local_ocr_fingerprint(),
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _cache_file(key: str) -> Path:
    return ANALYSIS_CACHE_DIR / f"{key}.json"


def _path_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_attachment_under(path_str: str, root: Path) -> Path | None:
    """Resolve current or relocated paths inside one managed attachment root.

    Knowledge rows persist absolute paths. A portable backup can be restored
    under a different home/application-data directory, so an otherwise valid
    path may still carry the old prefix. Only the stable
    ``data/<managed-dir>/...`` suffix is rebased; arbitrary external paths are
    never redirected.
    """
    raw = str(path_str or "").strip()
    if not raw:
        return None
    resolved_root = root.resolve()
    try:
        direct = Path(raw).expanduser().resolve()
        if _path_within(direct, resolved_root):
            return direct
    except Exception:
        pass

    normalized = raw.replace("\\", "/")
    if not (normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized)):
        return None
    marker = f"/data/{root.name}/"
    marker_index = normalized.lower().rfind(marker.lower())
    if marker_index < 0:
        return None
    relative = normalized[marker_index + len(marker):]
    try:
        candidate = (resolved_root / Path(relative)).resolve()
    except Exception:
        return None
    return candidate if _path_within(candidate, resolved_root) else None


def resolve_managed_attachment_path(path_str: str) -> Path | None:
    """Return the safe current location for a managed upload/export path."""
    for root in (UPLOADS_DIR, EXPORTS_DIR):
        resolved = _resolve_attachment_under(path_str, root)
        if resolved is not None:
            return resolved
    return None


def is_uploaded_attachment_path(path_str: str) -> bool:
    try:
        return _resolve_attachment_under(path_str, UPLOADS_DIR) is not None
    except Exception:
        return False


def is_exported_attachment_path(path_str: str) -> bool:
    try:
        return _resolve_attachment_under(path_str, EXPORTS_DIR) is not None
    except Exception:
        return False


def is_pdf_path(path: Path) -> bool:
    return path.suffix.lower() in _PDF_EXTENSIONS


def is_image_path(path: Path) -> bool:
    if path.suffix.lower() in _IMAGE_EXTENSIONS:
        return True
    guessed, _ = mimetypes.guess_type(str(path))
    return bool(guessed and guessed.startswith("image/"))


def model_supports_multimodal(model: str | None = None) -> bool:
    model_name = str(model or os.environ.get("OPENAI_MODEL", "")).strip().lower()
    if not model_name:
        return False
    return any(hint in model_name for hint in _MULTIMODAL_MODEL_HINTS)


def primary_model_supports_vision() -> bool:
    """Whether the configured primary model passed Cyrene's vision probe.

    Browser screenshots are only sent as image input after this persisted check;
    model-name heuristics are intentionally not used for that high-cost path.
    """
    try:
        from cyrene.runtime.settings_store import get_models

        models = get_models() or []
        primary = models[0] if models else {}
        return isinstance(primary, dict) and primary.get("vision_capable") is True
    except Exception:
        return False


async def analyze_image_with_primary_model(path_str: str, prompt: str) -> dict[str, Any]:
    """Send a local image to the configured primary model as multimodal input."""
    path = Path(path_str).resolve()
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
    ]
    result = await call_llm(
        [{"role": "user", "content": content}],
        model_type="primary",
        thinking="disabled",
        caller="browser_vision",
        publish_events=False,
        record_usage=False,
    )
    return {
        "vision_model": result.get("model", ""),
        "vision_text": truncate((assistant_text(result) or "").strip(), 12000),
    }

def safe_attachment_filename(filename: str, fallback_stem: str = "file") -> str:
    """Return an ASCII-safe filename while preserving its original extension."""
    raw = Path(str(filename or f"{fallback_stem}.bin")).name
    suffix = Path(raw).suffix
    stem = raw[:-len(suffix)] if suffix else raw
    safe_stem = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "._-")) else "_"
        for ch in stem
    ).strip("._")
    safe_suffix = "".join(
        ch for ch in suffix if ch.isascii() and (ch.isalnum() or ch == ".")
    ).lower()
    return f"{safe_stem or fallback_stem}{safe_suffix}" or f"{fallback_stem}.bin"


def _safe_attachment_name(filename: str) -> str:
    return safe_attachment_filename(filename)


def attachment_kind_from_meta(content_type: str, filename: str) -> str:
    normalized_type = str(content_type or "").strip().lower()
    suffix = Path(str(filename or "")).suffix.lower()
    if normalized_type.startswith("image/") or suffix in _IMAGE_EXTENSIONS:
        return "image"
    if normalized_type.startswith("audio/"):
        return "audio"
    if normalized_type.startswith("video/"):
        return "video"
    if normalized_type == "application/pdf" or suffix in _PDF_EXTENSIONS:
        return "pdf"
    if normalized_type in _MAP_CONTENT_TYPES or suffix in _MAP_EXTENSIONS:
        return "map"
    if suffix in _CODE_EXTENSIONS or (normalized_type.startswith("text/") and normalized_type not in {"text/html", "application/xhtml+xml"}):
        return "code"
    return "file"


def build_public_attachment_payload(item: dict[str, Any]) -> dict[str, Any]:
    attachment_id = str(item.get("id") or "").strip()
    url = str(item.get("url") or "").strip()
    if not url and attachment_id:
        path_str = str(item.get("path") or "").strip()
        if path_str and is_uploaded_attachment_path(path_str):
            url = f"/api/chat/upload/{attachment_id}"
        elif path_str and is_exported_attachment_path(path_str):
            url = f"/api/chat/export/{attachment_id}"
    return {
        "id": attachment_id,
        "name": str(item.get("name") or "file"),
        "content_type": str(item.get("content_type") or "application/octet-stream"),
        "size": int(item.get("size") or 0),
        "kind": str(item.get("kind") or "file"),
        "url": url,
        **({"width": int(item.get("width"))} if isinstance(item.get("width"), int) else {}),
        **({"height": int(item.get("height"))} if isinstance(item.get("height"), int) else {}),
    }


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def register_generated_attachment(path_str: str, display_name: str | None = None) -> dict[str, Any]:
    source = Path(path_str).resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Attachment source not found: {source}")

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_display_name = _safe_attachment_name(display_name or source.name)
    safe_stem = Path(safe_display_name).stem or "file"
    suffix = Path(safe_display_name).suffix or source.suffix or ".bin"
    source_hash = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:10]
    export_id = f"{safe_stem[:40]}_{source_hash}{suffix}"
    target = EXPORTS_DIR / export_id
    if source != target:
        shutil.copy2(source, target)

    content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    kind = attachment_kind_from_meta(content_type, safe_display_name)
    width, height = _image_dimensions(target) if kind == "image" else (None, None)
    return {
        "id": target.name,
        "name": display_name or source.name,
        "path": str(target.resolve()),
        "content_type": content_type,
        "size": target.stat().st_size,
        "kind": kind,
        "url": f"/api/chat/export/{target.name}",
        **({"width": width} if isinstance(width, int) else {}),
        **({"height": height} if isinstance(height, int) else {}),
    }


_INLINE_IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
    "BMP": ("image/bmp", ".bmp"),
}
_GENERATED_IMAGE_MAX_PIXELS = 80_000_000


def register_generated_image_bytes(
    content: bytes,
    *,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Register a decoded external-Agent image as a managed Cyrene export.

    The image format is detected from its bytes rather than trusted protocol
    metadata. A content-derived name makes repeated ACP updates idempotent and
    the export route keeps the resulting viewer URL inside Cyrene's managed
    file boundary.
    """
    if not content:
        raise ValueError("generated image is empty")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format = str(image.format or "").upper()
            width, height = int(image.width), int(image.height)
            if width <= 0 or height <= 0 or width * height > _GENERATED_IMAGE_MAX_PIXELS:
                raise ValueError("generated image dimensions are unsafe")
            image.verify()
    except Exception as exc:
        raise ValueError("generated image data is invalid") from exc
    detected = _INLINE_IMAGE_FORMATS.get(image_format)
    if detected is None:
        raise ValueError(f"generated image format is unsupported: {image_format or 'unknown'}")
    content_type, suffix = detected

    requested_name = safe_attachment_filename(display_name or f"agent-image{suffix}", "agent-image")
    requested_stem = Path(requested_name).stem or "agent-image"
    digest = hashlib.sha256(content).hexdigest()[:16]
    export_id = f"{requested_stem[:40]}_{digest}{suffix}"
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = EXPORTS_DIR / export_id
    if not target.exists():
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
    if not target.is_file():
        raise OSError(f"failed to register generated image: {target}")
    return {
        "id": target.name,
        "name": display_name or f"agent-image{suffix}",
        "path": str(target.resolve()),
        "content_type": content_type,
        "size": target.stat().st_size,
        "kind": "image",
        "url": f"/api/chat/export/{target.name}",
        "width": width,
        "height": height,
    }


def register_generated_attachment_bytes(
    content: bytes,
    *,
    display_name: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Register bounded external-Agent bytes as a managed Cyrene export.

    Images retain the stricter decode/dimension validation above. Other ACP
    resources are content-addressed and stored with a safe filename so the
    existing export route and Viewer can handle PDF, text, code, HTML, audio,
    and arbitrary downloadable files without trusting an Agent-supplied path.
    """
    if not content:
        raise ValueError("generated attachment is empty")
    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type.startswith("image/"):
        return register_generated_image_bytes(content, display_name=display_name)

    guessed_suffix = mimetypes.guess_extension(normalized_type) if normalized_type else ""
    requested_name = safe_attachment_filename(
        display_name or f"agent-file{guessed_suffix or '.bin'}",
        "agent-file",
    )
    suffix = Path(requested_name).suffix or guessed_suffix or ".bin"
    stem = Path(requested_name).stem or "agent-file"
    digest = hashlib.sha256(content).hexdigest()[:16]
    export_id = f"{stem[:40]}_{digest}{suffix}"
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = EXPORTS_DIR / export_id
    if not target.exists():
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
    if not target.is_file():
        raise OSError(f"failed to register generated attachment: {target}")
    effective_type = normalized_type or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return {
        "id": target.name,
        "name": display_name or requested_name,
        "path": str(target.resolve()),
        "content_type": effective_type,
        "size": target.stat().st_size,
        "kind": attachment_kind_from_meta(effective_type, requested_name),
        "url": f"/api/chat/export/{target.name}",
    }


def _build_attachment_preview(result: dict[str, Any]) -> str:
    kind = str(result.get("kind") or "file")
    if kind == "pdf":
        preview = str(result.get("text_preview") or "").strip()
        return preview or "PDF detected, but no text could be extracted."
    if kind == "image":
        ocr_text = str(result.get("ocr_text") or "").strip()
        vision_text = str(result.get("vision_text") or "").strip()
        if ocr_text and vision_text:
            return truncate(
                f"OCR text:\n{ocr_text}\n\nVisual analysis:\n{vision_text}",
                12000,
            )
        if ocr_text:
            return truncate(f"OCR text:\n{ocr_text}", 12000)
        if vision_text:
            return vision_text
        meta = result.get("image_meta", {})
        width = meta.get("width")
        height = meta.get("height")
        fmt = meta.get("format") or "image"
        if width and height:
            return f"Image metadata only: {fmt}, {width}x{height}."
        return "Image uploaded."
    if kind == "document":
        preview = str(result.get("text_preview") or "").strip()
        return preview or "Document detected, but no text could be extracted."
    return str(result.get("note") or "File uploaded.")


def _read_cache(key: str) -> dict[str, Any] | None:
    cache_file = _cache_file(key)
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_cache(key: str, payload: dict[str, Any]) -> None:
    # Best-effort: caching must never fail an otherwise-successful analysis.
    try:
        ANALYSIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _pdf_analysis(path: Path, max_chars: int = 12000) -> dict[str, Any]:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    joined = "\n\n".join(part.strip() for part in pages if part and part.strip())
    return {
        "kind": "pdf",
        "page_count": len(reader.pages),
        "text_chars": len(joined),
        "text_preview": truncate(joined, max_chars),
    }


def _image_metadata(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return {
            "format": str(image.format or "").upper(),
            "width": int(image.width),
            "height": int(image.height),
            "mode": str(image.mode or ""),
        }


async def _vision_analysis(path: Path, prompt: str = "") -> dict[str, Any]:
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    content_prompt = prompt.strip() or "Describe this image in detail and extract any visible text."
    content = [
        {"type": "text", "text": content_prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
    ]
    return await run_vision_chat(content, content_prompt=content_prompt)


async def vision_analysis(path: Path, prompt: str = "") -> dict[str, Any]:
    """Public primary-model vision boundary for Knowledge ingestion."""
    return await _vision_analysis(path, prompt)


async def run_vision_chat(
    content: list[dict[str, Any]],
    content_prompt: str = "",
    *,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    record_latency: bool = False,
) -> dict[str, Any]:
    """Run a vision-capable LLM call with image content."""
    # Vision analysis is an optional high-level execution path. Keep its model
    # gateway dependency at this service boundary so the low-level attachment
    # helpers remain importable by model/runtime infrastructure.
    call_model = import_module("cyrene.call_llm").call_llm
    result = await call_model(
        [{"role": "user", "content": content}],
        model_type="vision",
        max_tokens=max_tokens,
        timeout=timeout,
        thinking="disabled",
        caller="vision",
        publish_events=False,
        record_usage=False,
        record_latency=record_latency,
    )
    vision_text = assistant_text(result) or ""
    return {
        "vision_model": result.get("model", ""),
        "vision_prompt": content_prompt,
        "vision_text": truncate(vision_text.strip(), 12000),
    }


async def analyze_attachment(path_str: str, prompt: str = "", force_refresh: bool = False) -> dict[str, Any]:
    path = Path(path_str).resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Attachment file not found: {path}")
    cache_key = _analysis_cache_key(path, prompt)
    cached = None if force_refresh else _read_cache(cache_key)
    if cached:
        return cached

    payload: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "size": path.stat().st_size if path.exists() else 0,
        "content_type": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
    }
    if is_pdf_path(path):
        payload.update(_pdf_analysis(path))
    elif is_image_path(path):
        payload["kind"] = "image"
        payload["image_meta"] = _image_metadata(path)
        payload["multimodal_model"] = model_supports_multimodal()
        recognized = ""
        try:
            from cyrene.knowledge import local_models, ocr

            payload["local_ocr_available"] = local_models.is_ready(ocr.MODEL_ID)
            if payload["local_ocr_available"]:
                recognized = (await ocr.recognize(str(path))).strip()
                payload["ocr_model"] = ocr.MODEL_ID
                payload["ocr_text"] = truncate(recognized, 12000)
                payload["ocr_chars"] = len(recognized)
        except Exception:
            logger.debug("Local OCR failed for %s", path, exc_info=True)
            payload["local_ocr_status"] = "failed"

        # Good OCR is enough for the default text-extraction request. A short
        # result or an explicit semantic prompt still falls back to vision.
        needs_vision = len(recognized) < 30 or bool(prompt.strip())
        if needs_vision:
            try:
                payload.update(await _vision_analysis(path, prompt=prompt))
            except Exception:
                if payload["multimodal_model"] and not payload.get("ocr_text"):
                    raise
                if payload.get("ocr_text"):
                    payload["note"] = "Local OCR succeeded; visual description was unavailable."
                else:
                    payload["note"] = "Current model does not appear to support vision input."
        elif payload.get("ocr_text"):
            payload["note"] = "Text extracted with the local OCR model."
    else:
        from cyrene.knowledge.extractors import extract_office_xml_text

        text = extract_office_xml_text(path)
        if text.strip():
            payload["kind"] = "document"
            payload["text_chars"] = len(text)
            payload["text_preview"] = truncate(text, 12000)
        else:
            payload["kind"] = "file"
            payload["note"] = "No readable text could be extracted from this file type."

    payload["preview"] = _build_attachment_preview(payload)
    _write_cache(cache_key, payload)
    return payload
