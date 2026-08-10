"""Document ingestion pipeline for the knowledge base.

Handles text extraction, chunking, embedding, and indexing.
"""

import asyncio
import hashlib
import json
import re
import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cyrene.runtime.attachments import (
    is_pdf_path,
    is_image_path,
    vision_analysis,
)
from cyrene.model_runtime.client import approx_token_count
from cyrene.knowledge import store, embeddings
from cyrene.knowledge.ingest_tasks import (
    ACTIVE_INDEX_DOCS as _ACTIVE_INDEX_DOCS,
    ACTIVE_INDEX_TASKS as _ACTIVE_INDEX_TASKS,
    cancel_pending_tasks as _cancel_pending_tasks,
)
from cyrene.knowledge.extractors import (
    extract_office_xml_text as _extract_office_xml_text,
)


async def extract_document_text(path: Path, kind: str, *, content_hash: str = "") -> str:
    """Extract full text from a document based on its kind.

    - pdf: Use pypdf to extract full text from all pages
    - image: Use vision analysis to describe image
    - code/map (text-based): Read file with UTF-8 (ignoring errors)
    - other (binary/unknown): Return empty string (archived only)

    Binary/unknown files (kind == "file": .pptx, .docx, .zip, ...) are NOT read
    as text — doing so yields tens of MB of mojibake that explodes into tens of
    thousands of junk chunks (and, with embeddings on, that many API calls).
    """
    if not isinstance(path, Path):
        path = Path(path)

    if not path.exists():
        return ""

    # Cap to avoid pathological chunk/embedding blow-ups on very large files
    _MAX_EXTRACT_BYTES = 10 * 1024 * 1024  # 10 MB

    try:
        if kind == "pdf" or is_pdf_path(path):
            from pypdf import PdfReader
            from cyrene.knowledge import local_models, ocr

            reader = PdfReader(str(path))
            cached = ocr.read_cache(content_hash)
            if cached and isinstance(cached.get("pages"), list):
                return "\n\n".join(str(part).strip() for part in cached["pages"] if str(part).strip())
            pages: list[str] = []
            pdfium = None
            if local_models.is_ready(ocr.MODEL_ID):
                try:
                    import pypdfium2
                    pdfium = pypdfium2.PdfDocument(str(path))
                except Exception:
                    pdfium = None
            used_ocr = False
            for index, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if len(page_text.strip()) < 20 and pdfium is not None:
                    try:
                        image = await asyncio.to_thread(lambda i=index: pdfium[i].render(scale=2.0).to_pil())
                        recognized = await ocr.recognize(image)
                        if recognized.strip():
                            page_text = recognized
                            used_ocr = True
                    except Exception:
                        pass
                pages.append(page_text.strip())
            if used_ocr:
                await asyncio.to_thread(ocr.write_cache, content_hash, pages)
            return "\n\n".join(part.strip() for part in pages if part and part.strip())

        if kind == "image" or is_image_path(path):
            from cyrene.knowledge import local_models, ocr

            recognized = ""
            cached = ocr.read_cache(content_hash)
            if cached and isinstance(cached.get("pages"), list):
                cached_text = "\n".join(str(part) for part in cached["pages"]).strip()
                if cached_text:
                    return cached_text
            if local_models.is_ready(ocr.MODEL_ID):
                try:
                    recognized = await ocr.recognize(str(path))
                    if recognized.strip():
                        await asyncio.to_thread(ocr.write_cache, content_hash, [recognized])
                        if len(recognized.strip()) >= 30:
                            return recognized.strip()
                except Exception:
                    recognized = ""
            try:
                result = await vision_analysis(path, prompt="")
                vision_text = result.get("vision_text", "").strip()
                combined = "\n\n".join(part for part in (recognized.strip(), vision_text) if part)
                if recognized.strip():
                    await asyncio.to_thread(ocr.write_cache, content_hash, [combined])
                return combined
            except Exception:
                return recognized.strip()

        office_text = _extract_office_xml_text(path)
        if office_text:
            return office_text

        # Everything else: read as text, but guard against binaries. Files whose
        # kind is "file" (unknown) may be real text (e.g. .html) or binary
        # (.pptx/.docx/.zip). Sniff for NUL bytes to skip binaries instead of
        # turning them into mojibake that explodes into tens of thousands of chunks.
        try:
            if path.stat().st_size > _MAX_EXTRACT_BYTES:
                return ""
            sample = path.read_bytes()[:8192]
            if b"\x00" in sample:
                return ""  # binary content — archive only
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    except Exception:
        return ""


