"""Exact and explicitly scoped grants serialized by the session state lock."""
from __future__ import annotations
import hashlib
import json
from collections.abc import Mapping
from typing import Any

class PermissionGrants:
    def __init__(self, lock: Any) -> None:
        self.lock = lock
        self.once: set[str] = set()
        self.session: set[str] = set()

    @staticmethod
    def fingerprint(
        tool_name: str,
        arguments: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> str:
        explicit = str(request.get("fingerprint") or "").strip()
        if explicit:
            return explicit
        payload = {
            "tool": str(tool_name or "").strip(),
            "arguments": dict(arguments),
            "kind": str(request.get("kind") or "scope_elevation"),
            "operation": str(request.get("operation") or ""),
            "path_hint": str(request.get("path_hint") or ""),
            "reason": str(request.get("reason") or "")[:500],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def session_fingerprint(tool_name: str, request: Mapping[str, Any]) -> str:
        """Only boundaries declaring a scope may authorize different arguments."""
        scope = request.get("session_scope")
        if bool(request.get("single_use")) or not isinstance(scope, Mapping) or not scope:
            return ""
        payload = {
            "tool": str(tool_name or "").strip(),
            "kind": str(request.get("kind") or "scope_elevation"),
            "scope": dict(scope),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return "scope:v1:" + hashlib.sha256(encoded).hexdigest()

    def has_session_grant(self, fingerprint: str) -> bool:
        """Check a scope without materializing the exact invocation payload."""
        if not fingerprint:
            return False
        with self.lock:
            return fingerprint in self.session

    def restore(self, root_value: Mapping[str, Any]) -> None:
        with self.lock:
            self.session = {
                str(item).strip()
                for item in root_value.get("permission_session_grants") or ()
                if str(item).strip()
            }

    def consume(self, fingerprint: str, session_fingerprint: str = "") -> bool:
        normalized = str(fingerprint or "").strip()
        if not normalized:
            return False
        with self.lock:
            if normalized in self.session or (
                session_fingerprint and session_fingerprint in self.session
            ):
                return True
            if normalized in self.once:
                self.once.remove(normalized)
                return True
        return False

    def record_answer(self, pending, answer: str, store: Any, tree: Any) -> None:
        """Grant only an offered affirmative choice, under the state lock."""
        permission = pending["permission"]
        fingerprint = str(permission.get("fingerprint") or "").strip()
        choices = [
            item for item in pending.get("options") or ()
            if isinstance(item, Mapping) and item.get("id") == answer
        ]
        if not fingerprint or len(choices) != 1:
            return
        choice = choices[0]
        if choice.get("decision") != "allow":
            return
        scope = choice.get("scope")
        if scope not in {"once", "session"}:
            return
        if scope == "session" and permission.get("single_use"):
            return
        with self.lock:
            if scope == "session":
                grant = self.session_fingerprint(str(permission.get("tool_name") or ""), permission) or fingerprint
                self.session.add(grant)
                self.persist(grant, store, tree)
            else:
                self.once.add(fingerprint)

    def persist(self, fingerprint: str, store: Any, tree: Any) -> None:
        normalized = str(fingerprint or "").strip()
        if not normalized:
            return
        root = store.get_node(tree.id, tree.root_id)
        value = dict(root.value) if isinstance(root.value, Mapping) else {}
        grants = {
            str(item).strip()
            for item in value.get("permission_session_grants") or ()
            if str(item).strip()
        }
        grants.add(normalized)
        value["permission_session_grants"] = sorted(grants)
        store.update_node(tree.id, root.id, value)



def permission_options(once_label="允许这次", *, allow_session=False):
    options = [{"id": "allow_once", "label": once_label, "decision": "allow", "scope": "once"}]
    if allow_session:
        options.append({"id": "allow_session", "label": "本次会话内总是允许", "decision": "allow", "scope": "session"})
    options.append({"id": "deny", "label": "拒绝", "decision": "deny", "scope": "none"})
    return options


def permission_question(
    request, *, tool_name, operation, kind, requires_human, fingerprint,
    session_fingerprint, round_id, client_request_id,
):
    """Render the boundary's exact options and its explicitly declared scope."""
    path_hint = str(request.get("path_hint") or "").strip()
    reason = str(request.get("reason") or "").strip()
    detail = f"\n📂 目标：{path_hint}" if path_hint else ""
    why = f"\n💡 原因：{reason}" if reason else ""
    options = permission_options(allow_session=True)
    authored_options = request.get("options")
    if isinstance(authored_options, list) and authored_options:
        # Custom labels are display-only. Old untyped choices cannot grant access.
        options = [
            dict(item) for item in authored_options
            if isinstance(item, Mapping)
            and item.get("id")
            and ((item.get("decision") == "allow" and item.get("scope") in {"once", "session"})
                 or (item.get("decision") == "deny" and item.get("scope") == "none"))
        ]
        if not options:
            options = [{"id": "deny", "label": "拒绝", "decision": "deny", "scope": "none"}]
    if request.get("single_use"):
        options = [item for item in options if item["scope"] != "session"]
    scope_hint = str(request.get("scope_hint") or "").strip()
    session_hint = (
        "\n选择本次会话允许后，同一工具的同类操作无需再次确认；远程授权仅适用于同一设备和项目。"
        if session_fingerprint else ""
    )
    return {
        "status": "awaiting_user",
        "question_id": f"permission_{fingerprint[:24]}",
        "kind": kind,
        "text": (
            f"⚠️ Agent 尝试执行 {scope_hint}{operation}\n\n"
            f"工具：{tool_name}{detail}{why}\n\n"
            f"请确认是否允许此精确操作。{session_hint}"
        ),
        "options": options,
        "allow_custom": False,
        "round_id": round_id,
        "client_request_id": client_request_id,
        "permission": {
            **request,
            "fingerprint": fingerprint,
            "tool_name": tool_name,
        },
    }
