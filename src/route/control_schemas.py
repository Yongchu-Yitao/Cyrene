"""Strict, versioned request and response models for the Control API.

Unlike the historical Workbench UI request models, this contract rejects
unknown fields.  It is intentionally small enough to become a stable client
surface without exposing Cyrene's complete desktop-local management API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ControlErrorResponse(ControlModel):
    error: str
    code: str = ""
    detail: str = ""


class ControlFeature(ControlModel):
    name: str
    available: bool
    detail: str = ""


class ControlCapabilitiesResponse(ControlModel):
    api_version: Literal["v1"] = "v1"
    protocol_version: int = 1
    auth_boundary: Literal["desktop_local"] = "desktop_local"
    remote_transport_available: bool = False
    durable_run_events: bool = False
    operations: list[str]
    features: list[ControlFeature]


class ControlProjectSummary(ControlModel):
    id: str
    name: str
    status: str
    updated_at: str = ""
    task_count: int = 0


class ControlProjectListResponse(ControlModel):
    projects: list[ControlProjectSummary]


class ControlChatSummary(ControlModel):
    id: str
    project_id: str
    title: str
    status: str
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0
    running: bool = False
    awaiting_user: bool = False


class ControlChatListResponse(ControlModel):
    chats: list[ControlChatSummary]


class ControlMessage(ControlModel):
    id: str
    role: str
    content: str
    created_at: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    question_id: str = ""
    question_kind: str = ""


class ControlChatDetail(ControlChatSummary):
    messages: list[ControlMessage]


class ControlChatResponse(ControlModel):
    chat: ControlChatDetail


class ControlChatCreateRequest(ControlModel):
    project_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=160)


class ControlChatMessageRequest(ControlModel):
    message: str = Field(min_length=1, max_length=200_000)
    permission_mode: Literal["default", "plan"] = "default"
    language: Literal["", "en", "zh"] = ""


class ControlRunAccepted(ControlModel):
    run_id: str
    chat_id: str
    status: str
    created_at: str
    event_cursor: int = 0


class ControlRunResponse(ControlModel):
    run_id: str
    chat_id: str
    status: str
    created_at: str
    completed: bool
    termination_reason: str = ""
    outcome: str = ""
    last_event_cursor: int = 0


class ControlRunEvent(ControlModel):
    cursor: int
    run_id: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class ControlRunEventsResponse(ControlModel):
    run_id: str
    events: list[ControlRunEvent]
    next_cursor: int
    completed: bool
    truncated: bool = False


class ControlGuidanceRequest(ControlModel):
    message: str = Field(min_length=1, max_length=200_000)
    request_id: str = Field(default="", max_length=200)


class ControlGuidanceResponse(ControlModel):
    queued: bool
    duplicate: bool = False
    event_id: str
    run_id: str


class ControlInterruptResponse(ControlModel):
    interrupted: bool
    run_id: str
    status: str


class ControlTaskSummary(ControlModel):
    id: str
    project_id: str
    title: str
    goal: str = ""
    status: str
    priority: str = "medium"
    created_at: str = ""
    updated_at: str = ""
    artifact_count: int = 0


class ControlTaskDetail(ControlTaskSummary):
    plan: list[dict[str, Any]] = Field(default_factory=list)
    pending_question: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    goal_loop: dict[str, Any] | None = None


class ControlTaskListResponse(ControlModel):
    tasks: list[ControlTaskSummary]


class ControlTaskResponse(ControlModel):
    task: ControlTaskDetail


class ControlTaskCreateRequest(ControlModel):
    project_id: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=50_000)
    title: str = Field(default="", max_length=160)
    priority: Literal["high", "medium", "low"] = "medium"


class ControlTaskDispatchRequest(ControlModel):
    message: str = Field(min_length=1, max_length=200_000)
    permission_mode: Literal["default", "auto", "full_access"] = "auto"


class ControlTaskPlanApproveRequest(ControlModel):
    plan_definition_revision: int = Field(ge=0)


class ControlTaskStepRunRequest(ControlModel):
    message: str = Field(min_length=1, max_length=200_000)
    plan_definition_revision: int = Field(ge=0)
    permission_mode: Literal["default", "auto", "full_access"] = "auto"


class ControlTaskActionResponse(ControlModel):
    changed: bool
    action: Literal["pause", "resume", "cancel"]
    task: ControlTaskDetail


class ControlApprovalResponseRequest(ControlModel):
    answer: str = Field(min_length=1, max_length=200_000)
    permission_mode: Literal["default", "auto", "full_access"] = "default"


class ControlApprovalResponse(ControlModel):
    accepted: bool
    chat_id: str
    question_id: str
    awaiting_user: bool = False


class ControlTaskApprovalResponse(ControlModel):
    accepted: bool
    task_id: str
    question_id: str
    awaiting_user: bool = False


class ControlArtifactSummary(ControlModel):
    id: str
    task_id: str
    name: str
    type: str = ""
    created_at: str = ""
    size: int | None = None
    download_url: str


class ControlArtifactListResponse(ControlModel):
    artifacts: list[ControlArtifactSummary]
