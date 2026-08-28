"""Plugin-owned subagent spawn policy and TurnStart context mount."""

from __future__ import annotations

from agent.hook import TURN_START, HookEvent
from agent.plugin import PluginSetupContext
from cyrene.runtime import settings_store

_DEFAULT_POLICY = "conservative"
_SUPPORTED_POLICIES = frozenset({"aggressive", "conservative", "off"})
_HOOK_ID = "cyrene-subagent-spawn-policy-turn-start"
_LEGACY_HOOK_ID = "cyrene-subagent-spawn-policy-session-start"
_PLUGIN_ID = "cyrene_subagent.spawn_policy"


def current_spawn_policy() -> str:
    """Return the configured policy normalized to a supported value."""

    value = str(
        settings_store.get("spawn_policy", _DEFAULT_POLICY) or _DEFAULT_POLICY
    ).strip().lower()
    return value if value in _SUPPORTED_POLICIES else _DEFAULT_POLICY


def spawn_policy_context(policy: str) -> str:
    """Build the main-Agent instruction contributed for one spawn policy."""

    normalized = str(policy or "").strip().lower()
    if normalized == "aggressive":
        return (
            "## Subagent Spawn Policy\n"
            "Current policy: aggressive.\n"
            "- Proactively look for work that can be split into independent "
            "parallel subtasks.\n"
            "- When parallel research, verification, or implementation would "
            "clearly help, discover and invoke `spawn_subagent` from the "
            "`cyrene_subagent` Plugin early.\n"
            "- Favor delegation when task boundaries are clean and multiple "
            "tracks can advance at once."
        )
    if normalized == "off":
        return (
            "## Subagent Spawn Policy\n"
            "Current policy: off.\n"
            "- Do not invoke `spawn_subagent`.\n"
            "- Complete the task in single-Agent mode.\n"
            "- Subagent spawning remains unavailable until the user changes "
            "this setting."
        )
    return (
        "## Subagent Spawn Policy\n"
        "Current policy: conservative.\n"
        "- Invoke `spawn_subagent` only when parallelism is clearly beneficial.\n"
        "- Honor an explicit user request for separate subagents when it is "
        "compatible with the task and available capabilities.\n"
        "- Prefer delegation for well-bounded independent work, not tightly "
        "coupled or trivial work.\n"
        "- If the benefit is marginal, keep the work in the main Agent."
    )


def setup_spawn_policy_context(context: PluginSetupContext) -> None:
    """Mount the configured policy into enabled main-Agent sessions."""

    if context.agent_id != "main":
        return

    async def mount_spawn_policy(_event: HookEvent) -> dict[str, str]:
        return {
            "context": spawn_policy_context(current_spawn_policy()),
            "context_kind": "subagent_policy",
            "context_source": "cyrene_subagent",
        }

    existing = {hook.id: hook for hook in context.hooks.list()}
    if _LEGACY_HOOK_ID in existing:
        context.hooks.unregister(_LEGACY_HOOK_ID)
    if _HOOK_ID in existing:
        context.hooks.bind_plugin(
            _PLUGIN_ID,
            mount_spawn_policy,
            replace=True,
        )
        return
    context.hooks.register(
        TURN_START,
        mount_spawn_policy,
        plugin_id=_PLUGIN_ID,
        hook_id=_HOOK_ID,
        root_only=True,
        failure_policy="closed",
    )


__all__ = [
    "current_spawn_policy",
    "setup_spawn_policy_context",
    "spawn_policy_context",
]
