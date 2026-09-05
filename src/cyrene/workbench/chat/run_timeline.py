"""The public transcript projection shared by live delivery and persistence.

Records are allocated at their source boundary, never by comparing their text.
An activity's membership closes at prose/guidance boundaries; its existing
children may still finish later without moving the activity.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any


class RunTimeline:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.records: dict[str, dict[str, Any]] = {}
        self.sources: dict[str, str] = {}
        self.tools: dict[str, str] = {}
        self.reasonings: dict[str, str] = {}
        self.reasoning_id = ""
        self.reply_id = ""
        self.activity_id = ""
        self.status = "running"
        self.seen: set[str] = set()
        self.counter = 0
        self.revision = 0

    def _new(self, kind: str, at: str, source: str = "") -> dict[str, Any]:
        self.counter += 1
        if self.records:
            previous_at = next(reversed(self.records.values()))["createdAt"]
            if datetime.fromisoformat(at.replace("Z", "+00:00")) < datetime.fromisoformat(previous_at.replace("Z", "+00:00")):
                at = previous_at
        identity = f"{self.run_id}:{kind}:{self.counter}"
        record: dict[str, Any] = {
            "id": identity, "role": "assistant", "content": "",
            "runId": self.run_id, "roundId": self.run_id,
            "timelineOrder": self.counter, "timelineVersion": 1,
            "createdAt": at, "startedAt": at, "status": "running",
        }
        if kind == "activity":
            record.update(activityCard=True, reasoning="", reasoningActive=False, trace=[], intermediate=True)
        self.records[identity] = record
        if source:
            self.sources[f"{kind}:{source}"] = identity
        return record

    def _activity(self, at: str, source: str) -> dict[str, Any]:
        identity = self.sources.get(f"activity:{source}") if source else self.activity_id
        if identity and identity in self.records and not self.records[identity].get("membershipClosed"):
            return self.records[identity]
        record = self._new("activity", at, source)
        self.activity_id = record["id"]
        return record

    def _reasoning(self, at: str, source: str, *, new: bool = False) -> dict[str, Any]:
        # Membership controls where NEW work belongs, not where an existing
        # stream delivers its remaining deltas/completion. A tool can open a
        # newer card for this source while the preceding reasoning still ends.
        identity = self.reasonings.get(source) if source else self.reasoning_id
        if not new and identity in self.records:
            return self.records[identity]
        if new and not source and self.records.get(self.activity_id, {}).get("trace"):
            self.activity_id = ""
        record = self._activity(at, source)
        if source:
            self.reasonings[source] = record["id"]
        self.reasoning_id = record["id"]
        return record

    def _reply(self, at: str, source: str, *, new: bool = False) -> dict[str, Any]:
        identity = self.sources.get(f"message:{source}") if source else ("" if new else self.reply_id)
        if identity and identity in self.records:
            return self.records[identity]
        for activity in self.records.values():
            if activity.get("activityCard"):
                activity["membershipClosed"] = True
        if self.reply_id and self.reply_id in self.records:
            previous = self.records[self.reply_id]
            previous["intermediate"] = True
            if previous["status"] == "running":
                previous.update(status="completed", endedAt=at)
        self.activity_id = ""
        record = self._new("message", at, source)
        self.reply_id = record["id"]
        return record

    @staticmethod
    def _settle_activity(record: dict[str, Any], at: str) -> None:
        active = record.get("reasoningActive") or any(t.get("status") == "running" for t in record["trace"])
        record["status"] = "running" if active else "completed"
        if active:
            record.pop("endedAt", None)
        else:
            record["endedAt"] = at

    def _apply_intermediate(self, payload: dict[str, Any], at: str) -> None:
        message = payload.get("message") or {}
        identity = str(message.get("id") or "")
        record = self.records.get(identity)
        if record is None:
            record = self._reply(at, identity, new=True)
            if identity:
                allocated = record["id"]
                del self.records[allocated]
                record["id"] = identity
                self.records[identity] = record
                self.sources[f"message:{identity}"] = identity
                self.reply_id = identity
        record.update({k: copy.deepcopy(v) for k, v in message.items() if k not in {"id", "createdAt", "runId", "roundId"}})
        record.update(status="completed", endedAt=at, intermediate=True)
        self.activity_id = ""
        self.reply_id = ""

    def _apply_tool(self, kind: str, payload: dict[str, Any], at: str, source: str, event_id: str) -> None:
        if kind == "permission.reviewed":
            payload = {**payload, "toolCallId": "permission:" + str(payload.get("id") or event_id),
                       "name": "Permission review", "status": "completed" if payload.get("approved") else "failed"}
        call = str(payload.get("toolCallId") or payload.get("tool_call_id") or payload.get("call_id") or "")
        if call:
            owner = self.tools.get(call)
            record = self.records[owner] if owner else self._activity(at, source)
            self.tools[call] = record["id"]
            entry = next((t for t in record["trace"] if t["toolCallId"] == call), None)
            if entry is None:
                entry = {"kind": "permission" if kind == "permission.reviewed" else "tool", "toolCallId": call, "startedAt": at}
                record["trace"].append(entry)
            status = str(payload.get("status") or ("completed" if kind in {"tool.completed", "tool_call_finished"} else "running"))
            if entry.get("status") != "running" and entry.get("endedAt") and status == "running":
                status = str(entry["status"])
            entry.update(text=str(payload.get("name") or payload.get("tool") or entry.get("text") or "tool"), status=status,
                         failed=bool(payload.get("failed") or status in {"failed", "error"}),
                         preview=str(payload.get("outputSummary") or payload.get("inputSummary") or entry.get("preview") or ""))
            for key, candidates in {"input": ("input", "args"), "output": ("output", "result"), "presentation": ("presentation",)}.items():
                for candidate in candidates:
                    if candidate in payload:
                        entry[key] = copy.deepcopy(payload[candidate])
                        break
            if status != "running":
                entry["endedAt"] = at
            self._settle_activity(record, at)

    def _apply_artifact(self, payload: dict[str, Any], at: str, source: str) -> None:
        attachment = payload.get("attachment")
        if not isinstance(attachment, dict):
            attachment = {"id": payload.get("artifactId") or payload.get("id"),
                          "url": payload.get("uri") or payload.get("url"),
                          "name": payload.get("title") or payload.get("name"),
                          "content_type": payload.get("mimeType")}
        if attachment.get("url"):
            record = self._reply(at, source)
            files = record.setdefault("attachments", [])
            key = attachment.get("id") or attachment.get("url")
            files[:] = [file for file in files if (file.get("id") or file.get("url")) != key]
            files.append(copy.deepcopy(attachment))
            record.update(status="completed", endedAt=at)

    def _apply_guidance(self, payload: dict[str, Any], at: str) -> None:
        user = payload.get("userMessage")
        if isinstance(user, dict) and user.get("id"):
            record = self._new("user", at)
            allocated = record["id"]
            record.update(copy.deepcopy(user))
            record.update(status="completed", endedAt=at)
            del self.records[allocated]
            self.records[record["id"]] = record
        for record in self.records.values():
            if record.get("activityCard"):
                record["membershipClosed"] = True
        self.activity_id = ""
        # Keep reply/reasoning owners until their own completion. Guidance
        # closes membership only; it does not cancel or split in-flight output.

    def apply(self, event: dict[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("eventId") or event.get("event_id") or "")
        if event_id and event_id in self.seen:
            return self.patch([])
        if event_id:
            self.seen.add(event_id)
        kind = str(event.get("type") or "")
        payload = {**event, **(event.get("payload") if isinstance(event.get("payload"), dict) else {})}
        at = str(event.get("timestamp") or event.get("createdAt") or datetime.now(timezone.utc).isoformat())
        source = str(payload.get("messageId") or payload.get("sourceId") or "")
        delta_kind = kind in {"reply_delta", "message.delta", "reasoning_delta", "reasoning.delta"}
        # Deltas touch one record; do not copy the accumulated conversation on every token.
        if kind in {"reply_delta", "message.delta"}:
            identity = self.sources.get(f"message:{source}") if source else self.reply_id
        elif delta_kind:
            identity = self.reasonings.get(source) if source else self.reasoning_id
        else:
            identity = None
        if delta_kind and identity in self.records:
            before = dict(self.records)
            before[identity] = copy.deepcopy(before[identity])
        else:
            # Allocating a stream can also close older memberships and mark
            # the preceding reply intermediate; include those changes too.
            before = copy.deepcopy(self.records)
        if kind in {"reply_start", "message.started"}:
            self._reply(at, source, new=True)
        elif kind in {"reply_delta", "message.delta", "reply_done", "message.completed"}:
            record = self._reply(at, source)
            if kind in {"reply_delta", "message.delta"}:
                record["content"] += str(payload.get("delta") or payload.get("text") or "")
            else:
                text = payload.get("response", payload.get("text", payload.get("content")))
                if text is not None:
                    record["content"] = str(text)
                record.update(status="completed", endedAt=at)
        elif kind == "intermediate_message":
            self._apply_intermediate(payload, at)
        elif kind in {"reasoning_start", "reasoning.started", "reasoning_delta", "reasoning.delta", "reasoning_done", "reasoning.completed"}:
            record = self._reasoning(at, source, new=kind in {"reasoning_start", "reasoning.started"})
            if kind in {"reasoning_delta", "reasoning.delta"}:
                record["reasoning"] += str(payload.get("delta") or payload.get("text") or "")
            elif kind in {"reasoning_done", "reasoning.completed"}:
                record["reasoning"] = str(payload.get("response", payload.get("text", record["reasoning"])))
            record["reasoningActive"] = kind not in {"reasoning_done", "reasoning.completed"}
            self._settle_activity(record, at)
        elif kind in {"tool.started", "tool.updated", "tool.completed", "tool_call_started", "tool_call_progress", "tool_call_finished", "permission.reviewed"}:
            self._apply_tool(kind, payload, at, source, event_id)
        elif kind in {"notification.created", "notification"}:
            identity = str(payload.get("id") or event_id)
            record = self._new("notification", at, identity)
            record.update(notificationCard=True, notification=copy.deepcopy(payload), status="completed", intermediate=True, endedAt=at)
        elif kind in {"artifact.created", "artifact.updated"}:
            self._apply_artifact(payload, at, source)
        elif kind == "guidance_received":
            self._apply_guidance(payload, at)
        elif kind in {"permission.requested", "elicitation.requested", "awaiting_user", "run.awaiting_input"}:
            self.status = "waiting"
        elif kind in {"permission.resolved", "elicitation.resolved"}:
            self.status = "running"
        elif kind in {"run.completed", "run_finalizing", "saved", "run.failed", "error", "interrupted", "run.cancelled"}:
            self.status = "failed" if kind in {"error", "run.failed"} else "cancelled" if kind in {"interrupted", "run.cancelled"} else "completed"
            for record in self.records.values():
                if record["status"] == "running":
                    record.update(status=self.status, endedAt=at)
                    record["reasoningActive"] = False
                    for entry in record.get("trace", []):
                        if entry.get("status") == "running":
                            entry.update(status=self.status, endedAt=at)
        changed = [r for key, r in self.records.items() if before.get(key) != r]
        self.revision += 1
        for record in changed:
            record["timelineRevision"] = self.revision
        return self.patch(changed)

    def patch(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        return {"version": 1, "runId": self.run_id, "revision": self.revision,
                "status": self.status, "messages": copy.deepcopy(records)}

    def snapshot(self) -> dict[str, Any]:
        return self.patch(list(self.records.values()))

    def messages(self) -> list[dict[str, Any]]:
        return copy.deepcopy(list(self.records.values()))
