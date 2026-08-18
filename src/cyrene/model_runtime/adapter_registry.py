"""Declarative registry for model-provider protocol adapters.

The registry deliberately contains no user credentials and no UI code.  A
provider preset can point at one of these protocol adapters, while genuinely
new protocols register another :class:`AdapterDefinition` at import time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class AdapterDefinition:
    """Stable capabilities and configuration hints for one wire protocol."""

    id: str
    label: str
    auth_type: str = "api_key"
    default_base_url: str = ""
    supports_discovery: bool = True
    capabilities: tuple[str, ...] = ("chat",)
    config_fields: tuple[str, ...] = ("base_url", "api_key")
    wire_protocol: str = "openai_chat_completions"
    category: str = "remote"
    user_selectable: bool = True

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        payload["config_fields"] = list(self.config_fields)
        payload["protocol"] = self.wire_protocol
        payload["selectable"] = self.user_selectable
        payload["managed"] = self.category == "managed"
        payload["config_schema"] = [
            {
                "name": field,
                "type": "secret" if field == "api_key" else "url" if field == "base_url" else "text",
                "required": field == "base_url",
            }
            for field in self.config_fields
        ]
        return payload


_LOCK = RLock()
_REGISTRY: dict[str, AdapterDefinition] = {}


def register_adapter(definition: AdapterDefinition, *, replace: bool = False) -> None:
    """Register an adapter definition.

    Third-party Python packages may call this during application bootstrap.
    Silent replacement is rejected so two plugins cannot accidentally claim
    the same durable adapter id.
    """

    if not isinstance(definition, AdapterDefinition):
        raise TypeError("definition must be an AdapterDefinition")
    adapter_id = definition.id.strip().lower()
    if not adapter_id or adapter_id != definition.id:
        raise ValueError("adapter id must be a non-empty lowercase identifier")
    with _LOCK:
        if adapter_id in _REGISTRY and not replace:
            raise ValueError(f"model adapter {adapter_id!r} is already registered")
        _REGISTRY[adapter_id] = definition


def get_adapter(adapter_id: str) -> AdapterDefinition | None:
    with _LOCK:
        return _REGISTRY.get(str(adapter_id or "").strip().lower())


def require_adapter(adapter_id: str) -> AdapterDefinition:
    definition = get_adapter(adapter_id)
    if definition is None:
        raise ValueError(f"unsupported model adapter: {adapter_id}")
    return definition


def list_adapters() -> list[AdapterDefinition]:
    with _LOCK:
        return list(_REGISTRY.values())


register_adapter(AdapterDefinition(
    id="anthropic",
    label="Anthropic",
    wire_protocol="anthropic_messages",
    default_base_url="https://api.anthropic.com/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
), replace=True)
register_adapter(AdapterDefinition(
    id="openai",
    label="OpenAI",
    default_base_url="https://api.openai.com/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
), replace=True)
register_adapter(AdapterDefinition(
    id="openai_responses",
    label="OpenAI Responses",
    wire_protocol="openai_responses",
    default_base_url="https://api.openai.com/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
), replace=True)
register_adapter(AdapterDefinition(
    id="gemini",
    label="Gemini",
    wire_protocol="gemini_generate_content",
    default_base_url="https://generativelanguage.googleapis.com/v1beta",
    capabilities=("chat", "vision", "tools", "reasoning"),
), replace=True)
register_adapter(AdapterDefinition(
    id="openai_compatible",
    label="OpenAI Compatible (Legacy)",
    category="compatibility",
    user_selectable=False,
    default_base_url="https://api.openai.com/v1",
    capabilities=("chat", "vision", "embedding", "tools", "reasoning"),
), replace=True)
register_adapter(AdapterDefinition(
    id="codex_oauth",
    label="Codex OAuth",
    wire_protocol="codex_app_server",
    category="managed",
    user_selectable=False,
    auth_type="oauth",
    default_base_url="codex://oauth",
    capabilities=("chat", "vision", "tools", "reasoning"),
    config_fields=(),
), replace=True)
register_adapter(AdapterDefinition(
    id="ollama",
    label="Ollama",
    category="managed",
    user_selectable=False,
    auth_type="none",
    default_base_url="http://127.0.0.1:11434",
    capabilities=("chat", "vision", "embedding", "tools"),
    config_fields=("base_url",),
), replace=True)
register_adapter(AdapterDefinition(
    id="local_onnx",
    label="Local ONNX",
    wire_protocol="local_onnx",
    category="managed",
    user_selectable=False,
    auth_type="none",
    supports_discovery=False,
    capabilities=("embedding",),
    config_fields=(),
), replace=True)


__all__ = [
    "AdapterDefinition",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "require_adapter",
]
