"""Model connectivity and capability probes owned by the model Plugin pack."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.plugins.model_catalog import (
    application_model_runtime,
    resolve_model_plugin,
    resolve_registered_model_plugin,
)


_VISION_CAPABILITY_TEST_IMAGE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "0eQnAAAAAElFTkSuQmCC"
)


class ModelProbePort(Protocol):
    async def test_connection(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        provider_id: str = "openai_compatible",
        adapter: str = "openai_compatible",
    ) -> str: ...

    async def probe_vision(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        provider_id: str = "openai_compatible",
        adapter: str = "openai_compatible",
    ) -> dict[str, Any]: ...


class ModelProbeService:
    """Probe one model through the editable OpenAI-compatible model Plugin."""

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        runtime: PluginRuntime | None = None,
    ) -> None:
        self._registry = registry
        self._runtime = runtime

    async def test_connection(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        provider_id: str = "openai_compatible",
        adapter: str = "openai_compatible",
    ) -> str:
        result = await _complete(
            api_key,
            base_url,
            model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=48,
            registry=self._registry,
            runtime=self._runtime,
            provider_id=provider_id,
            adapter=adapter,
        )
        content = str(result.get("content") or "").strip()
        return content or "OK"

    async def probe_vision(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        provider_id: str = "openai_compatible",
        adapter: str = "openai_compatible",
    ) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        messages = [{
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
            }]
        try:
            await _complete(
                api_key,
                base_url,
                model,
                messages=messages,
                max_tokens=48,
                registry=self._registry,
                runtime=self._runtime,
                provider_id=provider_id,
                adapter=adapter,
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


async def _complete(
    api_key: str,
    base_url: str,
    model: str,
    *,
    messages: list[dict[str, Any]],
    max_tokens: int,
    registry: PluginRegistry | None = None,
    runtime: PluginRuntime | None = None,
    provider_id: str = "openai_compatible",
    adapter: str = "openai_compatible",
) -> dict[str, Any]:
    clean_base_url = str(base_url or "").strip().rstrip("/")
    clean_model = str(model or "").strip()
    connection = {
        "id": "onboarding-probe",
        "name": "Onboarding probe",
        "adapter": adapter,
        "enabled": True,
        "use_proxy": False,
        "base_url": clean_base_url,
        "api_key": str(api_key or "").strip(),
        "options": {"provider_preset": provider_id},
    }
    profile = {
        "id": "onboarding-probe",
        "connection_id": connection["id"],
        "model": clean_model,
        "name": clean_model,
        "enabled": True,
        "capabilities": ["chat", "vision"],
        "context_limit": 0,
        "dimensions": 0,
        "reasoning_effort": "",
        "options": {},
    }
    if registry is None:
        registry, plugin = resolve_model_plugin(
            provider_id,
            adapter,
        )
    else:
        plugin = resolve_registered_model_plugin(
            registry,
            provider_id,
            adapter,
        )
    if plugin is None:
        raise ValueError("Selected model Provider Plugin is not available")
    active_runtime = runtime or application_model_runtime(registry)
    outcome = await active_runtime.call(
        plugin.name,
        {
            "operation": "complete",
            "messages": messages,
            "model": clean_model,
            "max_tokens": max_tokens,
        },
        PluginContext(
            data={
                "caller": "onboarding_model_probe",
                "model_call_kind": "probe",
                "model_candidate": {
                    "id": profile["id"],
                    "profile_id": profile["id"],
                    "connection_id": connection["id"],
                    "model": clean_model,
                    "adapter": connection["adapter"],
                    "provider": provider_id,
                },
            },
            services={
                "model_connection": connection,
                "model_profile": profile,
            },
        ),
    )
    if not outcome.success:
        raise ValueError(outcome.error or "model Plugin probe failed")
    if not isinstance(outcome.value, dict):
        raise ValueError("model Plugin returned an invalid probe response")
    return dict(outcome.value)


__all__ = ["ModelProbePort", "ModelProbeService"]
