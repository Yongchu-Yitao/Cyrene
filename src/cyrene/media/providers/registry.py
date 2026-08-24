"""Provider registry and deterministic automatic routing."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cyrene.media.models import MediaProviderError
from cyrene.media.providers.base import MediaProvider
from cyrene.media.providers.comfyui import ComfyUIProvider
from cyrene.media.providers.google_media import GoogleMediaProvider
from cyrene.media.providers.helpers import reference_roles, request_references
from cyrene.media.providers.minimax import MiniMaxProvider
from cyrene.media.providers.openai_image import OpenAIImageProvider
from cyrene.media.providers.seedance import SeedanceProvider
from cyrene.media.providers.seedream import SeedreamProvider


PROVIDERS: dict[str, MediaProvider] = {
    "comfyui": ComfyUIProvider(),
    "openai": OpenAIImageProvider(),
    "seedream": SeedreamProvider(),
    "seedance": SeedanceProvider(),
    "minimax": MiniMaxProvider(),
    "google": GoogleMediaProvider(),
}

_ALIASES = {
    "comfy": "comfyui",
    "comfy-ui": "comfyui",
    "gpt-image": "openai",
    "gpt_image": "openai",
    "gpt-image-2": "openai",
    "gemini": "google",
    "veo": "google",
    "google-veo": "google",
    "google-gemini": "google",
    "bytedance-seedream": "seedream",
    "bytedance-seedance": "seedance",
}

_AUTO_ORDER = {
    "image": ("openai", "google", "seedream", "comfyui"),
    "video": ("google", "seedance", "minimax", "comfyui"),
    "music": ("minimax", "comfyui"),
}


def normalize_provider_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return _ALIASES.get(normalized, normalized)


def _provider_settings(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = settings.get("providers") if isinstance(settings.get("providers"), dict) else settings
    return {str(name): value for name, value in raw.items() if isinstance(value, dict)}


def _reference_kind(value: Any, role: str) -> tuple[str, bool]:
    normalized_role = str(role or "").lower()
    if normalized_role in {"video", "reference_video"}:
        forced = "video"
    elif normalized_role in {"audio", "reference_audio"}:
        forced = "audio"
    elif normalized_role in {
        "first_frame",
        "last_frame",
        "subject",
        "reference_image",
    }:
        forced = "image"
    else:
        forced = ""
    if isinstance(value, dict):
        raw = str(value.get("url") or value.get("uri") or value.get("path") or "").strip()
    else:
        raw = str(value or "").strip()
    remote = raw.startswith("https://")
    if forced:
        return forced, remote
    if raw.startswith("data:"):
        mime_type = raw[5:].split(";", 1)[0].lower()
    else:
        mime_type = (mimetypes.guess_type(Path(urlparse(raw).path).name)[0] or "").lower()
    if mime_type.startswith("image/"):
        return "image", remote
    if mime_type.startswith("video/"):
        return "video", remote
    if mime_type.startswith("audio/"):
        return "audio", remote
    return "unknown", remote


def _effective_model(
    kind: str,
    provider_settings: dict[str, Any],
    request: dict[str, Any] | None,
) -> str:
    return str((request or {}).get("model") or provider_settings.get(f"{kind}_model") or "").strip()


def _provider_ready(
    name: str,
    kind: str,
    provider_settings: dict[str, Any],
) -> bool:
    """Return whether automatic routing can execute without known setup gaps."""
    if not bool(provider_settings.get("enabled")):
        return False
    if name == "comfyui":
        return bool(str(provider_settings.get("mcp_server") or "").strip() and str(provider_settings.get(f"{kind}_workflow") or "").strip())
    return bool(str(provider_settings.get("api_key") or "").strip())


def _request_is_compatible(
    name: str,
    kind: str,
    provider_settings: dict[str, Any],
    request: dict[str, Any] | None,
) -> bool:
    if not request:
        return True
    references = request_references(request)
    has_mask = bool(request.get("mask_path"))
    if not references and not has_mask:
        return True
    roles = reference_roles(request, len(references))
    reference_kinds = [_reference_kind(reference, roles[index]) for index, reference in enumerate(references)]
    if name == "comfyui":
        if str(provider_settings.get("mode") or "local") == "cloud":
            return not has_mask and all(remote for _media, remote in reference_kinds)
        return True
    if kind == "image":
        if has_mask and name != "openai":
            return False
        if name == "seedream":
            return all(media == "image" for media, _remote in reference_kinds)
        if name in {"openai", "google"}:
            return all(media == "image" and not remote for media, remote in reference_kinds)
        return False
    if kind == "video":
        if has_mask:
            return False
        if name == "google":
            model = _effective_model(kind, provider_settings, request).lower()
            if "omni" in model:
                video_count = sum(media == "video" for media, _remote in reference_kinds)
                return (
                    all(not remote for _media, remote in reference_kinds)
                    and all(media in {"image", "video"} for media, _remote in reference_kinds)
                    and video_count <= 1
                    and (video_count == 0 or len(reference_kinds) == 1)
                    and "last_frame" not in roles
                )
            return all(media == "image" and not remote for media, remote in reference_kinds)
        if name == "minimax":
            model = _effective_model(kind, provider_settings, request).lower()
            if "h3" in model:
                counts = {media: sum(item == media for item, _remote in reference_kinds) for media in ("image", "video", "audio")}
                return (
                    all(media in {"image", "video", "audio"} for media, _remote in reference_kinds)
                    and counts["image"] <= 9
                    and counts["video"] <= 3
                    and counts["audio"] <= 3
                    and len(reference_kinds) <= 12
                )
            return all(media == "image" for media, _remote in reference_kinds)
        if name == "seedance":
            return all(media in {"image", "audio"} or (media == "video" and remote) for media, remote in reference_kinds)
    return False


def available_providers(
    kind: str,
    settings: dict[str, Any],
    request: dict[str, Any] | None = None,
) -> list[str]:
    normalized_kind = str(kind or "").strip().lower()
    configured = _provider_settings(settings)
    return [
        name
        for name in _AUTO_ORDER.get(normalized_kind, tuple(PROVIDERS))
        if name in PROVIDERS
        and PROVIDERS[name].supports(normalized_kind)
        and _provider_ready(name, normalized_kind, configured.get(name, {}))
        and _request_is_compatible(
            name,
            normalized_kind,
            configured.get(name, {}),
            request,
        )
    ]


def resolve_provider(
    requested: str,
    kind: str,
    settings: dict[str, Any],
    request: dict[str, Any] | None = None,
) -> tuple[str, MediaProvider]:
    """Resolve an explicit/default/automatic provider for one media kind."""
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in _AUTO_ORDER:
        raise MediaProviderError(f"Unsupported media kind: {normalized_kind or 'missing'}.", code="unsupported_kind")
    configured = _provider_settings(settings)
    name = normalize_provider_name(requested or "auto") or "auto"
    if name == "auto":
        defaults = settings.get("default_providers") if isinstance(settings.get("default_providers"), dict) else {}
        selected_default = normalize_provider_name(str(defaults.get(normalized_kind) or "auto"))
        if (
            selected_default
            and selected_default != "auto"
            and _provider_ready(
                selected_default,
                normalized_kind,
                configured.get(selected_default, {}),
            )
            and _request_is_compatible(
                selected_default,
                normalized_kind,
                configured.get(selected_default, {}),
                request,
            )
        ):
            name = selected_default
        else:
            available = available_providers(normalized_kind, settings, request)
            if not available:
                raise MediaProviderError(
                    f"No enabled provider supports {normalized_kind} generation.",
                    code="no_media_provider",
                )
            name = available[0]
    provider = PROVIDERS.get(name)
    if provider is None:
        raise MediaProviderError(f"Unknown media provider: {name}.", code="unknown_media_provider")
    if not provider.supports(normalized_kind):
        raise MediaProviderError(f"Provider {name} does not support {normalized_kind} generation.", code="unsupported_provider_kind")
    provider_config = configured.get(name)
    if not isinstance(provider_config, dict) or not bool(provider_config.get("enabled")):
        raise MediaProviderError(f"Media provider {name} is disabled or not configured.", code="media_provider_disabled")
    if not _request_is_compatible(
        name,
        normalized_kind,
        provider_config,
        request,
    ):
        raise MediaProviderError(
            f"Media provider {name} does not support the requested reference inputs.",
            code="provider_reference_unsupported",
        )
    return name, provider


__all__ = ["PROVIDERS", "available_providers", "normalize_provider_name", "resolve_provider"]