def chunk_text(
    text: str,
    target_chars: int = 800,
    overlap: int = 120,
) -> list[tuple[str, int, int]]:
    """Chunk text into overlapping segments preferring paragraph/sentence boundaries.

    Returns list of (text, char_start, char_end) tuples. Offsets refer to the
    normalized text. Short text (< target_chars) yields a single chunk.
    """
    if not text or not text.strip():
        return []

    # Normalize spaces/tabs and collapse 3+ blank lines, but PRESERVE paragraph breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    n = len(text)
    if n == 0:
        return []

    from cyrene.knowledge.splitter import split_text

    return split_text(text, target_chars=target_chars, overlap=overlap)


_INDEX_LOCK = asyncio.Lock()


async def cancel_pending_tasks(doc_id: str | None = None) -> None:
    """Cancel active knowledge indexing tasks."""
    await _cancel_pending_tasks(doc_id)


async def index_document(db_path: str, doc_id: str) -> None:
    """Index a document: extract text, chunk, embed, and update database.

    Serialized via a module-level lock so concurrent upload/reindex tasks (each
    fired as its own asyncio task) don't contend on the single SQLite writer and
    raise "database is locked". This also enforces the intended sequential,
    burst-free indexing.
    """
    task = asyncio.current_task()
    already_active = task in _ACTIVE_INDEX_TASKS if task is not None else False
    if task is not None:
        _ACTIVE_INDEX_TASKS.add(task)
        _ACTIVE_INDEX_DOCS[task] = doc_id
    try:
        async with _INDEX_LOCK:
            await _index_document_inner(db_path, doc_id)
    finally:
        if task is not None and not already_active:
            _ACTIVE_INDEX_TASKS.discard(task)
            _ACTIVE_INDEX_DOCS.pop(task, None)


async def _index_document_inner(db_path: str, doc_id: str) -> None:
    """Set status parsing -> indexed/error; degrades gracefully without embeddings."""
    try:
        # Get document
        doc = await store.get_document(db_path, doc_id)
        if not doc:
            return

        # Set status to parsing
        await store.update_document(db_path, doc_id, status="parsing")

        # Extract text
        path = Path(doc["path"])
        if not path.is_file():
            from cyrene.runtime.attachments import resolve_managed_attachment_path

            relocated = resolve_managed_attachment_path(str(doc["path"]))
            if relocated is not None:
                path = relocated
        content_hash = str(doc.get("content_hash") or "") or store.content_hash_file(path)
        text = await extract_document_text(path, doc["kind"], content_hash=content_hash)
        try:
            from cyrene.knowledge import ocr

            if ocr.read_cache(content_hash):
                metadata = dict(doc.get("metadata") or {})
                metadata.update({"ocr": True, "ocr_model": ocr.MODEL_ID})
                await store.update_document(
                    db_path, doc_id, metadata=metadata, content_hash=content_hash
                )
        except Exception:
            pass

        # Chunk text
        chunks_raw = chunk_text(text)

        # Prepare chunks for storage
        chunks_to_store = []
        for ordinal, (chunk_text_str, char_start, char_end) in enumerate(chunks_raw):
            chunk_dict = {
                "id": None,  # Will be generated
                "ordinal": ordinal,
                "content": chunk_text_str,
                "char_start": char_start,
                "char_end": char_end,
                "token_count": approx_token_count(chunk_text_str),
                "embedding": None,
                "embedding_dim": 0,
                "embedding_model": "",
            }
            chunks_to_store.append(chunk_dict)

        # Embed if configured
        if embeddings.is_configured() and chunks_to_store:
            try:
                model = embeddings._model()
                _, configured_dim = embeddings.current_identity()
                cached = await store.reusable_embeddings(db_path, model, configured_dim)
                missing: list[dict] = []
                for chunk_dict in chunks_to_store:
                    fingerprint = hashlib.sha256(chunk_dict["content"].encode("utf-8")).hexdigest()
                    reused = cached.get(fingerprint)
                    if reused:
                        chunk_dict.update({
                            "embedding": reused["embedding"],
                            "embedding_dim": reused["embedding_dim"],
                            "embedding_model": model,
                        })
                    else:
                        missing.append(chunk_dict)
                for start in range(0, len(missing), 32):
                    batch = missing[start:start + 32]
                    vectors = await embeddings.embed_texts([item["content"] for item in batch])
                    for chunk_dict, vector in zip(batch, vectors):
                        chunk_dict["embedding"] = embeddings.pack_vector(vector)
                        chunk_dict["embedding_dim"] = len(vector)
                        chunk_dict["embedding_model"] = model
            except Exception:
                # Gracefully degrade: proceed without embeddings
                pass

        # Replace chunks
        replaced = await store.replace_chunks(db_path, doc_id, chunks_to_store)
        if not replaced:
            return

        # Update document metadata
        summary = text[: min(300, len(text))] if text else ""
        await store.update_document(
            db_path,
            doc_id,
            status="indexed",
            char_count=len(text),
            chunk_count=len(chunks_to_store),
            summary=summary,
            indexed_at=store._now(),
        )

    except Exception as e:
        # Set status to error
        await store.update_document(
            db_path,
            doc_id,
            status="error",
            error=json.dumps({
                "message": str(e),
                "at": store._now(),
                "retries": _error_retries((await store.get_document(db_path, doc_id) or {}).get("error")) + 1,
            }),
        )


