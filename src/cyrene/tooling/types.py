"""Shared immutable types for Cyrene's tool control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping

ToolHandler = Callable[
    [dict[str, Any], Any, int, str, dict[str, bool] | None],
    Awaitable[str],
]


@dataclass(frozen=True)
class ToolSpec:
    capability_id: str
    concrete_name: str
    pack_id: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler | None
    actors: frozenset[str]
    risk_class: str
    side_effect_class: str
    resource_templates: tuple[str, ...]
    timeout_seconds: float = 180.0
    max_result_chars: int = 12_000
    external: bool = False


@dataclass(frozen=True)
class PackSpec:
    pack_id: str
    wire_name: str
    description: str
    capability_prefixes: tuple[str, ...]
    bundle_order: int


@dataclass(frozen=True)
class ToolCatalogSnapshot:
    version: str
    actor: str
    capabilities: Mapping[str, ToolSpec]
    enabled_capability_ids: frozenset[str]
    available_connector_ids: frozenset[str]
    schema_hashes: Mapping[str, str]


@dataclass(frozen=True)
class ToolExecutionContext:
    actor: str = "main"
    run_id: str = ""
    session_id: str = ""
    round_id: str = ""
    workspace: Path | None = None
    bot: Any = None
    chat_id: int = 0
    db_path: str = ""
    notify_state: dict[str, bool] | None = None
    system_initiated: bool = False
    permission_mode: str = "default"
    catalog_snapshot: ToolCatalogSnapshot | None = None


@dataclass
class ToolResult:
    status: Literal["success", "error", "awaiting_user", "skipped"]
    summary: str
    data: Any = None
    error_type: str | None = None
    next_valid_actions: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class WireToolBundle:
    version: str
    actor: str
    definitions: tuple[dict[str, Any], ...]
    sha256: str
    estimated_tokens: int
