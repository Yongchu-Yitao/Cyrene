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
        labels = {
            str(item.get("label") or "").strip()
            for item in pending.get("options") or () if isinstance(item, Mapping)
        }
        negative = answer.strip().lower() in {"拒绝", "否", "不允许", "no", "n", "deny", "cancel"}
        if not fingerprint or answer not in labels or negative:
            return
        with self.lock:
            if "本次会话" in answer or "始终" in answer:
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



def permission_question(
    request, *, tool_name, operation, kind, requires_human, fingerprint,
    session_fingerprint, round_id, client_request_id,
):
    """Render the boundary's exact options and its explicitly declared scope."""
    path_hint = str(request.get("path_hint") or "").strip()
    reason = str(request.get("reason") or "").strip()
    detail = f"\n📂 目标：{path_hint}" if path_hint else ""
    why = f"\n💡 原因：{reason}" if reason else ""
    authored_options = request.get("options")
    if requires_human:
        options = (
            [str(item) for item in authored_options if str(item).strip()]
            if isinstance(authored_options, list) and authored_options
            else ["允许这次", "拒绝"]
            if bool(request.get("single_use"))
            else ["允许这次", "本次会话内总是允许", "拒绝"]
        )
    else:
        options = (
            [str(item) for item in authored_options if str(item).strip()]
            if isinstance(authored_options, list) and authored_options
            else ["在本次会话同意", "同意一次", "拒绝"]
        )
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
