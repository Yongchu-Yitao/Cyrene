"""Typed boundary objects for Workbench chat application services.

These TypedDicts make the application/route seam explicit while preserving
extension-owned fields in the SQLite document model.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class ChatMessageDTO(TypedDict, total=False):
    id: str
    role: str
    content: str
    createdAt: str
    attachments: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    usage: dict[str, int]
    latestRequestUsage: dict[str, int]
    model: str
    modelIdentity: dict[str, Any]
    processingDurationMs: int
    modelGenerationDurationMs: float
    outputTokensPerSecond: float
    modelStatusCard: bool
    modelStatus: dict[str, str]


class ChatSummaryDTO(TypedDict, total=False):
    id: str
    projectId: str
    kind: str
    title: str
    status: str
    model: str
    preview: str
    createdAt: str
    updatedAt: str
    running: bool
    usage: dict[str, int]
    latestUsage: dict[str, int]


class ChatDetailDTO(ChatSummaryDTO, total=False):
    messages: list[ChatMessageDTO]
    completedTurnCount: int
    soulActive: bool
    workspaceActive: bool
    reasoningEffort: str
    workspaceOverride: str
    agent: dict[str, Any]
    modelAccess: dict[str, Any]
    capabilities: dict[str, Any]


class ChatStoreDTO(TypedDict):
    chats: list[ChatDetailDTO]


class ChatCreateDTO(TypedDict):
    project_id: str
    title: NotRequired[str]
    model: NotRequired[str]
    project_memory_snapshot: NotRequired[dict[str, Any] | None]
    agent: NotRequired[dict[str, Any] | None]
    model_access: NotRequired[dict[str, Any] | None]
    capabilities: NotRequired[dict[str, Any] | None]
    soul_active: NotRequired[bool | None]
    workspace_active: NotRequired[bool | None]
    reasoning_effort: NotRequired[str]


class ChatContextDTO(TypedDict, total=False):
    model: str
    selectedModel: str
    actualModel: str
    modelIdentity: dict[str, str]
    usage: dict[str, int]
    ctxLimit: int
    ctxUsed: int
    ratio: float | None
    compactTriggerRatio: float
    messageCount: int
    segments: list[dict[str, Any]]
    compaction: dict[str, Any]
    usedPluginPacks: list[str]
    usedStandalonePlugins: list[str]
    compositionSource: str


__all__ = [
    "ChatContextDTO",
    "ChatCreateDTO",
    "ChatDetailDTO",
    "ChatMessageDTO",
    "ChatStoreDTO",
    "ChatSummaryDTO",
]
