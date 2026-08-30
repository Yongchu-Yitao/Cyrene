"""Validated request bodies for the Workbench HTTP API.

Models intentionally ignore unknown fields.  The previous handlers read only
known keys from arbitrary dictionaries, so rejecting every extension field
would be a backwards-incompatible change for desktop clients.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    workspacePath: str | None = Field(default=None, max_length=4096)
    accountTier: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=20_000)
    stack: list[Any] | None = None
    executionActions: list[dict[str, Any]] | None = Field(default=None, max_length=40)
    executionScope: str | None = Field(default=None, max_length=1024)

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
    workspacePath: str | None = Field(default=None, max_length=4096)
    status: Literal["active", "paused", "archived"] | None = None
    model: str | None = Field(default=None, max_length=500)
    accountTier: str | None = Field(default=None, max_length=80)
    context: dict[str, Any] | None = None
    executionActions: list[dict[str, Any]] | None = Field(default=None, max_length=40)
    executionScope: str | None = Field(default=None, max_length=1024)


class NotificationsReadBody(APIBody):
    ids: list[str] = Field(default_factory=list, max_length=500)
    markAll: bool = False


class WorkbenchActivateBody(APIBody):
    projectId: str | None = Field(default=None, max_length=200)


class AgentInputBody(APIBody):
    input: str | None = Field(default=None, max_length=200_000)
    message: str | None = Field(default=None, max_length=200_000)
    clientRequestId: str | None = Field(default=None, max_length=200)
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
    uiInstanceId: str | None = Field(default=None, max_length=200)


class AnswerBody(APIBody):
    question_id: str = Field(min_length=1, max_length=500)
    answer: str | None = Field(default=None, max_length=200_000)
    selected_option: str | None = Field(default=None, max_length=200_000)
    mode: str | None = Field(default=None, max_length=80)
    stream: bool = False
    uiInstanceId: str | None = Field(default=None, max_length=200)


class AgentBindingBody(APIBody):
    """Optional agent binding carried on Workbench chat creation/messages.

    Only ``installationId`` is required to select an installed Agent; the
    remaining fields accept a client-provided snapshot and are normalized by
    the agent runtime.  Absent/legacy requests normalize to the built-in
    Cyrene Agent.
    """

    installationId: str | None = Field(default=None, max_length=200)
    agentId: str | None = Field(default=None, max_length=200)
    displayName: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=100)
    driver: str | None = Field(default=None, max_length=100)
    protocolVersion: int | None = Field(default=None, ge=0)
    externalSessionId: str | None = Field(default=None, max_length=500)
    bindingLocked: bool | None = None


class ModelAccessBody(APIBody):
    """Model source snapshot bound to an Agent conversation (handoff §10).

    ``cyrene_managed`` routes through the Cyrene Model Gateway; ``agent_managed``
    means the Agent uses its own provider/account configuration.  No credentials
    are accepted or stored here.
    """

    mode: Literal["", "cyrene_managed", "agent_managed"] = ""
    profileId: str | None = Field(default=None, max_length=200)
    protocol: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=500)


class ComposerContextActivationsBody(APIBody):
    mcpServers: list[str] = Field(default_factory=list, max_length=50)
    skills: list[str] = Field(default_factory=list, max_length=50)
    pluginPacks: list[str] = Field(default_factory=list, max_length=50)


class ChatCreateBody(APIBody):
    project: str | None = Field(default=None, max_length=200)
    projectId: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=160)
    agent: AgentBindingBody | None = None
    modelAccess: ModelAccessBody | None = None
    soulActive: bool | None = None
    workspaceActive: bool | None = None
    remoteDeviceIds: list[str] = Field(default_factory=list, max_length=50)
    reasoningEffort: Literal["", "low", "medium", "high", "xhigh", "max", "ultra"] = ""
    contextActivations: ComposerContextActivationsBody | None = None


class AgentRequestResponseBody(APIBody):
    response: dict[str, Any]


class SideAgentCreateBody(APIBody):
    quote: str = Field(min_length=1, max_length=12_000)
    title: str | None = Field(default=None, max_length=160)


class ChatUpdateBody(APIBody):
    title: str | None = Field(default=None, max_length=160)
    agent: AgentBindingBody | None = None
    modelAccess: ModelAccessBody | None = None
    agentConfigValues: dict[str, Any] | None = None
    model: str | None = Field(default=None, max_length=500)
    reasoningEffort: Literal["", "low", "medium", "high", "xhigh", "max", "ultra"] = ""
    soulActive: bool | None = None
    workspaceActive: bool | None = None
    workspaceOverride: str | None = Field(default=None, max_length=4096)
    remoteDeviceIds: list[str] | None = Field(default=None, max_length=50)
    contextActivations: ComposerContextActivationsBody | None = None
    activePlan: dict[str, Any] | None = None


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
    clientRequestId: str | None = Field(default=None, max_length=200)
    attachments: list[Any] = Field(default_factory=list, max_length=100)
    command: str | None = Field(default=None, max_length=20_000)
    model: str | None = Field(default=None, max_length=500)
    reasoningEffort: Literal["", "low", "medium", "high", "xhigh", "max", "ultra"] = ""
    stream: bool = False
    retry: bool = False
    forkReplay: bool = False
    mode: str | None = Field(default=None, max_length=80)
    lang: Literal["", "en", "zh"] = ""
    workspaceOverride: str | None = Field(default=None, max_length=4096)
    soulActive: bool | None = None
    workspaceActive: bool | None = None
    remoteDeviceIds: list[str] | None = Field(default=None, max_length=50)
    contextActivations: ComposerContextActivationsBody | None = None
    uiInstanceId: str | None = Field(default=None, max_length=200)
    agent: AgentBindingBody | None = None
    modelAccess: ModelAccessBody | None = None


class ChatGuidanceBody(APIBody):
    message: str = Field(min_length=1, max_length=200_000)
    clientRequestId: str | None = Field(default=None, max_length=200)
    uiInstanceId: str | None = Field(default=None, max_length=200)


class ChatActionBody(APIBody):
    """A `:::button` click forwarded from the frontend (block_actions protocol)."""
    actionId: str = Field(min_length=1, max_length=64)
    value: str = Field(default="", max_length=512)
    messageId: str = Field(min_length=1, max_length=200)
    eventId: str = Field(default="", max_length=200)


class ChatForkBody(APIBody):
    messageId: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200_000)


class ProjectTextFileUpdateBody(APIBody):
    content: str = Field(max_length=2_000_000)
    expectedVersion: str = Field(default="", max_length=128)
    force: bool = False


class ScheduleCreateBody(APIBody):
    prompt: str = Field(min_length=1, max_length=200_000)
    schedule_type: Literal["once", "cron", "interval"]
    schedule_value: str = Field(min_length=1, max_length=500)
    schedule_timezone: str | None = Field(default=None, min_length=1, max_length=100)
    action_type: Literal["message", "agent_task"] = "agent_task"


class ScheduleUpdateBody(APIBody):
    prompt: str | None = Field(default=None, min_length=1, max_length=200_000)
    schedule_type: Literal["once", "cron", "interval"] | None = None
    schedule_value: str | None = Field(default=None, min_length=1, max_length=500)
    schedule_timezone: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["active", "paused"] | None = None
    action_type: Literal["message", "agent_task"] | None = None


class KnowledgeUpdateBody(APIBody):
    title: str | None = Field(default=None, max_length=1000)
    tags: list[Any] | None = None
    summary: str | None = Field(default=None, max_length=100_000)
    entity_id: str | None = Field(default=None, max_length=500)
