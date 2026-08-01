"""Validated request bodies for the Workbench HTTP API.

Models intentionally ignore unknown fields.  The previous handlers read only
known keys from arbitrary dictionaries, so rejecting every extension field
would be a backwards-incompatible change for desktop clients.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SessionStatus = Literal[
    "idle",
    "pending",
    "initializing",
    "planning",
    "answered",
    "acted",
    "running",
    "waiting_for_user",
    "waiting_for_approval",
    "paused",
    "blocked",
    "failed",
    "review",
    "done",
    "completed",
    "cancelled",
]
Priority = Literal["high", "medium", "low"]


class APIBody(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        coerce_numbers_to_str=True,
    )


def body_dict(body: APIBody) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True, by_alias=True)


class EmptyBody(APIBody):
    pass


class ProjectCreateBody(APIBody):
    name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=20_000)
    icon: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=80)
    template: str | None = Field(default=None, max_length=80)
    workspacePath: str | None = Field(default=None, max_length=4096)
    accountTier: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=20_000)
    stack: list[Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_workspace_snake_case(cls, value: Any) -> Any:
        if isinstance(value, dict) and "workspacePath" not in value and "workspace_path" in value:
            value = {**value, "workspacePath": value["workspace_path"]}
        return value


class ProjectUpdateBody(APIBody):
    name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=20_000)
    icon: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=80)
    template: str | None = Field(default=None, max_length=80)
    workspacePath: str | None = Field(default=None, max_length=4096)
    status: Literal["active", "paused", "archived"] | None = None
    model: str | None = Field(default=None, max_length=500)
    accountTier: str | None = Field(default=None, max_length=80)
    context: dict[str, Any] | None = None


class SessionCreateBody(APIBody):
    title: str | None = Field(default=None, max_length=160)
    goal: str | None = Field(default=None, max_length=50_000)
    priority: Priority | None = None


class FollowUpBody(APIBody):
    title: str | None = Field(default=None, max_length=160)
    goal: str | None = Field(default=None, max_length=50_000)


class SessionUpdateBody(APIBody):
    title: str | None = Field(default=None, max_length=160)
    goal: str | None = Field(default=None, max_length=50_000)
    status: SessionStatus | None = None
    priority: Priority | None = None
    agentReply: str | None = None
    summary: str | dict[str, Any] | None = None
    kind: Literal["task", "init"] | None = None
    approvedPlanDefinitionRevision: int | None = Field(default=None, ge=0)
    constraints: list[Any] | None = None
    events: list[Any] | None = None
    runs: list[Any] | None = None
    artifacts: list[Any] | None = None
    acceptanceCriteria: list[Any] | None = None
    plan: list[Any] | None = None
    init: dict[str, Any] | None = None


class PlanMutationBody(APIBody):
    operation: Literal["add", "update", "set_dependencies", "delete", "reorder"]
    basePlanRevision: int = Field(ge=0)
    fields: dict[str, Any] | None = None
    step: dict[str, Any] | None = None
    stepId: str | None = Field(default=None, max_length=200)
    dependsOn: list[Any] | None = None
    orderedStepIds: list[Any] | None = None


class NotificationsReadBody(APIBody):
    ids: list[str] = Field(default_factory=list, max_length=500)
    markAll: bool = False


class WorkbenchActivateBody(APIBody):
    projectId: str | None = Field(default=None, max_length=200)
    sessionId: str | None = Field(default=None, max_length=200)


class InitGenerateBody(APIBody):
    lang: Literal["", "en", "zh"] = ""


class PlanGenerateBody(APIBody):
    goal: str | None = Field(default=None, max_length=50_000)
    feedback: str | None = Field(default=None, max_length=50_000)
    autoStart: bool = False
    operation: Literal["auto", "create", "revise", "replace"] = "auto"
    basePlanRevision: int | None = Field(default=None, ge=0)


class ReflectionBody(APIBody):
    focus: str | None = Field(default=None, max_length=50_000)
    goalGap: str | None = Field(default=None, max_length=50_000)


class AgentInputBody(APIBody):
    input: str | None = Field(default=None, max_length=200_000)
    message: str | None = Field(default=None, max_length=200_000)
    attachments: list[Any] = Field(default_factory=list, max_length=100)
    mode: str | None = Field(default=None, max_length=80)
    command: str | None = Field(default=None, max_length=20_000)
    model: str | None = Field(default=None, max_length=500)
    reasoningEffort: Literal["", "low", "medium", "high", "xhigh", "max", "ultra"] = ""
    stepId: str | None = Field(default=None, max_length=200)
    stepTitle: str | None = Field(default=None, max_length=1000)
    action: str | None = Field(default=None, max_length=200)
    meta: dict[str, Any] | None = None
    planDefinitionRevision: int | None = Field(default=None, ge=0)
    basePlanRevision: int | None = Field(default=None, ge=0)


class AnswerBody(APIBody):
    question_id: str = Field(min_length=1, max_length=500)
    answer: str | None = Field(default=None, max_length=200_000)
    selected_option: str | None = Field(default=None, max_length=200_000)
    mode: str | None = Field(default=None, max_length=80)
    stream: bool = False


class InitSubmitBody(APIBody):
    answers: dict[str, Any] = Field(default_factory=dict)


class InitPlanBody(APIBody):
    feedback: str | None = Field(default=None, max_length=50_000)
    message: str | None = Field(default=None, max_length=50_000)
    taskPlan: list[Any] = Field(default_factory=list, max_length=100)


class InitConfirmBody(APIBody):
    taskPlan: list[Any] = Field(default_factory=list, max_length=100)


class ChatCreateBody(APIBody):
    project: str | None = Field(default=None, max_length=200)
    projectId: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=160)


class SideAgentCreateBody(APIBody):
    quote: str = Field(min_length=1, max_length=12_000)
    title: str | None = Field(default=None, max_length=160)


class ChatUpdateBody(APIBody):
    title: str | None = Field(default=None, max_length=160)


class ChatGroupMetadataBody(APIBody):
    projectId: str | None = Field(default=None, max_length=200)
    groupId: str | None = Field(default=None, max_length=200)
    signature: str | None = Field(default=None, max_length=20_000)
    members: list[Any] = Field(default_factory=list, min_length=2, max_length=50)
    currentTitle: str | None = Field(default=None, max_length=160)
    titleLocked: bool = False
    lang: Literal["", "en", "zh"] = ""


class ChatGroupsReplaceBody(APIBody):
    projectId: str = Field(min_length=1, max_length=200)
    groups: list[Any] = Field(default_factory=list, max_length=1000)
    baseGroups: list[Any] | None = Field(default=None, max_length=1000)
    intent: dict[str, Any] | None = None


class ChatMessageBody(APIBody):
    message: str | None = Field(default=None, max_length=200_000)
    attachments: list[Any] = Field(default_factory=list, max_length=100)
    command: str | None = Field(default=None, max_length=20_000)
    model: str | None = Field(default=None, max_length=500)
    reasoningEffort: Literal["", "low", "medium", "high", "xhigh", "max", "ultra"] = ""
    stream: bool = False
    retry: bool = False
    forkReplay: bool = False
    mode: str | None = Field(default=None, max_length=80)
    lang: Literal["", "en", "zh"] = ""


class ChatGuidanceBody(APIBody):
    message: str = Field(min_length=1, max_length=200_000)
    clientRequestId: str | None = Field(default=None, max_length=200)


class ChatToTaskBody(APIBody):
    title: str | None = Field(default=None, max_length=160)
    goal: str | None = Field(default=None, max_length=50_000)


class ChatForkBody(APIBody):
    messageId: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200_000)


class MemoryCreateBody(APIBody):
    content: str = Field(min_length=1, max_length=200_000)
    category: Literal["preference", "project", "habit", "fact", "conversation"] = "fact"
    source: Literal["conversation", "knowledge", "manual", "agent", "other"] = "manual"
    confidence: Literal["", "high", "medium", "low"] = ""
    tags: list[Any] | str | None = None


class MemoryUpdateBody(APIBody):
    content: str | None = Field(default=None, min_length=1, max_length=200_000)
    category: Literal["preference", "project", "habit", "fact", "conversation"] | None = None
    source: Literal["conversation", "knowledge", "manual", "agent", "other"] | None = None
    confidence: Literal["", "high", "medium", "low"] | None = None
    tags: list[Any] | str | None = None
    stale: bool | None = None


class ScheduleCreateBody(APIBody):
    prompt: str = Field(min_length=1, max_length=200_000)
    schedule_type: Literal["once", "cron", "interval"]
    schedule_value: str = Field(min_length=1, max_length=500)
    next_run: str | None = Field(default=None, max_length=200)
    chat_id: int = -1


class ScheduleUpdateBody(APIBody):
    prompt: str | None = Field(default=None, min_length=1, max_length=200_000)
    schedule_type: Literal["once", "cron", "interval"] | None = None
    schedule_value: str | None = Field(default=None, min_length=1, max_length=500)
    next_run: str | None = Field(default=None, max_length=200)
    status: Literal["active", "paused"] | None = None


class KnowledgeUpdateBody(APIBody):
    title: str | None = Field(default=None, max_length=1000)
    tags: list[Any] | None = None
    summary: str | None = Field(default=None, max_length=100_000)
    entity_id: str | None = Field(default=None, max_length=500)


class GoalLoopPreviewBody(APIBody):
    goal: str = Field(min_length=1, max_length=50_000)
    basePlanDefinitionRevision: int = Field(ge=0)
    permissionMode: Literal["auto", "full_access"] = "auto"
    fullAccessConfirmed: bool = False
    reflectionMode: Literal["standard", "proactive", "frequent"] = "proactive"
    maxRuntimeHours: float = Field(default=2, ge=0.5, le=24)
    maxRepairRounds: int = Field(default=3, ge=0, le=10)


class GoalLoopStartBody(APIBody):
    draftId: str = Field(min_length=1, max_length=500)


class GoalLoopLimitsBody(APIBody):
    maxRuntimeHours: float | None = Field(default=None, ge=0.5, le=24)
    maxRepairRounds: int | None = Field(default=None, ge=0, le=10)
    reflectionMode: Literal["standard", "proactive", "frequent"] | None = None
