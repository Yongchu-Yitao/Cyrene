"""Complete Plugin-owned backend for the Workbench knowledge interface."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from cyrene.core.plugin import PluginContext

from .content import extract_text, parse_bibliography
from .retrieve import search_knowledge as retrieve_knowledge
from .store import KnowledgeStore
from .workspace import (
    WorkspaceNotFoundError,
    WorkspaceRequiredError,
    resolve_workspace as _default_workspace_resolver,
)

logger = logging.getLogger(__name__)

_EDITABLE_FIELDS = {
    "abstract",
    "citekey",
    "date_text",
    "doi",
    "isbn",
    "issue",
    "item_type",
    "language",
    "pages",
    "publisher",
    "tags",
    "title",
    "url",
    "venue",
    "volume",
    "year",
}


def _default_data_root() -> Path:
    from cyrene.platform.paths import DATA_DIR

    return Path(DATA_DIR).expanduser().resolve() / "plugin_data" / "cyrene_knowledge"


def creator_label(creators: Sequence[Mapping[str, Any]]) -> str:
    names: list[str] = []
    for creator in creators:
        literal = str(creator.get("name") or "").strip()
        first = str(creator.get("first_name") or "").strip()
        last = str(creator.get("last_name") or "").strip()
        label = literal or " ".join(value for value in (first, last) if value)
        if label:
            names.append(label)
    return ", ".join(names)


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _snippet(text: Any, length: int = 160) -> str:
    normalized = " ".join(str(text or "").split())
    return normalized[:length] + ("…" if len(normalized) > length else "")


def _render_citation(item: Mapping[str, Any], style: str) -> str:
    authors = creator_label(list(item.get("creators") or [])) or "Unknown author"
    title = str(item.get("title") or "Untitled")
    year = str(item.get("year") or "n.d.")
    venue = str(item.get("venue") or item.get("publisher") or "").strip()
    doi = str(item.get("doi") or "").strip()
    url = str(item.get("url") or "").strip()
    source = f" https://doi.org/{doi}" if doi else (f" {url}" if url else "")
    normalized_style = str(style or "ieee").casefold()
    if normalized_style in {"apa", "apa7"}:
        return f"{authors} ({year}). {title}. {venue}.{source}".strip()
    if normalized_style in {"mla", "mla9"}:
        return f'{authors}. "{title}." {venue}, {year}.{source}'.strip()
    if normalized_style in {"chicago", "chicago-author-date"}:
        return f'{authors}. {year}. "{title}." {venue}.{source}'.strip()
    return f'{authors}, "{title}," {venue}, {year}.{source}'.strip()


def _render_bibtex(item: Mapping[str, Any]) -> str:
    item_type = {
        "book": "book",
        "bookSection": "incollection",
        "conferencePaper": "inproceedings",
        "journalArticle": "article",
        "report": "techreport",
        "thesis": "phdthesis",
    }.get(str(item.get("item_type") or ""), "misc")
    citekey = str(item.get("citekey") or "").strip()
    if not citekey:
        first_author = creator_label(list(item.get("creators") or [])[:1]) or "item"
        stem = re.sub(r"[^A-Za-z0-9]+", "", first_author.split()[-1]) or "item"
        citekey = f"{stem}{item.get('year') or ''}"
    author_values = []
    for creator in item.get("creators") or []:
        name = str(creator.get("name") or "").strip()
        if not name:
            name = " ".join(
                value
                for value in (
                    str(creator.get("first_name") or "").strip(),
                    str(creator.get("last_name") or "").strip(),
                )
                if value
            )
        if name:
            author_values.append(name)
    fields = {
        "title": item.get("title"),
        "author": " and ".join(author_values),
        "year": item.get("year"),
        "journal": item.get("venue"),
        "publisher": item.get("publisher"),
        "volume": item.get("volume"),
        "number": item.get("issue"),
        "pages": item.get("pages"),
        "doi": item.get("doi"),
        "url": item.get("url"),
    }
    body = ",\n".join(f"  {key} = {{{str(value).replace('{', '').replace('}', '')}}}" for key, value in fields.items() if _has_value(value))
    return f"@{item_type}{{{citekey},\n{body}\n}}"


class KnowledgeService:
    """Backend used directly by HTTP routes, Agent tools, and Workbench hooks."""

    def __init__(
        self,
        data_directory: str | Path | None = None,
        *,
        workspace_resolver: Callable[[str], str] | None = None,
        zotero_settings: Callable[[], Mapping[str, Any]] | None = None,
        legacy_store_directory: str | Path | None = None,
        project_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        initialize_store: bool = True,
    ) -> None:
        base = Path(data_directory).expanduser().resolve() if data_directory else _default_data_root()
        self.store = KnowledgeStore(base, initialize=initialize_store)
        self._workspace_resolver = workspace_resolver or _default_workspace_resolver
        if zotero_settings is None:
            from .zotero_settings import get_zotero_settings

            zotero_settings = get_zotero_settings
        self._zotero_settings = zotero_settings
        self._legacy_store_directory = (
            Path(legacy_store_directory).expanduser().resolve()
            if legacy_store_directory is not None
            else None
        )
        self._project_state_provider = project_state_provider
        self._tasks: set[asyncio.Task[Any]] = set()
        self._reembed: dict[str, dict[str, Any]] = {}

    def resolve_workspace(self, value: str) -> str:
        return self._workspace_resolver(str(value or "").strip())

    def _context_workspace(self, context: PluginContext) -> str:
        data = context.data
        run_context = data.get("run_context")
        run_context = run_context if isinstance(run_context, Mapping) else {}
        for key in ("workspace_id", "project_id"):
            value = str(data.get(key) or run_context.get(key) or "").strip()
            if value:
                return self.resolve_workspace(value)
        session_id = str(data.get("session_id") or run_context.get("session_id") or "").strip()
        if not session_id:
            raise WorkspaceRequiredError("Knowledge tools require context.data.project_id, workspace_id, or session_id")
        from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_session

        project_id = str(resolve_workbench_project_id_for_session(session_id) or "").strip()
        if not project_id:
            raise WorkspaceNotFoundError(f"Workbench session is not attached to a project: {session_id}")
        return self.resolve_workspace(project_id)

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _embedding_identity() -> tuple[bool, str, int]:
        from . import embeddings

        configured = embeddings.is_configured()
        model, dimensions = embeddings.current_identity()
        if not configured or not model or dimensions <= 0:
            return False, "cyrene-hash-v1", 128
        return True, model, dimensions

    async def _query_embedding(
        self,
        query: str,
    ) -> tuple[list[float] | None, str, int]:
        if not str(query or "").strip():
            return None, "cyrene-hash-v1", 128
        configured, model, dimensions = self._embedding_identity()
        if not configured:
            return None, model, dimensions
        from . import embeddings

        try:
            vectors = await embeddings.embed_texts([query], input_type="query")
            return vectors[0], model, dimensions
        except Exception:
            logger.warning(
                "Configured knowledge embedding query failed; using lexical/hash fallback",
                exc_info=True,
            )
            return None, "cyrene-hash-v1", 128

    async def _refresh_embeddings(self, workspace: str) -> int:
        configured, model, dimensions = self._embedding_identity()
        if not configured:
            return 0
        from . import embeddings

        updated = 0
        while True:
            chunks = await asyncio.to_thread(
                self.store.embedding_chunks,
                workspace,
                model,
                dimensions,
                limit=64,
            )
            if not chunks:
                return updated
            vectors = await embeddings.embed_texts(
                [chunk["content"] for chunk in chunks],
                input_type="document",
            )
            applied = await asyncio.to_thread(
                self.store.apply_embeddings,
                workspace,
                model,
                dimensions,
                [
                    (chunk["id"], vector)
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
            # Chunks can be replaced or deleted while inference is running.
            # The next pass sees their replacements, so a short update is safe.
            updated += applied

    def _start_embedding_refresh(
        self,
        workspace: str,
        *,
        invalidate: bool = False,
    ) -> dict[str, Any]:
        current = self._reembed.get(workspace) or {}
        if current.get("running"):
            current["rerun"] = True
            current["invalidate"] = bool(current.get("invalidate") or invalidate)
            return dict(current)
        state: dict[str, Any] = {
            "running": True,
            "updated": 0,
            "error": "",
            "rerun": False,
            "invalidate": invalidate,
        }
        self._reembed[workspace] = state

        async def run() -> None:
            try:
                while True:
                    should_invalidate = bool(state.get("invalidate"))
                    state["invalidate"] = False
                    state["rerun"] = False
                    if should_invalidate:
                        await asyncio.to_thread(
                            self.store.invalidate_embeddings,
                            workspace,
                        )
                    state["updated"] += await self._refresh_embeddings(workspace)
                    if not state.get("rerun") and not state.get("invalidate"):
                        break
            except Exception as exc:
                state["error"] = str(exc)
                logger.exception("Knowledge embedding refresh failed for %s", workspace)
            finally:
                state.pop("rerun", None)
                state.pop("invalidate", None)
                state["running"] = False

        self._track(asyncio.create_task(run()))
        return dict(state)

    async def list_documents(
        self,
        context: PluginContext,
        *,
        status: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        workspace = self._context_workspace(context)
        return await asyncio.to_thread(
            self.store.list_documents,
            workspace,
            status=str(status or ""),
            limit=max(1, min(int(limit or 100), 500)),
        )

    async def search_knowledge(
        self,
        context: PluginContext,
        query: str,
        *,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        workspace = self._context_workspace(context)
        needle = str(query or "").strip()
        if not needle:
            raise ValueError("query cannot be empty")
        query_vector, model, dimensions = await self._query_embedding(needle)
        self._start_embedding_refresh(workspace)
        return await retrieve_knowledge(
            self.store,
            workspace,
            needle,
            limit=limit,
            query_vector=query_vector,
            embedding_model=model,
            embedding_dimensions=dimensions,
        )

    async def list_library_items(
        self,
        context: PluginContext,
        *,
        query: str = "",
        status: str = "",
        collection_id: str = "",
        tag: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return await self.items(
            self._context_workspace(context),
            q=query,
            status=status,
            collection=collection_id,
            tag=tag,
            limit=limit,
        )

    async def search_library(
        self,
        context: PluginContext,
        query: str,
        *,
        limit: int = 8,
        status: str = "",
        tag: str = "",
    ) -> list[dict[str, Any]]:
        workspace = self._context_workspace(context)
        needle = str(query or "").strip()
        if not needle:
            raise ValueError("query cannot be empty")
        query_vector, model, dimensions = await self._query_embedding(needle)
        self._start_embedding_refresh(workspace)
        return await asyncio.to_thread(
            self.store.search_library,
            workspace,
            needle,
            limit=max(1, min(int(limit or 8), 30)),
            status=str(status or "").strip(),
            tag=str(tag or "").strip(),
            query_vector=query_vector,
            embedding_model=model,
            embedding_dimensions=dimensions,
        )

    async def update_library_metadata(
        self,
        context: PluginContext,
        paper_id: str,
        metadata: Mapping[str, Any],
        *,
        overwrite: bool = False,
    ) -> tuple[dict[str, Any] | None, list[str], list[str]]:
        workspace = self._context_workspace(context)
        paper_id = str(paper_id or "").strip()
        if not paper_id:
            raise ValueError("paper_id cannot be empty")
        if not isinstance(metadata, Mapping) or not metadata:
            raise ValueError("metadata must contain at least one verified field")
        item = await asyncio.to_thread(self.store.get_item, workspace, paper_id)
        if item is None:
            raise LookupError(f"paper_id was not found: {paper_id}")
        patch: dict[str, Any] = {}
        skipped: list[str] = []
        for field in _EDITABLE_FIELDS:
            if field not in metadata or not _has_value(metadata[field]):
                continue
            if overwrite or not _has_value(item.get(field)):
                patch[field] = metadata[field]
            else:
                skipped.append(field)
        if isinstance(metadata.get("authors"), list):
            if overwrite or not item.get("creators"):
                patch["creators"] = [{"name": str(value).strip(), "creator_type": "author"} for value in metadata["authors"] if str(value or "").strip()]
            else:
                skipped.append("authors")
        updated = await asyncio.to_thread(self.store.update_item, workspace, paper_id, patch) if patch else item
        if patch:
            self._start_embedding_refresh(workspace)
        return updated, sorted(patch), sorted(set(skipped))

    async def items(self, workspace: str, **filters: Any) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        year = filters.get("year")
        year_value = int(year) if str(year or "").isdigit() else None
        return await asyncio.to_thread(
            self.store.list_items,
            workspace,
            q=str(filters.get("q") or ""),
            collection=str(filters.get("collection") or ""),
            status=str(filters.get("status") or ""),
            tag=str(filters.get("tag") or ""),
            item_type=str(filters.get("item_type") or ""),
            file_type_filter=str(filters.get("file_type") or ""),
            year=year_value,
            starred=filters.get("starred"),
            trash=bool(filters.get("trash")),
            sort=str(filters.get("sort") or "updated_at"),
            order=str(filters.get("order") or "desc"),
            limit=max(1, min(int(filters.get("limit") or 200), 1000)),
            offset=max(0, int(filters.get("offset") or 0)),
        )

    async def create_item(self, workspace: str, body: Mapping[str, Any]) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        item = await asyncio.to_thread(self.store.create_item, workspace, body)
        self._start_embedding_refresh(workspace)
        return item

    async def get_item(self, workspace: str, item_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.get_item, self.resolve_workspace(workspace), item_id)

    async def update_item(
        self,
        workspace: str,
        item_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        workspace = self.resolve_workspace(workspace)
        if "title" in body and not str(body.get("title") or "").strip():
            raise ValueError("title cannot be empty")
        if "reading_status" in body and body.get("reading_status") not in {
            "unread",
            "reading",
            "read",
        }:
            raise ValueError("reading_status must be unread, reading, or read")
        item = await asyncio.to_thread(self.store.update_item, workspace, item_id, body)
        if item is not None and any(key in body for key in ("title", "abstract", "content")):
            self._start_embedding_refresh(workspace)
        return item

    async def delete_items(
        self,
        workspace: str,
        item_ids: Sequence[str],
        *,
        permanent: bool,
    ) -> int:
        return await asyncio.to_thread(
            self.store.delete_items,
            self.resolve_workspace(workspace),
            item_ids,
            permanent=permanent,
        )

    async def delete_item(self, workspace: str, item_id: str, *, permanent: bool) -> bool:
        return bool(await self.delete_items(workspace, [item_id], permanent=permanent))

    async def restore_item(self, workspace: str, item_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.restore_item, self.resolve_workspace(workspace), item_id)

    async def stats(self, workspace: str) -> dict[str, int]:
        return await asyncio.to_thread(self.store.stats, self.resolve_workspace(workspace))

    async def tags(self, workspace: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_tags, self.resolve_workspace(workspace))

    async def collections(self, workspace: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_collections, self.resolve_workspace(workspace))

    async def create_collection(
        self,
        workspace: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self.store.create_collection, self.resolve_workspace(workspace), body)

    async def update_collection(
        self,
        workspace: str,
        collection_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if "name" in body and not str(body.get("name") or "").strip():
            raise ValueError("name cannot be empty")
        return await asyncio.to_thread(
            self.store.update_collection,
            self.resolve_workspace(workspace),
            collection_id,
            body,
        )

    async def delete_collection(self, workspace: str, collection_id: str) -> bool:
        return await asyncio.to_thread(self.store.delete_collection, self.resolve_workspace(workspace), collection_id)

    async def create_note(
        self,
        workspace: str,
        item_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.create_note, self.resolve_workspace(workspace), item_id, body)

    async def update_note(
        self,
        workspace: str,
        note_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.update_note, self.resolve_workspace(workspace), note_id, body)

    async def delete_note(self, workspace: str, note_id: str) -> bool:
        return await asyncio.to_thread(self.store.delete_note, self.resolve_workspace(workspace), note_id)

    async def create_relation(
        self,
        workspace: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self.store.create_relation, self.resolve_workspace(workspace), body)

    async def delete_relation(self, workspace: str, relation_id: str) -> bool:
        return await asyncio.to_thread(self.store.delete_relation, self.resolve_workspace(workspace), relation_id)

    async def _write_upload(
        self,
        workspace: str,
        filename: str,
        content: bytes,
        content_type: str,
        item_id: str = "",
        *,
        provider: str = "cyrene",
        provider_library_id: str = "",
        provider_key: str = "",
    ) -> dict[str, Any]:
        target = self.store.managed_path(workspace, filename)
        await asyncio.to_thread(target.write_bytes, content)
        text, pages = await asyncio.to_thread(extract_text, target, content_type)
        created_item_id = ""
        try:
            if item_id:
                item = await asyncio.to_thread(self.store.get_item, workspace, item_id)
                if item is None:
                    raise LookupError("item not found")
            elif provider_key:
                item, was_created = await asyncio.to_thread(
                    self.store.upsert_provider_item,
                    workspace,
                    {
                        "title": Path(filename).stem or filename,
                        "item_type": "document",
                        "provider": provider,
                        "provider_library_id": provider_library_id,
                        "provider_item_key": provider_key,
                    },
                )
                if was_created:
                    created_item_id = str(item["id"])
            else:
                item = await asyncio.to_thread(
                    self.store.create_item,
                    workspace,
                    {"title": Path(filename).stem or filename, "item_type": "document"},
                )
                created_item_id = str(item["id"])
            await asyncio.to_thread(
                self.store.add_attachment,
                workspace,
                str(item["id"]),
                filename=filename,
                path=target,
                content_type=content_type,
                indexed_text=text,
                page_count=pages,
                provider=provider,
                provider_library_id=provider_library_id,
                provider_key=provider_key,
            )
            value = await asyncio.to_thread(self.store.get_item, workspace, str(item["id"]))
            if value is None:
                raise RuntimeError("uploaded item disappeared")
            self._start_embedding_refresh(workspace)
            return value
        except Exception:
            target.unlink(missing_ok=True)
            if created_item_id:
                await asyncio.to_thread(
                    self.store.delete_items,
                    workspace,
                    [created_item_id],
                    permanent=True,
                )
            raise

    async def upload(
        self,
        workspace: str,
        files: Sequence[Any],
        item_id: str = "",
    ) -> list[dict[str, Any]]:
        workspace = self.resolve_workspace(workspace)
        result: list[dict[str, Any]] = []
        for upload in files:
            filename = Path(str(getattr(upload, "filename", "") or "attachment")).name
            content = await upload.read()
            records = await asyncio.to_thread(parse_bibliography, filename, content)
            if records:
                for record in records:
                    result.append(
                        await asyncio.to_thread(
                            self.store.create_item,
                            workspace,
                            {**record, "title": str(record.get("title") or Path(filename).stem)},
                        )
                    )
                continue
            content_type = str(getattr(upload, "content_type", "") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
            result.append(await self._write_upload(workspace, filename, content, content_type, item_id=item_id))
        if result:
            self._start_embedding_refresh(workspace)
        return result

    async def import_records(
        self,
        workspace: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        records = body.get("items")
        if not isinstance(records, list):
            raise ValueError("items must be a list")
        created: list[dict[str, Any]] = []
        for raw in records:
            if not isinstance(raw, Mapping) or not str(raw.get("title") or "").strip():
                continue
            created.append(await asyncio.to_thread(self.store.create_item, workspace, dict(raw)))
        if created:
            self._start_embedding_refresh(workspace)
        return {"imported": len(created), "created": len(created), "items": created}

    async def raw(
        self,
        workspace: str,
        item_id: str,
        attachment_id: str = "",
    ) -> dict[str, Any] | None:
        workspace = self.resolve_workspace(workspace)
        attachment = await asyncio.to_thread(
            self.store.primary_attachment,
            workspace,
            item_id,
            attachment_id,
        )
        if not attachment:
            return None
        path = Path(str(attachment.get("path") or "")).expanduser().resolve()
        try:
            path.relative_to(self.store.files_root)
        except ValueError as exc:
            raise PermissionError("attachment is outside the Plugin data directory") from exc
        if not path.is_file():
            return None
        return {
            "path": path,
            "filename": str(attachment.get("filename") or path.name),
            "media_type": str(attachment.get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
        }

    async def citation(self, workspace: str, item_id: str, style: str) -> dict[str, Any] | None:
        item = await self.get_item(workspace, item_id)
        if not item:
            return None
        return {
            "citation": _render_citation(item, style),
            "bibtex": _render_bibtex(item),
            "citekey": str(item.get("citekey") or ""),
            "style": str(style or "ieee"),
        }

    async def mark_read(
        self,
        workspace: str,
        *,
        attachment_url: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        match = re.search(r"/items/([^/]+)/raw", unquote(urlsplit(attachment_url).path))
        item_id = match.group(1) if match else ""
        if not item_id and file_name:
            listed = await self.items(workspace, q=file_name, limit=20)
            candidate = next((item for item in listed["items"] if str(item.get("attachment_name") or "") == file_name), None)
            item_id = str(candidate.get("id") or "") if candidate else ""
        item = await asyncio.to_thread(self.store.mark_read, workspace, item_id) if item_id else None
        return {"ok": bool(item), "item": item}

    async def search(self, workspace: str, query: str, limit: int) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        needle = str(query or "").strip()
        query_vector, model, dimensions = await self._query_embedding(needle)
        self._start_embedding_refresh(workspace)
        results = await asyncio.to_thread(
            self.store.search_library,
            workspace,
            needle,
            limit=max(1, min(int(limit or 20), 100)),
            query_vector=query_vector,
            embedding_model=model,
            embedding_dimensions=dimensions,
        )
        return {"results": results}

    async def embedding_status(self, workspace: str) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        model_configured, model, dimensions = self._embedding_identity()
        status = await asyncio.to_thread(
            self.store.embedding_status,
            workspace,
            configured=model_configured,
            model=model,
            dimensions=dimensions,
        )
        status["model_configured"] = model_configured
        status["reembed"] = dict(self._reembed.get(workspace) or {"running": False, "updated": 0, "error": ""})
        return status

    async def reembed(self, workspace: str) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        return {
            "ok": True,
            "reembed": self._start_embedding_refresh(workspace, invalidate=True),
        }

    async def zotero_status(self, workspace: str) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        from .zotero import ZoteroClient

        settings = dict(self._zotero_settings())
        client = ZoteroClient(str(settings.get("base_url") or ""))
        status = await client.status()
        status.update(
            {
                "auto_sync": bool(settings.get("auto_sync")),
                "copy_attachments": bool(settings.get("copy_attachments", True)),
                "default_library_id": "0",
                "default_library_type": "user",
                "sync_sources": [],
            }
        )
        status["sync_sources"] = await asyncio.to_thread(self.store.list_sync_states, workspace, "zotero")
        return status

    async def zotero_collections(self, library_id: str, library_type: str) -> dict[str, Any]:
        from .zotero import ZoteroClient

        client = ZoteroClient(str(self._zotero_settings().get("base_url") or ""))
        records, version = await client.collections(library_id, library_type)
        return {"collections": records, "library_version": version}

    async def zotero_sync(
        self,
        workspace: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace)
        from .zotero import sync_zotero

        settings = dict(self._zotero_settings())
        result = await sync_zotero(
            self,
            workspace,
            base_url=str(settings.get("base_url") or ""),
            library_id=str(body.get("library_id") or "0"),
            library_type=str(body.get("library_type") or "user"),
            collection_key=str(body.get("collection_key") or ""),
            copy_attachments=bool(settings.get("copy_attachments", True)),
        )
        self._start_embedding_refresh(workspace)
        return result

    async def search_workbench(self, query: str, limit: int) -> list[dict[str, Any]]:
        query_vector, model, dimensions = await self._query_embedding(query)
        hits = await retrieve_knowledge(
            self.store,
            None,
            query,
            limit=limit,
            query_vector=query_vector,
            embedding_model=model,
            embedding_dimensions=dimensions,
        )
        try:
            from cyrene.workbench.sessions.context import read_projects

            names = {str(project.get("id") or ""): str(project.get("name") or "Workspace") for project in read_projects()}
        except Exception:
            names = {}
        seen: set[tuple[str, str]] = set()
        results: list[dict[str, Any]] = []
        for hit in hits:
            identity = (str(hit["workspace"]), str(hit["document_id"]))
            if identity in seen:
                continue
            seen.add(identity)
            results.append(
                {
                    "id": str(hit["document_id"]),
                    "type": "knowledge",
                    "title": str(hit.get("document_name") or "Knowledge"),
                    "snippet": _snippet(hit.get("content")),
                    "projectId": str(hit["workspace"]),
                    "projectName": names.get(str(hit["workspace"]), "Workspace"),
                    "docId": str(hit["document_id"]),
                    "chunkId": str(hit["chunk_id"]),
                    "score": hit.get("score"),
                }
            )
            if len(results) >= max(1, int(limit or 10)):
                break
        return results

    async def register_attachments(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        if not items:
            return
        from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_session

        workspace = self.resolve_workspace(str(resolve_workbench_project_id_for_session(session_id) or ""))
        for raw in items:
            source = Path(str(raw.get("path") or "")).expanduser()
            if not source.is_file():
                continue
            content = await asyncio.to_thread(source.read_bytes)
            await self._write_upload(
                workspace,
                str(raw.get("name") or source.name),
                content,
                str(raw.get("content_type") or mimetypes.guess_type(source.name)[0] or "application/octet-stream"),
                provider="workbench_attachment",
                provider_library_id=session_id,
                provider_key=str(raw.get("id") or source.resolve()),
            )

    async def resolve_library_file_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        body = dict(raw or {})
        nested = body.get("file") if isinstance(body.get("file"), dict) else {}
        source_kind = str(body.get("sourceKind") or nested.get("sourceKind") or "")
        item_id = str(body.get("libraryItemId") or nested.get("libraryItemId") or "")
        workspace_raw = str(body.get("ownerProjectId") or nested.get("ownerProjectId") or "")
        if source_kind != "library" or not item_id or not workspace_raw:
            return body
        try:
            workspace = self.resolve_workspace(workspace_raw)
        except (WorkspaceRequiredError, WorkspaceNotFoundError):
            return body
        attachment = await asyncio.to_thread(self.store.primary_attachment, workspace, item_id)
        if not attachment:
            return body
        path = Path(str(attachment.get("path") or "")).resolve()
        try:
            path.relative_to(self.store.files_root)
        except ValueError:
            return body
        if not path.is_file():
            return body
        name = str(attachment.get("filename") or path.name)
        content_type = str(attachment.get("content_type") or "application/octet-stream")
        resolved_file = {
            **nested,
            "id": str(nested.get("id") or f"library:{workspace}:{item_id}"),
            "name": name,
            "path": str(path),
            "url": str(body.get("url") or nested.get("url") or ""),
            "content_type": content_type,
            "size": int(path.stat().st_size),
            "kind": str(nested.get("kind") or "file"),
            "sourceKind": "library",
            "libraryItemId": item_id,
            "ownerProjectId": workspace,
        }
        return {
            **body,
            "name": name,
            "title": str(body.get("title") or name),
            "path": str(path),
            "content_type": content_type,
            "size": int(path.stat().st_size),
            "sourceKind": "library",
            "libraryItemId": item_id,
            "ownerProjectId": workspace,
            "file": resolved_file,
        }

    def delete_workspace(self, workspace: str) -> None:
        try:
            normalized = self.resolve_workspace(workspace)
        except (WorkspaceRequiredError, WorkspaceNotFoundError):
            normalized = str(workspace or "").strip()
        if normalized:
            self.store.delete_workspace(normalized)

    def storage_paths(self) -> dict[str, tuple[Path, ...]]:
        """Expose Plugin-owned data to the generic Settings storage scanner."""

        from .local_models import MODEL_ROOT
        from .ocr import OCR_CACHE
        from .opencv_runtime import OPENCV_ROOT

        legacy_databases = (
            tuple(sorted(self._legacy_store_directory.glob("kb_*.db*")))
            if self._legacy_store_directory is not None
            else ()
        )
        return {
            "knowledge": (self.store.root, OCR_CACHE, *legacy_databases),
            "local_models": (MODEL_ROOT,),
            "opencv_runtime": (OPENCV_ROOT,),
        }

    def backup_sources(self) -> dict[str, tuple[tuple[Path, str], ...]]:
        """Describe the durable post-migration knowledge store for backups."""

        legacy_databases = (
            tuple(
                (path, f"store/{path.name}")
                for path in sorted(self._legacy_store_directory.glob("kb_*.db"))
            )
            if self._legacy_store_directory is not None
            else ()
        )
        return {
            "files": legacy_databases,
            "directories": (
                (
                    self.store.root,
                    "data/plugin_data/cyrene_knowledge",
                ),
            ),
        }

    async def startup(self) -> None:
        await asyncio.to_thread(self.store.initialize)
        legacy_root = self._legacy_store_directory
        if (
            legacy_root is not None
            and legacy_root.is_dir()
            and any(legacy_root.glob("kb_*.db"))
        ):
            provider = self._project_state_provider
            if provider is None:
                from cyrene.workbench.sessions.context import read_project_state

                provider = read_project_state
            try:
                project_state = provider()
                from .legacy_migration import migrate_legacy_knowledge

                await asyncio.to_thread(
                    migrate_legacy_knowledge,
                    self.store,
                    legacy_root,
                    project_state if isinstance(project_state, Mapping) else {},
                )
            except Exception:
                # A damaged legacy database must not make the new Plugin store
                # or the rest of the application unavailable. The source is
                # left untouched so a later release or manual repair can retry.
                logger.exception("Legacy knowledge migration failed")

    def local_model_status(self) -> dict[str, Any]:
        from . import local_models

        return local_models.status()

    def start_local_model_download(self, model_id: str) -> dict[str, Any]:
        from . import local_models

        if model_id == "qwen3-embedding-0.6b":
            # Import registers the post-download inference health check.
            from . import local_onnx as _local_onnx  # noqa: F401
            self._ensure_local_embedding_configuration()

        result = local_models.start_download(model_id)
        if model_id == "qwen3-embedding-0.6b":
            download = local_models._TASKS.get(model_id)

            async def refresh_when_ready() -> None:
                try:
                    if download is not None and not download.done():
                        await asyncio.shield(download)
                    if not local_models.is_ready(model_id):
                        return
                    workspaces = await asyncio.to_thread(
                        self.store.embedding_workspaces
                    )
                    for workspace in workspaces:
                        self._start_embedding_refresh(workspace)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Local embedding model became ready but vector refresh failed"
                    )

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # The production HTTP path always has an event loop; keeping
                # this callable safe also supports synchronous administration.
                pass
            else:
                self._track(loop.create_task(refresh_when_ready()))
        return result

    @staticmethod
    def _ensure_local_embedding_configuration() -> None:
        from cyrene.core.plugin import application_plugin_service

        service = application_plugin_service("model_configuration")
        if service is None:
            return
        configuration = service.get_model_configuration()
        connection = next(
            (
                item
                for item in configuration.get("connections") or []
                if item.get("adapter") == "local_onnx" and item.get("enabled", True)
            ),
            None,
        )
        if connection is None:
            return
        profiles = [dict(item) for item in configuration.get("profiles") or []]
        profile = next(
            (
                item
                for item in profiles
                if item.get("connection_id") == connection.get("id")
                and item.get("model") == "qwen3-embedding-0.6b"
            ),
            None,
        )
        if profile is None:
            base_id = f"{connection['id']}:qwen3-embedding-0.6b"
            used = {str(item.get("id") or "") for item in profiles}
            profile_id = base_id
            suffix = 2
            while profile_id in used:
                profile_id = f"{base_id}:{suffix}"
                suffix += 1
            profile = {
                "id": profile_id,
                "connection_id": connection["id"],
                "model": "qwen3-embedding-0.6b",
                "name": "Qwen3 Embedding 0.6B",
                "enabled": True,
                "capabilities": ["embedding"],
                "dimensions": 1024,
            }
            profiles.append(profile)
        routes = {
            name: list((configuration.get("routes") or {}).get(name) or [])
            for name in ("primary", "secondary", "vision", "embedding")
        }
        if profile["id"] not in routes["embedding"]:
            routes["embedding"].append(profile["id"])
        service.save_model_configuration({
            "version": configuration.get("version", 1),
            "connections": configuration.get("connections") or [],
            "profiles": profiles,
            "routes": routes,
        })

    async def delete_local_model(self, model_id: str) -> dict[str, Any]:
        from . import local_models

        return await local_models.delete_model(model_id)

    async def delete_all_local_models(self) -> None:
        from . import local_models

        await local_models.delete_all_models()

    def is_local_model_ready(self, model_id: str) -> bool:
        from . import local_models

        return local_models.is_ready(model_id)

    def local_model_dir(self, model_id: str) -> Path:
        from . import local_models

        return local_models.model_dir(model_id)

    def local_model_provider(self, model_id: str) -> str:
        from . import local_models

        return local_models.sherpa_provider(model_id)

    def register_local_model_resetter(self, model_id: str, callback: Callable[[], None]) -> None:
        from . import local_models

        local_models.register_resetter(model_id, callback)

    @property
    def ocr_model_id(self) -> str:
        from .ocr import MODEL_ID

        return MODEL_ID

    async def recognize_image(self, path: str | Path) -> str:
        from .ocr import recognize

        return await recognize(str(path))

    def extract_file_text(self, path: str | Path, content_type: str = "") -> tuple[str, int]:
        return extract_text(Path(path), content_type)

    async def embed_local_texts(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        from .local_onnx import embed_texts

        return await embed_texts(texts, query=query)

    async def shutdown(self) -> None:
        tasks = set(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        await asyncio.to_thread(self.store.close)

    async def reset_data(self) -> None:
        await self.shutdown()
        self._reembed.clear()
        await asyncio.to_thread(self.store.reset)

    async def prepare_data_reset(self) -> dict[str, bool]:
        """Clear Plugin-owned cache roots outside the main data directory."""

        import shutil

        from .ocr import OCR_CACHE
        from . import opencv_runtime

        await self.shutdown()
        await self.delete_all_local_models()
        await opencv_runtime.delete_all()
        await asyncio.to_thread(shutil.rmtree, OCR_CACHE, True)
        return {
            "knowledge_cache": True,
            "local_models": True,
            "opencv_runtime": True,
        }


def create_knowledge_service(
    data_directory: str | Path | None = None,
    *,
    workspace_resolver: Callable[[str], str] | None = None,
    zotero_settings: Callable[[], Mapping[str, Any]] | None = None,
    legacy_store_directory: str | Path | None = None,
    project_state_provider: Callable[[], Mapping[str, Any]] | None = None,
    initialize_store: bool = True,
) -> KnowledgeService:
    return KnowledgeService(
        data_directory,
        workspace_resolver=workspace_resolver,
        zotero_settings=zotero_settings,
        legacy_store_directory=legacy_store_directory,
        project_state_provider=project_state_provider,
        initialize_store=initialize_store,
    )


__all__ = [
    "KnowledgeService",
    "WorkspaceNotFoundError",
    "WorkspaceRequiredError",
    "create_knowledge_service",
    "creator_label",
]
