"""Public model connectivity and capability probes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from cyrene.model_runtime.protocol_adapters import protocol_endpoints


_VISION_CAPABILITY_TEST_IMAGE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "0eQnAAAAAElFTkSuQmCC"
)


class ModelProbePort(Protocol):
    async def test_connection(self, api_key: str, base_url: str, model: str) -> str: ...

    async def probe_vision(
        self, api_key: str, base_url: str, model: str
    ) -> dict[str, Any]: ...


class ModelProbeService:
    """Probe one OpenAI-compatible model without persisting configuration."""

    async def test_connection(self, api_key: str, base_url: str, model: str) -> str:
        endpoint = protocol_endpoints("openai", base_url, model)[0]
        headers = _headers(api_key)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 48,
        }
        data = await _post_probe(endpoint, headers, payload)
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("LLM endpoint returned no choices")
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        return content or "OK"

    async def probe_vision(
        self, api_key: str, base_url: str, model: str
    ) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        endpoint = protocol_endpoints("openai", base_url, model)[0]
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "This is a capability check. Briefly confirm that an image was received.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _VISION_CAPABILITY_TEST_IMAGE},
                    },
                ],
            }],
            "max_tokens": 48,
        }
        try:
            data = await _post_probe(endpoint, _headers(api_key), payload)
            if not (data.get("choices") or []):
                raise ValueError(
                    "LLM endpoint returned no choices for the vision capability check"
                )
        except Exception as exc:
            detail = " ".join(str(exc).split())[:500]
            return {
                "vision_capable": False,
                "vision_checked_at": checked_at,
                "vision_check_error": detail or type(exc).__name__,
            }
        return {
            "vision_capable": True,
            "vision_checked_at": checked_at,
            "vision_check_error": "",
        }


async def _post_probe(
    endpoint: str, headers: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    transport = httpx.AsyncHTTPTransport(retries=1)
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


__all__ = ["ModelProbePort", "ModelProbeService"]
