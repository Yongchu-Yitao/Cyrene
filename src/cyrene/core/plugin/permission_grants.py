"""Exact permission grants, with consumption serialized by the session state lock."""
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

    def consume(self, fingerprint: str) -> bool:
        normalized = str(fingerprint or "").strip()
        if not normalized:
            return False
        with self.lock:
            if normalized in self.session:
                return True
            if normalized in self.once:
                self.once.remove(normalized)
                return True
        return False

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

