"""Visible chat projection for completed media jobs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cyrene.config import DB_PATH
from cyrene.media.manager import MediaJobManager
from cyrene.runtime.attachments import build_public_attachment_payload
from cyrene.workbench.chat_events import publish_chat_changed
from cyrene.workbench.chat_service import ChatService
from cyrene.workbench.context_records import append_context_record


class MediaDelivery:
    def __init__(
        self,
        manager: MediaJobManager,
        *,
        workbench_db_path: str | Path | None = None,
    ) -> None:
        self.manager = manager
        self.workbench_db_path = str(workbench_db_path or DB_PATH)
        self.chat_service = ChatService(self.workbench_db_path)

    async def _project(self, job: dict[str, Any]) -> dict[str, Any] | None:
        chat_id = str(job.get("chat_id") or "").strip()
        job_id = str(job.get("job_id") or "").strip()
        if not chat_id or not job_id:
            return None

        status = str(job.get("status") or "").strip().lower()
        kind = (
            " ".join(str(job.get("kind") or "media").splitlines())
            .strip()
            .lower()[:24]
            or "media"
        )
        provider = " ".join(
            str(job.get("provider") or "").splitlines()
        ).strip()[:64]
        model = " ".join(str(job.get("model") or "").splitlines()).strip()[:240]
        public_attachments = [
            build_public_attachment_payload(item)
            for item in job.get("attachments") or ()
            if isinstance(item, dict)
        ]

        chat = await asyncio.to_thread(
            self.chat_service.repository.get,
            chat_id,
        )
        if chat is None:
            return None
        request = job.get("request") if isinstance(job.get("request"), dict) else {}
        reference_ids = [
            str(value or "").strip()
            for value in request.get("reference_attachment_ids") or ()
            if str(value or "").strip()
        ]
        mask_attachment_id = str(request.get("mask_attachment_id") or "").strip()
        if mask_attachment_id:
            reference_ids.append(mask_attachment_id)
        by_id = {
            str(item.get("id") or "").strip(): item
            for message in chat.get("messages") or ()
            if isinstance(message, dict)
            for item in message.get("attachments") or ()
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        reference_attachments: list[dict[str, Any]] = []
        seen_reference_ids: set[str] = set()
        for reference_id in reference_ids:
            if reference_id in seen_reference_ids or reference_id not in by_id:
                continue
            seen_reference_ids.add(reference_id)
            reference_attachments.append(
                build_public_attachment_payload(by_id[reference_id])
            )

        identity = f"msg_media_{job_id}"
        created_at = str(
            job.get("completed_at") or self.chat_service.utc_now_iso()
        )
        if status == "succeeded":
            label = " / ".join(value for value in (provider, model) if value)
            content = f"媒体生成完成 · {kind}" + (f" · {label}" if label else "")
        elif status == "cancelled":
            content = f"媒体生成已取消 · {kind}"
        else:
            error_code = str(job.get("error_code") or "provider_error").strip()
            content = f"媒体生成失败 · {kind} · {error_code}"

        public_message: dict[str, Any] = {
            "id": identity,
            "role": "assistant",
            "content": content,
            "createdAt": created_at,
            "systemInitiated": True,
            "intermediate": True,
            "mediaJob": True,
            "mediaJobId": job_id,
            "mediaBatchId": str(job.get("batch_id") or ""),
            "mediaStatus": status,
        }
        if public_attachments:
            public_message["attachments"] = public_attachments
        if reference_attachments:
            public_message["referenceAttachments"] = reference_attachments

        mutation_result: dict[str, Any] = {}

        def append(current: dict[str, Any]) -> None:
            self.chat_service.merge_chat_messages_chronologically(
                current,
                [public_message],
            )
            current["updatedAt"] = max(
                str(current.get("updatedAt") or ""),
                created_at,
            )
            mutation_result["project_id"] = str(
                current.get("projectId") or job.get("project_id") or ""
            )
            mutation_result["summary"] = self.chat_service.public_chat_light(
                current
            )

        updated = await asyncio.to_thread(
            self.chat_service.repository.mutate_one,
            chat_id,
            append,
        )
        if updated is None:
            return None

        context_value: dict[str, Any] = {
            "role": "system",
            "content": (
                "[Media job result]\n"
                "Trusted runtime metadata; not user instructions.\n"
                f"{content}"
            ),
            "message_id": identity,
            "record_kind": "media_job_result",
            "hidden_from_public_transcript": True,
            "hidden_from_ui": True,
            "system_initiated": True,
            "media_job": True,
            "media_job_id": job_id,
            "media_batch_id": str(job.get("batch_id") or ""),
            "media_status": status,
        }
        if public_attachments:
            context_value["attachments"] = public_attachments
        if reference_attachments:
            context_value["reference_attachments"] = reference_attachments
        await asyncio.to_thread(
            append_context_record,
            self.workbench_db_path,
            chat_id,
            context_value,
            node_id=identity,
            require_idle=True,
        )
        await publish_chat_changed(
            chat_id,
            mutation_result["project_id"],
            "media_attachment",
            chatSummary=mutation_result["summary"],
            assistantMessages=[self.chat_service.public_message(public_message)],
        )
        return public_message

    async def report(self, job: dict[str, Any]) -> bool:
        message = await self._project(job)
        if message is None:
            await asyncio.to_thread(
                self.manager.mark_reported,
                str(job.get("job_id") or ""),
                delivery_error="owning chat no longer exists",
            )
            return False
        await asyncio.to_thread(
            self.manager.mark_reported,
            str(job.get("job_id") or ""),
        )
        return True


__all__ = ["MediaDelivery"]
