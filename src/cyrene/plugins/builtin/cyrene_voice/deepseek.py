"""DeepSeek's shared speech service, using the official model connection."""

from __future__ import annotations

import io
import wave
from urllib.parse import urlsplit

import httpx

from cyrene.core.plugin import application_plugin_service
from cyrene.platform.network_proxy import configured_proxy_url

MODEL_ID = "deepseek"
VOICES = ("stella", "tide")
MAX_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 120


def configured_connection() -> dict | None:
    service = application_plugin_service("model_configuration")
    if service is None:
        return None
    for connection in service.get_model_configuration().get("connections", []):
        if not connection.get("enabled", True) or not str(connection.get("api_key") or "").strip():
            continue
        try:
            url = urlsplit(connection.get("base_url") or "")
            if (url.scheme == "https" and url.hostname == "api.deepseek.com"
                    and url.username is None and url.password is None and url.port in (None, 443)):
                return connection
        except ValueError:
            continue
    return None


def _request(tool: str, **kwargs) -> httpx.Response:
    connection = configured_connection()
    if not connection:
        raise RuntimeError("DeepSeek model service is unavailable")
    try:
        with httpx.Client(timeout=httpx.Timeout(90, connect=10), trust_env=False,
                          proxy=configured_proxy_url(opt_in=connection.get("use_proxy") is True) or None) as client:
            response = client.post(
                f"https://api.deepseek.com/v1/tools/{tool}",
                headers={"Authorization": f"Bearer {connection['api_key']}"},
                **kwargs,
            )
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"DeepSeek {tool} returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"DeepSeek {tool} connection failed") from exc


def transcribe(payload: bytes) -> dict:
    if len(payload) > MAX_BYTES:
        raise ValueError("DeepSeek audio exceeds 8 MiB")
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            frames = wav.getnframes()
            if (wav.getsampwidth() != 2 or wav.getnchannels() not in (1, 2)
                    or wav.getframerate() not in (8000, 16000, 22050, 24000, 32000, 44100, 48000)
                    or not 0 < frames <= MAX_SECONDS * wav.getframerate()):
                raise ValueError("DeepSeek requires 16-bit PCM WAV up to 120 seconds")
    except (EOFError, wave.Error) as exc:
        raise ValueError("Invalid WAV recording") from exc
    response = _request("asr", files={"file": ("recording.wav", payload, "audio/wav")})
    try:
        result = response.json()
        text = result["text"].strip()
        duration = int(result["duration_ms"]) / 1000
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        raise RuntimeError("Invalid DeepSeek transcription") from exc
    return {"text": text, "raw_text": text, "silence_only": not text, "segments": int(bool(text)),
            "duration_seconds": duration, "model": MODEL_ID}


def synthesize(text: str, voice: str) -> bytes:
    if voice not in VOICES:
        raise ValueError("Unknown DeepSeek voice")
    audio = _request("tts", json={"text": text, "voice_id": voice, "format": "wav"}).content
    if len(audio) <= 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RuntimeError("DeepSeek returned invalid WAV audio")
    return audio
