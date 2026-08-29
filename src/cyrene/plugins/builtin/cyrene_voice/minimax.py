"""MiniMax cloud text-to-speech adapter owned by the Voice Plugin."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

TURBO_MODEL_ID = "speech-2.8-turbo"
HD_MODEL_ID = "speech-2.8-hd"
MODEL_IDS = (TURBO_MODEL_ID, HD_MODEL_ID)
DEFAULT_VOICE_ID = "male-qn-qingse"
_OFFICIAL_HOSTS = frozenset({"api.minimax.io", "api.minimaxi.com"})


def _is_minimax_connection(connection: dict[str, Any]) -> bool:
    options = connection.get("options")
    preset = str(
        options.get("provider_preset") if isinstance(options, dict) else ""
    ).strip().lower()
    connection_id = str(connection.get("id") or "").strip().lower()
    name = str(connection.get("name") or "").strip().lower()
    try:
        host = (urlsplit(str(connection.get("base_url") or "")).hostname or "").lower()
    except ValueError:
        host = ""
    return (
        preset == "minimax"
        or connection_id == "minimax"
        or name == "minimax"
        or host in _OFFICIAL_HOSTS
    )


def configured_connection() -> dict[str, Any] | None:
    """Return the first enabled MiniMax model connection with an API key."""
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    if service is None:
        return None
    configuration = service.get_model_configuration()
    return next(
        (
            dict(connection)
            for connection in configuration.get("connections", [])
            if isinstance(connection, dict)
            and connection.get("enabled", True)
            and str(connection.get("api_key") or "").strip()
            and _is_minimax_connection(connection)
        ),
        None,
    )


def is_configured() -> bool:
    return configured_connection() is not None


def _endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("MiniMax model service URL is invalid")
    if (parsed.hostname or "").lower() in _OFFICIAL_HOSTS:
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1/t2a_v2", "", ""))
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = f"{path}/t2a_v2"
    else:
        path = f"{path}/v1/t2a_v2"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def synthesize(
    text: str,
    *,
    model: str,
    voice_id: str = DEFAULT_VOICE_ID,
) -> bytes:
    if model not in MODEL_IDS:
        raise ValueError("unsupported MiniMax speech model")
    connection = configured_connection()
    if connection is None:
        raise RuntimeError("Configure MiniMax in Model Services before using MiniMax TTS")
    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": str(voice_id or DEFAULT_VOICE_ID).strip() or DEFAULT_VOICE_ID,
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32_000,
            "format": "wav",
            "channel": 1,
        },
        "output_format": "hex",
        "subtitle_enable": False,
    }
    headers = {
        "Authorization": f"Bearer {connection['api_key']}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(90.0, connect=10.0),
            follow_redirects=False,
        ) as client:
            response = client.post(
                _endpoint(str(connection.get("base_url") or "")),
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
    except httpx.TimeoutException as exc:
        raise RuntimeError("MiniMax TTS request timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"MiniMax TTS request failed with HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("MiniMax TTS request failed") from exc

    if not isinstance(result, dict):
        raise RuntimeError("MiniMax TTS returned an invalid response")
    base_resp = result.get("base_resp")
    try:
        status_code = int(base_resp.get("status_code") or 0) if isinstance(base_resp, dict) else 0
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MiniMax TTS returned an invalid response") from exc
    if status_code != 0:
        status_message = str(base_resp.get("status_msg") or "request rejected").strip()
        raise RuntimeError(f"MiniMax TTS error {status_code}: {status_message}")
    data = result.get("data")
    audio_hex = str(data.get("audio") or "").strip() if isinstance(data, dict) else ""
    if not audio_hex:
        raise RuntimeError("MiniMax TTS returned empty audio")
    try:
        audio = bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise RuntimeError("MiniMax TTS returned invalid audio") from exc
    if not audio:
        raise RuntimeError("MiniMax TTS returned empty audio")
    return audio


__all__ = [
    "DEFAULT_VOICE_ID",
    "HD_MODEL_ID",
    "MODEL_IDS",
    "TURBO_MODEL_ID",
    "configured_connection",
    "is_configured",
    "synthesize",
]
