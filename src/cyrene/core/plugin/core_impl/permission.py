"""Fixed LLM permission reviewer for model-produced Plugin calls."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from ...hook import PRE_TOOL_USE, HookEvent, HookRegistration

PERMISSION_PLUGIN_ID = "core.permission"

PERMISSION_DECIDE_TOOL = {
    "type": "function",
    "function": {
        "name": "decide",
        "description": "Return the permission decision. Call this tool exactly once.",
        "parameters": {
            "type": "object",
            "properties": {
                "approve": {"type": "boolean"},
                "rationale": {"type": "string"},
            },
            "required": ["approve", "rationale"],
            "additionalProperties": False,
        },
    },
}

PERMISSION_DECIDE_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": "decide"},
}

PERMISSION_BATCH_DECIDE_TOOL = {
    "type": "function",
    "function": {
        "name": "decide",
        "description": "Return one permission decision for every indexed tool call.",
        "parameters": {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer", "minimum": 0},
                            "approve": {"type": "boolean"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["index", "approve", "rationale"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["decisions"],
            "additionalProperties": False,
        },
    },
}

PERMISSION_SYSTEM_PROMPT = (
    "你是 Cyrene 的安全审核员。主 agent 正在为用户完成任务，碰到了一个超出默认安全范围的操作，"
    "需要你裁决是否放行。你必须自主决定，绝不能把问题抛回给用户。\n\n"
    "放行原则：\n"
    "- 操作明显服务于用户的请求、且非破坏性 → approve。\n"
    "- 高风险操作要谨慎 deny：删除多个文件 / 递归删除（rm -rf）、写入系统目录或 workspace 之外的敏感位置、"
    "包含命令替换（$() 或反引号）等无法静态验证路径的 shell、可能造成不可逆后果的命令。\n"
    "- 当 deny 时，请在 rationale 里简述风险，主 agent 会据此改用更安全的做法。\n"
    "只调用 decide 工具返回结果。"
)

PERMISSION_BATCH_SYSTEM_PROMPT = (
    PERMISSION_SYSTEM_PROMPT
    + "\n本次请求包含多个带索引的操作。必须为每个索引返回且只返回一个决定，"
    "不得遗漏或重复；只调用一次 decide。"
)

PermissionModel: TypeAlias = Callable[
    [str, Mapping[str, Any]],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]
UserRequestProvider: TypeAlias = Callable[
    [HookEvent],
    str | Awaitable[str],
]
PermissionAction: TypeAlias = Literal["allow", "review", "confirm", "deny"]


@dataclass(frozen=True, slots=True)
class PermissionRequirement:
    """Deterministic boundary result evaluated before semantic review."""

    action: PermissionAction = "review"
    rationale: str = ""
    question: Mapping[str, Any] | None = None


PermissionPolicyProvider: TypeAlias = Callable[
    [HookEvent],
    PermissionRequirement | Awaitable[PermissionRequirement],
]
PermissionReviewObserver: TypeAlias = Callable[
    [Sequence[HookEvent], Sequence["PermissionDecision"]],
    Any | Awaitable[Any],
]


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    approve: bool
    rationale: str


class PermissionReviewPlugin:
    """A fail-closed PreToolUse Hook backed by the model component."""

    def __init__(
        self,
        model: PermissionModel,
        *,
        user_request: UserRequestProvider | None = None,
        policy: PermissionPolicyProvider | None = None,
        on_review: PermissionReviewObserver | None = None,
    ) -> None:
        if not callable(model):
            raise TypeError("permission model must be callable")
        if on_review is not None and not callable(on_review):
            raise TypeError("permission review observer must be callable")
        self._model = model
        self._user_request = user_request
        self._policy = policy
        self._on_review = on_review

    async def __call__(self, event: HookEvent) -> dict[str, str]:
        return (await self.review_batch((event,)))[0]

    async def review_batch(
        self,
        events: Sequence[HookEvent],
    ) -> tuple[dict[str, str], ...]:
        normalized_events = tuple(events)
        if not normalized_events:
            return ()
        outputs: list[dict[str, Any] | None] = [None] * len(normalized_events)
        review_events: list[HookEvent] = []
        review_positions: list[int] = []
        tools: list[dict[str, Any]] = []
        for position, event in enumerate(normalized_events):
            payload = event.payload if isinstance(event.payload, Mapping) else {}
            tool = payload.get("tool") if isinstance(payload, Mapping) else None
            tool = tool if isinstance(tool, Mapping) else {}
            try:
                requirement = PermissionRequirement()
                if self._policy is not None:
                    policy_value = self._policy(event)
                    if inspect.isawaitable(policy_value):
                        policy_value = await policy_value
                    if not isinstance(policy_value, PermissionRequirement):
                        raise TypeError("permission policy must return PermissionRequirement")
                    requirement = policy_value
                if requirement.action == "allow":
                    outputs[position] = {
                        "decision": "allow",
                        "reason": requirement.rationale or "Within the default safety boundary.",
                    }
                    continue
                if requirement.action == "deny":
                    outputs[position] = {
                        "decision": "block",
                        "reason": requirement.rationale or "Denied by deterministic policy.",
                    }
                    continue
                if requirement.action == "confirm":
                    outputs[position] = {
                        "decision": "ask",
                        "reason": requirement.rationale or "User confirmation is required.",
                        "question": dict(requirement.question or {}),
                    }
                    continue
                if requirement.action != "review":
                    raise ValueError(f"unsupported permission action: {requirement.action}")
            except Exception as exc:
                outputs[position] = {
                    "decision": "block",
                    "reason": f"permission policy failed: {exc}",
                }
                continue
            index = len(review_events)
            review_events.append(event)
            review_positions.append(position)
            tools.append({
                "index": index,
                "name": str(tool.get("name") or ""),
                "arguments": dict(tool.get("arguments") or {}),
                "permission": (
                    dict(payload.get("permission"))
                    if isinstance(payload.get("permission"), Mapping)
                    else {}
                ),
            })
        if not review_events:
            return tuple(dict(output or {}) for output in outputs)
        request_text = ""
        try:
            if self._user_request is not None:
                request_value = self._user_request(review_events[0])
                if inspect.isawaitable(request_value):
                    request_value = await request_value
                request_text = str(request_value or "")
            raw = self._model(
                PERMISSION_BATCH_SYSTEM_PROMPT,
                {
                    "tree_id": review_events[0].tree_id,
                    "tools": tools,
                    "user_request": request_text,
                },
            )
            if inspect.isawaitable(raw):
                raw = await raw
            decisions = self._parse_batch(raw, expected=len(tools))
        except Exception as exc:
            decisions = tuple(
                PermissionDecision(
                    False,
                    f"permission review failed: {exc}",
                )
                for _event in review_events
            )
        await self._notify_review(review_events, decisions)
        for position, decision in zip(review_positions, decisions):
            outputs[position] = {
                "decision": "allow" if decision.approve else "block",
                "reason": decision.rationale,
            }
        return tuple(dict(output or {}) for output in outputs)

    async def _notify_review(
        self,
        events: Sequence[HookEvent],
        decisions: Sequence[PermissionDecision],
    ) -> None:
        if self._on_review is None:
            return
        try:
            observed = self._on_review(events, decisions)
            if inspect.isawaitable(observed):
                await observed
        except Exception:
            # Audit/UI observation must never change the permission decision.
            logger.exception("Permission review observer failed")

    @staticmethod
    def _parse(raw: Mapping[str, Any]) -> PermissionDecision:
        if not isinstance(raw, Mapping):
            raise TypeError("permission model must return an object")
        if set(raw) != {"approve", "rationale"}:
            raise ValueError("permission decision must contain approve and rationale")
        approve = raw.get("approve")
        rationale = raw.get("rationale")
        if type(approve) is not bool or not isinstance(rationale, str):
            raise TypeError("permission decision has invalid field types")
        return PermissionDecision(approve, rationale.strip())

    @classmethod
    def _parse_batch(
        cls,
        raw: Mapping[str, Any],
        *,
        expected: int,
    ) -> tuple[PermissionDecision, ...]:
        if expected == 1 and isinstance(raw, Mapping) and "decisions" not in raw:
            return (cls._parse(raw),)
        if not isinstance(raw, Mapping) or set(raw) != {"decisions"}:
            raise ValueError("batch permission decision must contain decisions")
        values = raw.get("decisions")
        if not isinstance(values, list) or len(values) != expected:
            raise ValueError("batch permission decision count does not match tool count")
        indexed: dict[int, PermissionDecision] = {}
        for value in values:
            if not isinstance(value, Mapping) or set(value) != {
                "index", "approve", "rationale"
            }:
                raise ValueError("batch permission decision has invalid fields")
            index = value.get("index")
            if type(index) is not int or not 0 <= index < expected or index in indexed:
                raise ValueError("batch permission decision has an invalid index")
            indexed[index] = cls._parse({
                "approve": value.get("approve"),
                "rationale": value.get("rationale"),
            })
        if set(indexed) != set(range(expected)):
            raise ValueError("batch permission decision is missing an index")
        return tuple(indexed[index] for index in range(expected))

    def registration(
        self,
        *,
        hook_id: str = "core-permission-review",
    ) -> HookRegistration:
        """Return the binding to pass in a tree's ``initial_hooks``."""

        return HookRegistration(
            event=PRE_TOOL_USE,
            plugin_id=PERMISSION_PLUGIN_ID,
            plugin=self,
            hook_id=hook_id,
            failure_policy="block",
        )


__all__ = [
    "PERMISSION_BATCH_DECIDE_TOOL",
    "PERMISSION_BATCH_SYSTEM_PROMPT",
    "PERMISSION_DECIDE_TOOL",
    "PERMISSION_DECIDE_TOOL_CHOICE",
    "PERMISSION_PLUGIN_ID",
    "PERMISSION_SYSTEM_PROMPT",
    "PermissionDecision",
    "PermissionModel",
    "PermissionPolicyProvider",
    "PermissionRequirement",
    "PermissionReviewObserver",
    "PermissionReviewPlugin",
    "UserRequestProvider",
]
