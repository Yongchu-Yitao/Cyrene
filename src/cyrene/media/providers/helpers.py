"""Validation, HTTP, reference, and artifact helpers shared by providers."""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import socket
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import httpx

from cyrene.media.models import MediaArtifact, MediaProviderError


_RETRYABLE_HTTP_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_MAX_REFERENCE_BYTES = 64 * 1024 * 1024
_DEFAULT_DOWNLOAD_BYTES = 256 * 1024 * 1024
_MAX_ERROR_CHARS = 1600
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def api_url(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/") + "/"
    parsed = urlparse(base)
    loopback_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parsed.scheme != "https" and not loopback_http) or not parsed.netloc:
        raise MediaProviderError(
            "Provider base URL must use HTTPS (loopback HTTP is allowed).",
            code="invalid_base_url",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MediaProviderError(
            "Provider base URL must not contain credentials, a query, or a fragment.",
            code="invalid_base_url",
        )
    return urljoin(base, str(path or "").lstrip("/"))


def require_api_key(settings: dict[str, Any], provider: str) -> str:
    value = str(settings.get("api_key") or "").strip()
    if not value:
        raise MediaProviderError(f"{provider} API key is not configured.", code="missing_api_key")
    return value


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    return max(minimum, min(result, maximum))


def bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    if result != result or result in {float("inf"), float("-inf")}:
        result = float(default)
    return max(minimum, min(result, maximum))


def parameters(request: dict[str, Any]) -> dict[str, Any]:
    raw = request.get("parameters")
    return dict(raw) if isinstance(raw, dict) else {}


def request_value(request: dict[str, Any], name: str, default: Any = None) -> Any:
    if request.get(name) is not None:
        return request.get(name)
    return parameters(request).get(name, default)


def request_references(request: dict[str, Any]) -> list[Any]:
    explicit: list[Any] = []
    for key in ("reference_paths", "reference_urls"):
        raw = request.get(key)
        if isinstance(raw, (list, tuple)):
            explicit.extend(item for item in raw if item is not None and item != "")
        if raw is not None and raw != "":
            if not isinstance(raw, (list, tuple)):
                explicit.append(raw)
    if explicit:
        return explicit
    for key in ("references", "images", "input_paths"):
        raw = request.get(key)
        if isinstance(raw, (list, tuple)):
            return [item for item in raw if item is not None and item != ""]
        if raw is not None and raw != "":
            return [raw]
    raw = request.get("image")
    return [] if raw is None or raw == "" else [raw]


def reference_roles(request: dict[str, Any], count: int) -> list[str]:
    raw = request.get("reference_roles")
    if not isinstance(raw, (list, tuple)):
        raw = parameters(request).get("reference_roles")
    roles = [str(value or "reference").strip().lower() for value in raw] if isinstance(raw, (list, tuple)) else []
    return (roles + ["reference"] * count)[:count]


def _reference_path(value: Any) -> Path | None:
    if isinstance(value, dict):
        value = value.get("path")
    raw = str(value or "").strip()
    if not raw or raw.startswith(("http://", "https://", "data:")):
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise MediaProviderError(f"Media reference does not exist: {path}", code="missing_reference")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_REFERENCE_BYTES:
        raise MediaProviderError("Media reference must be between 1 byte and 64 MiB.", code="invalid_reference_size")
    return path


def read_reference(value: Any) -> tuple[bytes, str, str]:
    """Return bytes, MIME type, and a stable display name for a reference."""
    explicit_mime = str(value.get("mime_type") or value.get("content_type") or "") if isinstance(value, dict) else ""
    source = value.get("data") if isinstance(value, dict) and value.get("data") is not None else value
    path = _reference_path(source)
    if path is not None:
        return path.read_bytes(), explicit_mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream", path.name
    raw = str(source or "").strip()
    if not raw.startswith("data:"):
        raise MediaProviderError("This provider requires local or data-URL references.", code="unsupported_reference")
    header, separator, encoded = raw.partition(",")
    if not separator or ";base64" not in header:
        raise MediaProviderError("Reference data URL must contain base64 data.", code="invalid_reference")
    mime_type = explicit_mime or header[5:].split(";", 1)[0] or "application/octet-stream"
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MediaProviderError("Reference data URL contains invalid base64.", code="invalid_reference") from exc
    if not data or len(data) > _MAX_REFERENCE_BYTES:
        raise MediaProviderError("Media reference must be between 1 byte and 64 MiB.", code="invalid_reference_size")
    return data, mime_type, "reference" + extension_for_mime(mime_type)


def reference_as_url(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("url", "uri"):
            if value.get(key):
                value = value[key]
                break
    raw = str(value or "").strip()
    if raw.startswith(("https://", "data:")):
        return raw
    data, mime_type, _ = read_reference(value)
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def extension_for_mime(mime_type: str, default: str = ".bin") -> str:
    normalized = str(mime_type or "").split(";", 1)[0].strip().lower()
    overrides = {
        "image/jpeg": ".jpg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "video/mp4": ".mp4",
    }
    return overrides.get(normalized) or mimetypes.guess_extension(normalized) or default


def safe_filename(value: str, *, fallback: str) -> str:
    name = Path(str(value or "")).name.strip()
    name = _SAFE_FILENAME.sub("-", name).strip(".-")[:160]
    return name or fallback


def artifact_from_bytes(
    data: bytes,
    *,
    prefix: str,
    index: int,
    content_type: str,
    filename: str = "",
) -> MediaArtifact:
    if not data:
        raise MediaProviderError("Provider returned an empty media file.", code="empty_output")
    suffix = extension_for_mime(content_type)
    fallback = f"{prefix}-{index}{suffix}"
    clean_name = safe_filename(filename, fallback=fallback)
    if "." not in clean_name:
        clean_name += suffix
    return MediaArtifact(filename=clean_name, content_type=content_type or "application/octet-stream", data=data)


async def _validate_public_download_url(url: str) -> None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise MediaProviderError("Provider returned an invalid output URL.", code="invalid_output_url")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise MediaProviderError("Provider output URL points to a local address.", code="unsafe_output_url")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError) as exc:
        raise MediaProviderError("Could not resolve provider output URL.", retryable=True, code="output_dns_error") from exc
    for item in addresses:
        try:
            address = ipaddress.ip_address(item[4][0])
        except ValueError:
            continue
        if not address.is_global:
            raise MediaProviderError("Provider output URL resolved to a non-public address.", code="unsafe_output_url")


async def download_bytes(
    url: str,
    *,
    max_bytes: int = _DEFAULT_DOWNLOAD_BYTES,
    timeout_seconds: float = 180.0,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str, str]:
    """Download a provider artifact with redirect checks and a hard byte limit."""
    limit = max(1, min(int(max_bytes), 2 * 1024 * 1024 * 1024))
    current = str(url or "").strip()
    timeout = httpx.Timeout(timeout_seconds, connect=min(20.0, timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for redirect_index in range(6):
                await _validate_public_download_url(current)
                request_headers = {"Accept": "*/*"}
                # Provider credentials are only sent to the original URL. A
                # redirect target is independently validated and must rely on
                # its own signed URL rather than receiving reusable secrets.
                if redirect_index == 0:
                    request_headers.update(headers or {})
                async with client.stream("GET", current, headers=request_headers) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        target = str(response.headers.get("location") or "").strip()
                        if not target:
                            raise MediaProviderError("Provider output redirect had no destination.", code="invalid_output_url")
                        current = urljoin(current, target)
                        continue
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", errors="replace")[:_MAX_ERROR_CHARS]
                        raise MediaProviderError(
                            f"Could not download provider output (HTTP {response.status_code}): {body}",
                            retryable=response.status_code in _RETRYABLE_HTTP_STATUS,
                            code=f"output_http_{response.status_code}",
                        )
                    header_size = response.headers.get("content-length")
                    if header_size:
                        try:
                            exceeds_limit = int(header_size) > limit
                        except (TypeError, ValueError):
                            exceeds_limit = False
                        if exceeds_limit:
                            raise MediaProviderError("Generated media exceeds the configured download limit.", code="output_too_large")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise MediaProviderError("Generated media exceeds the configured download limit.", code="output_too_large")
                        chunks.append(chunk)
                    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip()
                    filename = Path(urlparse(current).path).name
                    return b"".join(chunks), content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream", filename
    except MediaProviderError:
        raise
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise MediaProviderError(f"Provider output download failed: {exc}", retryable=True, code="output_download_error") from exc
    raise MediaProviderError("Provider output redirected too many times.", code="output_redirect_loop")


async def download_to_path(
    url: str,
    destination: str | Path,
    *,
    max_bytes: int = _DEFAULT_DOWNLOAD_BYTES,
    timeout_seconds: float = 180.0,
    headers: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Securely stream a provider artifact to disk without buffering it in RAM."""
    limit = max(1, min(int(max_bytes), 2 * 1024 * 1024 * 1024))
    current = str(url or "").strip()
    target = Path(destination)
    timeout = httpx.Timeout(timeout_seconds, connect=min(20.0, timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for redirect_index in range(6):
                await _validate_public_download_url(current)
                request_headers = {"Accept": "*/*"}
                if redirect_index == 0:
                    request_headers.update(headers or {})
                async with client.stream("GET", current, headers=request_headers) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = str(response.headers.get("location") or "").strip()
                        if not location:
                            raise MediaProviderError(
                                "Provider output redirect had no destination.",
                                code="invalid_output_url",
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", errors="replace")[:_MAX_ERROR_CHARS]
                        raise MediaProviderError(
                            f"Could not download provider output (HTTP {response.status_code}): {body}",
                            retryable=response.status_code in _RETRYABLE_HTTP_STATUS,
                            code=f"output_http_{response.status_code}",
                        )
                    header_size = response.headers.get("content-length")
                    if header_size:
                        try:
                            exceeds_limit = int(header_size) > limit
                        except (TypeError, ValueError):
                            exceeds_limit = False
                        if exceeds_limit:
                            raise MediaProviderError(
                                "Generated media exceeds the configured download limit.",
                                code="output_too_large",
                            )
                    total = 0
                    with target.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > limit:
                                raise MediaProviderError(
                                    "Generated media exceeds the configured download limit.",
                                    code="output_too_large",
                                )
                            stream.write(chunk)
                    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip()
                    filename = Path(urlparse(current).path).name
                    return (
                        content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                        filename,
                    )
    except MediaProviderError:
        target.unlink(missing_ok=True)
        raise
    except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise MediaProviderError(
            f"Provider output download failed: {exc}",
            retryable=True,
            code="output_download_error",
        ) from exc
    target.unlink(missing_ok=True)
    raise MediaProviderError(
        "Provider output redirected too many times.",
        code="output_redirect_loop",
    )


async def artifact_from_url(
    url: str,
    *,
    prefix: str,
    index: int,
    max_bytes: int = _DEFAULT_DOWNLOAD_BYTES,
    timeout_seconds: float = 180.0,
    filename: str = "",
) -> MediaArtifact:
    del max_bytes, timeout_seconds
    remote_name = Path(urlparse(str(url or "")).path).name
    content_type = mimetypes.guess_type(filename or remote_name)[0] or "application/octet-stream"
    suffix = extension_for_mime(content_type)
    clean_name = safe_filename(
        filename or remote_name,
        fallback=f"{prefix}-{index}{suffix}",
    )
    if "." not in clean_name:
        clean_name += suffix
    return MediaArtifact(
        filename=clean_name,
        content_type=content_type,
        url=str(url or "").strip(),
    )


def json_payload(response: httpx.Response, provider: str) -> dict[str, Any]:
    if response.status_code >= 400:
        message = response.text[:_MAX_ERROR_CHARS]
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or message)
            elif error:
                message = str(error)
            elif isinstance(body, dict):
                message = str(body.get("message") or body.get("detail") or message)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        raise MediaProviderError(
            f"{provider} request failed (HTTP {response.status_code}): {message[:_MAX_ERROR_CHARS]}",
            retryable=response.status_code in _RETRYABLE_HTTP_STATUS,
            code=f"{provider.lower()}_http_{response.status_code}",
        )
    try:
        value = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProviderError(f"{provider} returned invalid JSON.", retryable=True, code=f"{provider.lower()}_invalid_json") from exc
    if not isinstance(value, dict):
        raise MediaProviderError(f"{provider} returned an invalid response object.", retryable=True, code=f"{provider.lower()}_invalid_response")
    return value


async def request_json(
    method: str,
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds, connect=min(20.0, timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.request(method, url, headers=headers, json=payload, params=params)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise MediaProviderError(f"{provider} request failed: {exc}", retryable=True, code=f"{provider.lower()}_transport") from exc
    return json_payload(response, provider)


def decode_base64_media(value: str, *, provider: str) -> bytes:
    raw = str(value or "").strip()
    if raw.startswith("data:"):
        raw = raw.partition(",")[2]
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MediaProviderError(f"{provider} returned invalid base64 media.", code=f"{provider.lower()}_invalid_base64") from exc


def parse_json_text(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    source = str(value or "").strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if len(lines) >= 3:
            source = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    for marker in ("{", "["):
        offset = source.find(marker)
        if offset < 0:
            continue
        try:
            parsed, _ = decoder.raw_decode(source[offset:])
            return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return {"text": source}


def walk_values(value: Any, keys: Iterable[str]) -> list[Any]:
    wanted = {str(key) for key in keys}
    results: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in wanted and child is not None and child != "":
                results.append(child)
            if isinstance(child, (dict, list, tuple)):
                results.extend(walk_values(child, wanted))
    elif isinstance(value, (list, tuple)):
        for child in value:
            results.extend(walk_values(child, wanted))
    return results


def first_string(value: Any, keys: Iterable[str]) -> str:
    for key in keys:
        for candidate in walk_values(value, (str(key),)):
            if isinstance(candidate, (str, int)) and str(candidate).strip():
                return str(candidate).strip()
    return ""


def configured_download_limit(settings: dict[str, Any]) -> int:
    megabytes = bounded_int(
        settings.get("max_download_mb") or settings.get("_max_download_mb"),
        256,
        10,
        1024,
    )
    return megabytes * 1024 * 1024


__all__ = [
    "api_url",
    "artifact_from_bytes",
    "artifact_from_url",
    "bounded_float",
    "bounded_int",
    "configured_download_limit",
    "decode_base64_media",
    "download_bytes",
    "extension_for_mime",
    "first_string",
    "json_payload",
    "parameters",
    "parse_json_text",
    "read_reference",
    "reference_as_url",
    "reference_roles",
    "request_json",
    "request_references",
    "request_value",
    "require_api_key",
    "safe_filename",
    "walk_values",
]
