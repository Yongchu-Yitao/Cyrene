"""Persistence boundary for Workbench chat documents."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, cast

from cyrene.workbench import chat as _legacy
from cyrene.workbench.chat_dto import ChatDetailDTO, ChatStoreDTO


class ChatRepository:
    """Own chat-store reads, writes and in-store lookup.

    The legacy functions remain the canonical storage implementation during
    migration, so existing SQLite/JSON compatibility and monkeypatch seams are
    preserved. Route adapters no longer know where or how chats are stored.
    """

    def configure(self, db_path: str) -> None:
        _legacy.configure_store(db_path)

    def read(self) -> ChatStoreDTO:
        return cast(ChatStoreDTO, _legacy._read_chats_store())

    def read_summaries(self) -> ChatStoreDTO:
        return cast(ChatStoreDTO, _legacy._read_chat_summaries_store())

    def get(self, chat_id: str) -> ChatDetailDTO | None:
        return cast(ChatDetailDTO | None, _legacy.get_workbench_chat(chat_id))

    def write(self, payload: ChatStoreDTO | dict[str, Any]) -> None:
        _legacy._write_chats_store(cast(dict[str, Any], payload))

    def write_one(
        self,
        chat: ChatDetailDTO | dict[str, Any],
        *,
        base_chat: ChatDetailDTO | dict[str, Any] | None = None,
    ) -> ChatDetailDTO | None:
        return cast(
            ChatDetailDTO | None,
            _legacy._write_chat_store(
                cast(dict[str, Any], chat),
                base_chat=(
                    cast(dict[str, Any], base_chat)
                    if base_chat is not None
                    else None
                ),
            ),
        )

    def mutate_one(
        self,
        chat_id: str,
        mutation: Callable[[dict[str, Any]], Any],
    ) -> ChatDetailDTO | None:
        return cast(
            ChatDetailDTO | None,
            _legacy._mutate_chat_store(chat_id, mutation),
        )

    def find(
        self,
        payload: ChatStoreDTO | dict[str, Any],
        chat_id: str,
    ) -> ChatDetailDTO | None:
        return cast(
            ChatDetailDTO | None,
            _legacy._find_chat(cast(dict[str, Any], payload), chat_id),
        )

    def mutate(self, mutation: Callable[[ChatStoreDTO], Any]) -> Any:
        with _legacy._CHATS_STORE_JSON_LOCK:
            payload = self.read()
            result = mutation(payload)
            self.write(payload)
            return result

    @property
    def lock(self) -> AbstractContextManager[Any]:
        return _legacy._CHATS_STORE_JSON_LOCK


__all__ = ["ChatRepository"]
