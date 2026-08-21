"""Public Workbench-runtime operations consumed by Chat application adapters."""

from __future__ import annotations

from typing import Any

from cyrene.agent.state import session_state_file
from cyrene.model_runtime.client import approx_token_count
from cyrene.workbench import runtime as _runtime


class WorkbenchRuntimeFacade:
    """Narrow, named facade over the legacy Workbench runtime module."""

    @property
    def awaiting_user_sentinel(self) -> str:
        return _runtime._AWAITING_USER_SENTINEL

    @property
    def chat_id(self) -> str:
        return _runtime._CHAT_ID

    def read_store(self) -> dict[str, Any]:
        return _runtime._read_workbench_store()

    def write_store(self, payload: dict[str, Any]) -> None:
        _runtime._write_workbench_store(payload)

    def find_project(self, *args: Any, **kwargs: Any):
        return _runtime._workbench_find_project(*args, **kwargs)

    def find_project_lightweight(self, *args: Any, **kwargs: Any):
        return _runtime._workbench_find_project_lightweight(*args, **kwargs)

    def project_data_key(self, *args: Any, **kwargs: Any) -> str:
        return _runtime._workbench_project_data_key(*args, **kwargs)

    def resolve_workspace_dir(self, *args: Any, **kwargs: Any):
        return _runtime._workbench_resolve_workspace_dir(*args, **kwargs)

    def get_model(self) -> str:
        return _runtime._get_model()

    def normalize_attachments(self, *args: Any, **kwargs: Any):
        return _runtime._workbench_normalize_attachments(*args, **kwargs)

    def build_public_attachment_payload(self, *args: Any, **kwargs: Any):
        return _runtime.build_public_attachment_payload(*args, **kwargs)

    async def register_attachments_kb(self, *args: Any, **kwargs: Any):
        return await _runtime._workbench_register_attachments_kb(*args, **kwargs)

    def attachment_prompt_block(self, *args: Any, **kwargs: Any) -> str:
        return _runtime._attachment_prompt_block(*args, **kwargs)

    def pending_question_for(self, *args: Any, **kwargs: Any):
        return _runtime._workbench_pending_question_for(*args, **kwargs)

    async def answer_pending(self, *args: Any, **kwargs: Any):
        return await _runtime._workbench_answer_pending(*args, **kwargs)

    def reply_stream_chunks(self, *args: Any, **kwargs: Any):
        return _runtime._reply_stream_chunks(*args, **kwargs)

    def new_session(self, *args: Any, **kwargs: Any):
        return _runtime._workbench_new_session(*args, **kwargs)

    def delete_chat_session(self, *args: Any, **kwargs: Any):
        return _runtime._delete_chat_session(*args, **kwargs)

    async def check_budget_gate(self, *args: Any, **kwargs: Any):
        return await _runtime._check_budget_gate(*args, **kwargs)

    def schedule_capture(self, *args: Any, **kwargs: Any):
        return _runtime.schedule_capture(*args, **kwargs)

    @staticmethod
    def session_state_file(session_id: str = ""):
        return session_state_file(session_id)

    @staticmethod
    def approx_token_count(text: Any) -> int:
        return approx_token_count(str(text or ""))


__all__ = ["WorkbenchRuntimeFacade"]
