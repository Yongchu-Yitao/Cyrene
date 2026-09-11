"""DeepSeek Chat Completions compatibility, owned entirely by the provider.

Contract: https://api-docs.deepseek.com/guides/thinking_mode/
Forced tool choices require non-thinking mode. Tools-enabled thinking requests
must replay original reasoning even for assistant turns without tool calls.
"""

from collections.abc import Mapping
from copy import deepcopy


def _reasoning(message):
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str):
            return value
    details = message.get("reasoning_details")
    if isinstance(details, list) and details and all(
        isinstance(item, Mapping)
        and item.get("type") == "reasoning.text"
        and isinstance(item.get("text"), str)
        for item in details
    ):
        return "".join(item["text"] for item in details)
    return None


def _signature(message):
    content = message.get("content") or ""
    if not isinstance(content, str):
        return None
    calls = message.get("tool_calls") or []
    ids = tuple(str(call.get("id") or "") for call in calls)
    # Empty or transformed assistant records cannot identify a stored response.
    if not content or ids:
        return None
    return content


def restore_reasoning(messages, context, model):
    """Recover only exact, unambiguous responses on this conversation's path.

    Tool turns already carry reasoning_details through the core projection.
    Plain assistant turns omit it, but retain the original text in the tree.
    Never fetch another branch or guess reasoning for summaries/foreign models.
    """
    result = deepcopy(messages)
    missing = [m for m in result if m.get("role") == "assistant" and _reasoning(m) is None]
    if not missing or context.tree is None or not context.tree_id or not context.node_id:
        return result
    path = context.tree.get_path(context.tree_id, context.node_id)
    from cyrene.core.context.tasks import state_from

    task_state = state_from(path)
    active_task = task_state.get("active") if task_state else None
    candidates = {}
    for node in path:
        value = node.value
        if not isinstance(value, Mapping) or value.get("role") != "assistant":
            continue
        signature = _signature(value)
        if signature is None:
            continue
        eligible = value.get("model") == model and (
            not task_state or value.get("task_context_id") == active_task
        )
        reasoning = _reasoning(value) if eligible else None
        candidates.setdefault(signature, set()).add(reasoning)
    for message in missing:
        matches = candidates.get(_signature(message), set())
        if len(matches) == 1 and None not in matches:
            message["reasoning_content"] = next(iter(matches))
    return result


def adapt_payload(payload):
    result = deepcopy(payload)
    missing = False
    for message in result.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        reasoning = _reasoning(message)
        if reasoning is None:
            missing = True
        else:
            message["reasoning_content"] = reasoning
        # These are Cyrene/OpenRouter representations, not DeepSeek fields.
        message.pop("reasoning", None)
        message.pop("reasoning_details", None)
    choice = result.get("tool_choice")
    forced = choice == "required" or isinstance(choice, Mapping)
    if forced or (result.get("tools") and missing):
        # Do not weaken forced permission decisions or fabricate lost reasoning.
        result["thinking"] = {"type": "disabled"}
        result.pop("reasoning_effort", None)
    return result


def adapt_provider_payload(payload, provider_id):
    return adapt_payload(payload) if provider_id == "deepseek" else payload


def restore_provider_reasoning(arguments, provider_id, messages, context, model):
    if provider_id != "deepseek":
        return arguments
    return {**arguments, "messages": restore_reasoning(messages, context, model)}
