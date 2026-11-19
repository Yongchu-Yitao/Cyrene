"""Public vocabulary for user Hooks. Events select when, actions select what."""
from __future__ import annotations

from collections.abc import Mapping

EVENT_DESCRIPTIONS = {
    "SessionStart": "Session context is being initialized.",
    "TurnStart": "A user turn is starting.",
    "ModelStart": "A main model request is being prepared.",
    "PreToolUse": "A tool invocation is pending.",
    "PostToolUse": "A tool has returned a result or error.",
    "ContextChange": "A context-tree change has committed.",
    "ContextUsed": "Context usage was reported.",
    "SessionEnd": "A run reached its final outcome.",
    "ConversationTurnCommitted": "A public conversation turn was saved.",
    "Stop": "The active run is being stopped.",
}

ACTION_GUIDE = """
Choose actions independently of the selected event. Every event accepts every action.
The script can do local work. Print {} for no runtime actions. For one action use a JSON
object; for multiple actions use {"actions":[...]} in the requested order.
{"message":"literal text"} displays the text directly in chat, without calling the model.
{"context":"instruction"} gives the Agent a prompt. Use this when the user wants the Agent
to speak, reason or act. Use message when they want the system to show exact text.
{"decision":"allow","target":"tool"} continues processing.
{"decision":"modify","target":"tool","arguments":{"path":"notes.txt"}} replaces arguments using the target tool schema.
{"decision":"block","target":"tool","reason":"optional explanation"} stops the target.
{"decision":"ask","target":"tool","question":"question for the user"} pauses for input.
Select the decision target from the user's intent, independently of the event: tool
(pending tool invocation), result (delivery of a tool result), or model (main model request).
When the selected target is not pending yet, the decision applies once at its next
occurrence in this conversation. For modify, arguments is the replacement tool argument
object, {"value":...} for a result, or a model request patch with messages and/or tools.
Decisions never undo committed work. Multiple actions may include messages, contexts,
edits and a decision. A block takes precedence over an ask or allow for the same target.
"""

class ActionError(ValueError):
    pass


def validate_action(raw):
    if not isinstance(raw, dict):
        raise ActionError("each action must be a JSON object")
    item = dict(raw)
    allowed = {"message", "context"}
    for key in ("message", "context", "reason"):
        if key in item and not isinstance(item[key], str):
            raise ActionError(f"{key} must be a string")
    if "decision" in item or "arguments" in item:
        decision = item.get("decision", "modify")
        if not isinstance(decision, str) or decision.strip().lower() not in {"allow", "modify", "block", "ask"}:
            raise ActionError("decision must be allow, modify, block, or ask")
        decision = decision.strip().lower()
        item["decision"] = decision
        allowed.update({"decision", "target"})
        target = item.get("target", "tool")
        if target not in ("tool", "result", "model"):
            raise ActionError("target must be tool, result, or model")
        if decision in ("allow", "modify"):
            allowed.add("arguments")
            if "arguments" in item or decision == "modify":
                args = item.get("arguments")
                if not isinstance(args, dict):
                    raise ActionError("arguments must be an object")
                if target == "result" and set(args) != {"value"}:
                    raise ActionError("result modification requires arguments with a value field")
                if target == "model":
                    if not args or args.keys() - {"messages", "tools"}:
                        raise ActionError("model modification supports messages and tools")
                    for key, values in args.items():
                        if not isinstance(values, list) or not all(isinstance(v, Mapping) for v in values):
                            raise ActionError(f"model {key} must be an array of objects")
        if decision == "block":
            allowed.add("reason")
        if decision == "ask":
            allowed.add("question")
            question = item.get("question")
            if isinstance(question, str):
                question = {"text": question}
            if not isinstance(question, dict) or not isinstance(question.get("text"), str) or not question["text"].strip():
                raise ActionError("ask requires a question with nonempty text")
            if question.keys() - {"text", "options", "allow_custom"}:
                raise ActionError("question accepts text, options and allow_custom")
            if "options" in question and not isinstance(question["options"], list):
                raise ActionError("question options must be an array")
            if "allow_custom" in question and not isinstance(question["allow_custom"], bool):
                raise ActionError("question allow_custom must be boolean")
            item["question"] = {**question, "status": "awaiting_user", "kind": "hook"}
    unknown = item.keys() - allowed
    if unknown:
        raise ActionError("unsupported fields: " + ", ".join(sorted(unknown)))
    return item


def combine_decisions(actions):
    """Keep edits and a single effective gate. Allow cannot override a block."""
    result = {}
    for item in actions:
        if "arguments" in item:
            if item.get("target") == "model":
                result.setdefault("arguments", {}).update(item["arguments"])
            else:
                result["arguments"] = item["arguments"]
        decision = item.get("decision")
        if decision == "block":
            result.update(decision="block", reason=item.get("reason") or "Blocked by Hook")
            result.pop("question", None)
        elif decision == "ask" and result.get("decision") != "block":
            question = dict(item["question"])
            if result.get("question"):
                question = {"text": result["question"]["text"] + "\n" + question["text"],
                            "status": "awaiting_user", "kind": "hook", "allow_custom": True}
            result.update(decision="ask", question=question)
        elif decision and result.get("decision") not in ("ask", "block"):
            result["decision"] = "modify" if "arguments" in result else "allow"
    return result