async def reindex_document(db_path: str, doc_id: str) -> None:
    """Reindex a document by calling index_document."""
    await index_document(db_path, doc_id)


async def process_pending(db_path: str, *, limit: int | None = None) -> None:
    """Process pending documents sequentially.

    Indexes up to `limit` pending documents. Failures are marked as error without retry.
    Sequential processing avoids overwhelming vision/embedding APIs.
    """
    task = asyncio.current_task()
    already_active = task in _ACTIVE_INDEX_TASKS if task is not None else False
    if task is not None:
        _ACTIVE_INDEX_TASKS.add(task)
    try:
        async with aiosqlite.connect(db_path, timeout=30) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, status, error FROM kb_documents WHERE status IN ('pending', 'error') ORDER BY created_at ASC LIMIT ?",
                (limit or 999999,),
            )
            rows = await cursor.fetchall()

        for row in rows:
            if row["status"] == "error" and not _retry_due(row["error"]):
                continue
            doc_id = row["id"]
            await index_document(db_path, doc_id)
    finally:
        if task is not None and not already_active:
            _ACTIVE_INDEX_TASKS.discard(task)
            _ACTIVE_INDEX_DOCS.pop(task, None)


def _error_payload(raw: object) -> dict:
    try:
        payload = json.loads(str(raw or ""))
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {"message": str(raw or ""), "retries": 0}


def _error_retries(raw: object) -> int:
    return int(_error_payload(raw).get("retries") or 0)


def _retry_due(raw: object) -> bool:
    payload = _error_payload(raw)
    retries = int(payload.get("retries") or 0)
    if retries >= 2:
        return False
    try:
        failed_at = datetime.fromisoformat(str(payload.get("at") or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    delay = timedelta(seconds=60 if retries <= 1 else 300)
    return datetime.now(timezone.utc) >= failed_at + delay


async def reembed_all(db_path: str) -> dict[str, int]:
    """Replace stale/missing vectors without extracting source documents again."""
    if not embeddings.is_configured():
        raise RuntimeError("Embeddings not configured")
    model = embeddings._model()
    _, configured_dim = embeddings.current_identity()
    chunks = await store.iter_all_chunks(db_path)
    pending = [
        chunk for chunk in chunks
        if chunk.get("embedding_model") != model
        or not chunk.get("embedding")
        or (configured_dim and int(chunk.get("embedding_dim") or 0) != configured_dim)
    ]
    updates: list[dict] = []
    async with _INDEX_LOCK:
        for start in range(0, len(pending), 32):
            batch = pending[start:start + 32]
            vectors = await embeddings.embed_texts([chunk["content"] for chunk in batch])
            for chunk, vector in zip(batch, vectors):
                updates.append({
                    "id": chunk["id"],
                    "embedding": embeddings.pack_vector(vector),
                    "embedding_dim": len(vector),
                    "embedding_model": model,
                })
        if updates:
            await store.update_chunk_embeddings(db_path, updates)
    return {"updated": len(updates), "total": len(chunks)}
