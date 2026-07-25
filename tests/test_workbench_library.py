"""Focused coverage for the project-scoped structured literature library."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.runtime.database import init_knowledge_db
from cyrene.knowledge import bibliography, library, store, zotero
from route.workbench import library as library_routes


@pytest.fixture
async def library_db(tmp_path):
    path = str(tmp_path / "kb_project_one.db")
    await init_knowledge_db(path)
    return path


@pytest.mark.asyncio
async def test_library_crud_filters_stats_and_project_isolation(library_db, tmp_path):
    collection = await library.create_collection(library_db, {"name": "Machine Learning"})
    paper = await library.create_item(library_db, {
        "title": "Attention Is All You Need",
        "abstract": "Transformer architecture",
        "year": 2017,
        "venue": "NeurIPS",
        "tags": ["Machine Learning", "Transformer", "Transformer"],
        "starred": True,
        "reading_status": "read",
        "creators": [{"first_name": "Ashish", "last_name": "Vaswani"}],
        "collection_ids": [collection["id"]],
    })
    await library.create_note(library_db, paper["id"], {"content": "Key paper"})

    matches, total = await library.list_items(library_db, q="Vaswani", year=2017)
    assert total == 1
    assert matches[0]["title"] == "Attention Is All You Need"
    assert matches[0]["tags"] == ["Machine Learning", "Transformer"]
    assert (await library.list_items(library_db, status="recent_read"))[1] == 1
    assert (await library.list_items(library_db, collection="__unclassified__"))[1] == 0

    stats = await library.get_stats(library_db)
    assert stats["total"] == 1
    assert stats["starred"] == 1
    assert stats["notes"] == 1

    second_db = str(tmp_path / "kb_project_two.db")
    await init_knowledge_db(second_db)
    assert (await library.list_items(second_db))[1] == 0

    assert await library.delete_item(library_db, paper["id"])
    assert (await library.list_items(library_db))[1] == 0
    assert (await library.list_items(library_db, trash=True))[1] == 1
    assert (await library.restore_item(library_db, paper["id"]))["deleted_at"] is None


@pytest.mark.asyncio
async def test_library_batch_delete_supports_trash_and_permanent_removal(library_db):
    first = await library.create_item(library_db, {"title": "First"})
    second = await library.create_item(library_db, {"title": "Second"})

    assert await library.delete_items(
        library_db, [first["id"], second["id"], first["id"]]
    ) == 2
    assert (await library.list_items(library_db))[1] == 0
    assert (await library.list_items(library_db, trash=True))[1] == 2

    assert await library.delete_items(
        library_db, [first["id"], second["id"]], permanent=True
    ) == 2
    assert (await library.list_items(library_db, trash=True))[1] == 0


@pytest.mark.asyncio
async def test_library_filters_general_knowledge_by_file_type(library_db):
    image = await library.create_item(library_db, {"title": "Product mockup"})
    audio = await library.create_item(library_db, {"title": "Interview recording"})
    video = await library.create_item(library_db, {"title": "Demo video"})
    sheet = await library.create_item(library_db, {"title": "Budget"})
    archive = await library.create_item(library_db, {"title": "Source archive"})
    await library.create_item(library_db, {"title": "Plain knowledge note"})
    await library.create_item(
        library_db, {"title": "Reference website", "item_type": "webpage"}
    )

    await library.add_attachment(
        library_db,
        image["id"],
        {"filename": "mockup.png", "content_type": "image/png"},
    )
    await library.add_attachment(
        library_db,
        audio["id"],
        {"filename": "interview.mp3", "content_type": "audio/mpeg"},
    )
    await library.add_attachment(
        library_db,
        video["id"],
        {"filename": "demo.mp4", "content_type": "video/mp4"},
    )
    await library.add_attachment(
        library_db,
        sheet["id"],
        {
            "filename": "budget.xlsx",
            "content_type": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        },
    )
    await library.add_attachment(
        library_db,
        archive["id"],
        {"filename": "source.zip", "content_type": "application/zip"},
    )

    async def titles(file_type: str) -> set[str]:
        items, _ = await library.list_items(library_db, file_type=file_type)
        return {item["title"] for item in items}

    assert await titles("image") == {"Product mockup"}
    assert await titles("audio") == {"Interview recording"}
    assert await titles("video") == {"Demo video"}
    assert await titles("spreadsheet") == {"Budget"}
    assert await titles("document") == {"Plain knowledge note"}
    assert await titles("link") == {"Reference website"}
    assert await titles("other") == {"Source archive"}


@pytest.mark.asyncio
async def test_existing_knowledge_documents_are_bridged_without_cross_project_copy(
    library_db, tmp_path
):
    async with aiosqlite.connect(library_db) as db:
        await db.execute(
            """INSERT INTO kb_documents (
                id,name,path,content_hash,content_type,kind,size,status,source,title,
                summary,tags,char_count,chunk_count,error,created_at,updated_at,metadata
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "legacy-doc-1", "existing-paper.pdf", "/project-one/existing-paper.pdf",
                "hash-1", "application/pdf", "pdf", 4096, "indexed", "kb_upload",
                "Existing Knowledge Document", "Existing indexed summary",
                '["legacy","project-one"]', 1200, 3, "",
                "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00", "{}",
            ),
        )
        await db.commit()

    items, total = await library.list_items(library_db)
    assert total == 1
    assert items[0]["title"] == "Existing Knowledge Document"
    assert items[0]["provider"] == "knowledge"
    assert items[0]["tags"] == ["legacy", "project-one"]
    assert items[0]["abstract"] == ""
    detail = await library.get_item(library_db, items[0]["id"])
    assert detail["attachments"][0]["kb_document_id"] == "legacy-doc-1"
    assert detail["attachments"][0]["content_type"] == "application/pdf"

    # Repeated loads are idempotent.
    assert await library.sync_knowledge_documents(library_db) == 0
    assert (await library.list_items(library_db))[1] == 1

    second_db = str(tmp_path / "kb_project_without_document.db")
    await init_knowledge_db(second_db)
    assert (await library.list_items(second_db))[1] == 0


@pytest.mark.asyncio
async def test_knowledge_bridge_uses_only_source_abstract_and_repairs_generated_summary(
    library_db,
):
    async with aiosqlite.connect(library_db) as db:
        await db.executemany(
            """INSERT INTO kb_documents (
                id,name,path,content_hash,content_type,kind,size,status,source,title,
                summary,tags,char_count,chunk_count,error,created_at,updated_at,metadata
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "ordinary-image", "diagram.png", "/project/diagram.png", "hash-image",
                    "image/png", "image", 1024, "indexed", "kb_upload", "Diagram",
                    "Generated visual description", "[]", 28, 1, "",
                    "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "{}",
                ),
                (
                    "source-paper", "paper.pdf", "/project/paper.pdf", "hash-paper",
                    "application/pdf", "pdf", 2048, "indexed", "kb_upload", "Paper",
                    "Generated indexing preview", "[]", 120, 1, "",
                    "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                    '{"abstract":"Publisher-provided abstract"}',
                ),
            ],
        )
        await db.commit()

    items, total = await library.list_items(library_db)
    assert total == 2
    by_title = {item["title"]: item for item in items}
    assert by_title["Diagram"]["abstract"] == ""
    assert by_title["Paper"]["abstract"] == "Publisher-provided abstract"

    # Repair rows created by older versions that copied the indexing preview.
    async with aiosqlite.connect(library_db) as db:
        await db.execute(
            "UPDATE library_items SET abstract=? WHERE id=?",
            ("Generated visual description", by_title["Diagram"]["id"]),
        )
        await db.commit()
    assert await library.sync_knowledge_documents(library_db) == 0
    repaired = await library.get_item(library_db, by_title["Diagram"]["id"])
    assert repaired["abstract"] == ""


@pytest.mark.asyncio
async def test_zotero_import_is_idempotent_and_maps_children(library_db):
    collection = {
        "key": "COLL1", "version": 4,
        "data": {"key": "COLL1", "name": "Agents", "parentCollection": False},
    }
    parent = {
        "key": "ITEM1", "version": 8, "library": {"id": 42},
        "data": {
            "key": "ITEM1", "itemType": "journalArticle", "title": "Agent Systems",
            "date": "2025-03", "publicationTitle": "AI Journal", "DOI": "10.1/agent",
            "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
            "collections": ["COLL1"], "tags": [{"tag": "Agents"}],
        },
    }
    attachment = {
        "key": "ATT1", "version": 2,
        "data": {"key": "ATT1", "itemType": "attachment", "parentItem": "ITEM1",
                 "title": "PDF", "filename": "agent.pdf", "contentType": "application/pdf",
                 "path": "attachments:agent.pdf", "linkMode": "imported_file"},
    }
    note = {
        "key": "NOTE1", "version": 3,
        "data": {"key": "NOTE1", "itemType": "note", "parentItem": "ITEM1", "note": "<p>Insight</p>"},
    }
    annotation = {
        "key": "ANN1", "version": 5,
        "data": {"key": "ANN1", "itemType": "annotation", "parentItem": "ATT1",
                 "annotationType": "highlight", "annotationText": "important",
                 "annotationPageLabel": "7", "annotationColor": "#ffd400"},
    }

    first = await zotero.import_records(
        library_db, [parent, attachment, note, annotation],
        collections=[collection], library_id="42", copy_attachments=False,
    )
    assert first["created"] == 1 and first["updated"] == 0 and first["skipped"] == 0
    item = first["items"][0]
    detail = await library.get_item(library_db, item["id"])
    assert detail["year"] == 2025
    assert detail["creators"][0]["last_name"] == "Lovelace"
    assert detail["collections"][0]["name"] == "Agents"
    assert detail["attachments"][0]["provider_library_id"] == "42"
    assert detail["notes"][0]["content"] == "<p>Insight</p>"
    assert detail["annotations"][0]["page_label"] == "7"

    parent["version"] = 9
    parent["data"]["title"] = "Agent Systems, Revised"
    second = await zotero.import_records(
        library_db, [parent, attachment, note, annotation],
        collections=[collection], library_id="42", copy_attachments=False,
    )
    assert second["created"] == 0 and second["updated"] == 1
    assert (await library.list_items(library_db))[1] == 1
    assert (await library.get_item(library_db, item["id"]))["title"] == "Agent Systems, Revised"

    # Zotero keys are only unique inside a user/group library. The same keys
    # from another library must not overwrite the first library's children.
    third = await zotero.import_records(
        library_db, [parent, attachment, note, annotation],
        collections=[collection], library_id="43", copy_attachments=False,
    )
    assert third["created"] == 1
    async with aiosqlite.connect(library_db) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM library_attachments WHERE provider_key='ATT1'"
        )
        assert (await cursor.fetchone())[0] == 2

    # Incremental deletion keys also apply to child objects and remain scoped
    # to the source library.
    assert await zotero._apply_deleted(library_db, "42", {"items": ["NOTE1"]}, 10) == 1
    async with aiosqlite.connect(library_db) as db:
        cursor = await db.execute(
            "SELECT provider_library_id FROM library_notes WHERE provider_key='NOTE1'"
        )
        assert [row[0] for row in await cursor.fetchall()] == ["43"]

    assert await zotero._apply_deleted(
        library_db, "42", {"collections": ["COLL1"]}, 11
    ) == 1
    async with aiosqlite.connect(library_db) as db:
        cursor = await db.execute(
            "SELECT provider_library_id FROM library_collections WHERE provider_key='COLL1'"
        )
        assert [row[0] for row in await cursor.fetchall()] == ["43"]


@pytest.mark.asyncio
async def test_zotero_client_paginates_tracks_version_and_resolves_file(tmp_path):
    attachment_file = tmp_path / "paper.pdf"
    attachment_file.write_bytes(b"%PDF-test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/file/view/url"):
            return httpx.Response(200, text=attachment_file.as_uri())
        start = int(request.url.params.get("start", "0"))
        payload = [{"key": f"K{start + index}", "data": {"itemType": "book"}} for index in range(2)] if start == 0 else []
        return httpx.Response(
            200, json=payload,
            headers={"Total-Results": "2", "Last-Modified-Version": "17"},
        )

    client = zotero.ZoteroLocalClient(transport=httpx.MockTransport(handler))
    records, version = await client.fetch_all("users/0/items", page_size=2)
    assert len(records) == 2 and version == 17
    assert await client.attachment_path("ATT1") == attachment_file


def test_library_http_contract(monkeypatch, library_db):
    async def ensure(_workspace):
        return library_db

    monkeypatch.setattr(library_routes, "_ensure_kb_db", ensure)
    monkeypatch.setattr(library_routes, "_resolve_workspace_id", lambda value: value or "default")
    app = FastAPI()
    router = APIRouter()
    library_routes.register_workbench_library_routes(router)
    app.include_router(router)
    client = TestClient(app)

    created = client.post(
        "/api/workbench/library/items?workspace=p1",
        json={"title": "HTTP Paper", "year": 2024, "tags": ["API"]},
    )
    assert created.status_code == 200
    item_id = created.json()["id"]
    listed = client.get("/api/workbench/library/items", params={"workspace": "p1", "year": 2024})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["workspace"] == "p1"
    updated = client.patch(
        f"/api/workbench/library/items/{item_id}?workspace=p1",
        json={
            "item_type": "journalArticle",
            "title": "HTTP Paper, Revised",
            "venue": "API Journal",
            "volume": "7",
            "issue": "2",
            "pages": "10-18",
            "year": 2025,
            "doi": "10.1/http",
            "isbn": "978-0-00-000000-0",
            "language": "English",
            "creators": [{"name": "Ada Lovelace"}],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["venue"] == "API Journal"
    assert updated.json()["creators"][0]["name"] == "Ada Lovelace"
    note = client.post(
        f"/api/workbench/library/items/{item_id}/notes?workspace=p1",
        json={"content": "HTTP note"},
    )
    assert note.status_code == 200
    detail = client.get(f"/api/workbench/library/items/{item_id}?workspace=p1").json()
    assert detail["note_count"] == 1
    citation = client.get(
        f"/api/workbench/library/items/{item_id}/citation?workspace=p1&style=apa"
    )
    assert citation.status_code == 200
    assert citation.json()["style"] == "apa"
    assert "HTTP Paper, Revised" in citation.json()["citation"]
    assert citation.json()["bibtex"].startswith("@article{")
    assert "title = {HTTP Paper, Revised}" in citation.json()["bibtex"]
    assert "author = {Ada Lovelace}" in citation.json()["bibtex"]
    second = client.post(
        "/api/workbench/library/items?workspace=p1",
        json={"title": "Second HTTP Paper"},
    )
    batch_deleted = client.post(
        "/api/workbench/library/items/batch-delete?workspace=p1",
        json={"item_ids": [item_id, second.json()["id"]]},
    )
    assert batch_deleted.status_code == 200
    assert batch_deleted.json() == {"ok": True, "deleted": 2}
    assert client.get(
        "/api/workbench/library/items",
        params={"workspace": "p1", "trash": "true"},
    ).json()["total"] == 2


@pytest.mark.asyncio
async def test_library_raw_media_is_served_inline(
    monkeypatch, library_db, tmp_path
):
    media_path = tmp_path / "sample.mp4"
    media_path.write_bytes(b"video-bytes")
    document = await store.upsert_document_by_path(
        library_db,
        path=str(media_path),
        name=media_path.name,
        content_type="video/mp4",
        kind="file",
        size=media_path.stat().st_size,
        source="test",
    )
    item = await library.create_item(library_db, {"title": "Sample video"})
    await library.add_attachment(
        library_db,
        item["id"],
        {
            "kb_document_id": document["id"],
            "filename": media_path.name,
            "content_type": "video/mp4",
        },
    )

    async def ensure(_workspace):
        return library_db

    monkeypatch.setattr(library_routes, "_ensure_kb_db", ensure)
    app = FastAPI()
    router = APIRouter()
    library_routes.register_workbench_library_routes(router)
    app.include_router(router)
    response = TestClient(app).get(
        f"/api/workbench/library/items/{item['id']}/raw?workspace=p1"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.content == b"video-bytes"


@pytest.mark.asyncio
async def test_library_read_event_marks_unique_viewed_attachment(
    monkeypatch, library_db, tmp_path
):
    media_path = tmp_path / "viewed.md"
    media_path.write_text("# Viewed", encoding="utf-8")
    document = await store.upsert_document_by_path(
        library_db,
        path=str(media_path),
        name=media_path.name,
        content_type="text/markdown",
        kind="file",
        size=media_path.stat().st_size,
        source="test",
    )
    item = await library.create_item(library_db, {"title": "Viewed document"})
    await library.add_attachment(
        library_db,
        item["id"],
        {
            "kb_document_id": document["id"],
            "filename": media_path.name,
            "content_type": "text/markdown",
        },
    )

    async def ensure(_workspace):
        return library_db

    monkeypatch.setattr(library_routes, "_ensure_kb_db", ensure)
    app = FastAPI()
    router = APIRouter()
    library_routes.register_workbench_library_routes(router)
    app.include_router(router)
    response = TestClient(app).post(
        "/api/workbench/library/read?workspace=p1",
        json={
            "attachment_url": "/api/chat/export/not-the-same.md",
            "file_name": media_path.name,
        },
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    viewed = await library.get_item(library_db, item["id"])
    assert viewed["reading_status"] == "read"
    assert viewed["last_read_at"]


def test_bibliography_parsers_and_upload_contract(monkeypatch, library_db):
    ris = b"""TY  - JOUR\nTI  - Imported RIS Paper\nAU  - Lovelace, Ada\nPY  - 2024\nJO  - Computing\nDO  - 10.1/ris\nER  -\n"""
    bib = b"""@article{turing1936, title={On Computable Numbers}, author={Turing, Alan}, year={1936}, journal={Proceedings}}"""
    assert bibliography.parse_ris(ris)[0]["creators"][0]["last_name"] == "Lovelace"
    assert bibliography.parse_bibtex(bib)[0]["citekey"] == "turing1936"

    async def ensure(_workspace):
        return library_db

    monkeypatch.setattr(library_routes, "_ensure_kb_db", ensure)
    app = FastAPI()
    router = APIRouter()
    library_routes.register_workbench_library_routes(router)
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        "/api/workbench/library/upload?workspace=p1",
        files={"files": ("papers.ris", ris, "application/x-research-info-systems")},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == "Imported RIS Paper"
    assert response.json()["items"][0]["doi"] == "10.1/ris"


def test_library_frontend_uses_project_api_and_clears_filtered_selection():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "workbench-webui" / "workbench-library.jsx").read_text(
        encoding="utf-8"
    )
    shell = (root / "src" / "workbench-webui" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    shell_styles = (root / "src" / "workbench-webui" / "workbench.css").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "workbench-webui" / "workbench-library.css").read_text(
        encoding="utf-8"
    )

    assert 'var workspace = props.project && props.project.id' in source
    assert 'query.set("workspace", workspace)' in source
    assert "intentionally owns no sample data" in source
    assert "!nextItems.some" in source
    assert 'setSelectedId("")' in source
    assert 'scope.type === "all" ? "知识库"' in source
    assert 'scope.type !== "all" && h("h2"' in source
    assert 'className: "wb-lib-head-button zotero"' not in source
    assert "wb-lib-side-section-toggle" in source
    assert "复制纯文本" in source and "复制 BibTeX" in source
    assert ".wb-lib-results {" in styles
    assert "overflow-x: hidden" in styles
    assert "overflow-y: hidden" in styles
    assert "max-height: 50%" in styles
    assert ".wb-lib-work-tabs" in styles and "overflow: hidden" in styles
    assert "wb-lib-work-tabs-more" in source
    assert "flex: 0 0 auto" in styles
    assert ".wb-lib-right-section-head" in styles and "margin-bottom: 8px" in styles
    assert "wb-lib-work-resizer" in source
    assert "removeMany" in source
    assert "function removeChecked()" in source
    assert "永久删除所选知识" in source
    assert ".wb-lib-batch-actions" in styles
    assert "filters.file_type" in source
    assert "params.file_type = filters.file_type" in source
    assert '"文件类型"' in source
    assert '"图片"' in source and '"音频"' in source and '"视频"' in source
    assert "wb-lib-card-description" in source
    assert "wb-lib-card-foot" in source
    assert 'className: "wb-lib-check wb-lib-card-check"' in source
    assert "onToggle: toggleChecked" in source
    assert "grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))" in styles
    assert "overflow-wrap: anywhere" in styles
    assert ".wb-lib-view-toggle button { width: 40px; height: 100%; display: grid; place-items: center;" in styles
    assert ".wb-lib-view-toggle svg { display: block; }" in styles
    assert "inset: 1px 6px" in styles
    assert ".wb-lib-row > * { position: relative; z-index: 1; }" in styles
    assert "background: color-mix(in srgb, var(--wb-accent) 7%, var(--wb-card-bg));" in styles
    assert "is-library-mode" not in shell
    assert "is-library-mode" not in shell_styles
    assert "is-library-mode" not in styles
    assert "--wb-accent: #f34fae" not in shell_styles
    assert "background: var(--wb-shell-bg)" in styles
    assert "background: var(--wb-task-rail-bg)" in styles
    assert "background: var(--wb-main-bg, var(--wb-surface))" in styles
    assert "background: var(--wb-right-bg)" in styles
    assert "#ee4caa" not in styles
    assert "cyrene.library.workspaceHeight" in source
    assert 'role: "separator"' in source
    assert "cursor: ns-resize" in styles
    assert "RightMetadataEditor" in source
    assert "保存全部信息" in source
    assert "wb-lib-right-edit-button" in source
    assert "wb-lib-right-delete" in source
    assert "wb-lib-trash-button" not in source
    assert "border-left: 3px solid var(--wb-accent)" not in styles
    assert "border: 1px solid var(--wb-accent)" in styles
    assert '"aria-label": "文献具体信息"' in source
    assert '"aria-label": "摘要、笔记和标签"' in source
    assert 'props.tab === "info" ? " info" : ""' in source
    assert ".wb-lib-work-body.info { overflow: hidden; padding: 0; }" in styles
    assert "border-right: 1px solid var(--wb-line)" in styles
    assert "overscroll-behavior: contain" in styles
    assert ".wb-lib-tag-cloud h2" in styles
    assert ".wb-lib-side-count" in styles
    assert "background: transparent" in styles
    assert "--lib-sidebar: 236px" in styles
    assert "--lib-sidebar: 196px" in styles
    assert ".wb-lib-side-row:focus, .wb-lib-side-row:focus-visible" in styles
    assert "color-mix(in srgb, var(--wb-accent) 12%, var(--wb-surface))" in styles
    assert ".wb-lib-row.active" in styles
    assert ".wb-lib-card.active" in styles
    assert (
        "border-color: color-mix(in srgb, var(--wb-accent) 36%, var(--wb-line));"
    ) in styles
    assert (
        ".wb-lib-table-body { min-height: 0; flex: 1 1 auto; "
        "overflow-y: auto; overscroll-behavior: contain;"
    ) in styles
    assert (
        ".wb-lib-row { min-height: 41px; padding: 0 14px; "
        "border: 0; border-radius: 0;"
    ) in styles
    assert ".wb-lib-row.active::after {" in styles and "border-radius: 7px;" in styles
    assert ".wb-lib-side-row.active {" in styles and "font-weight: 520;" in styles
    assert "font-weight: 520 !important;" in styles
    assert ".wb-lib-cloud button span" in styles and "font-weight: 400;" in styles
    assert 'h("img", { src: props.rawUrl' in source
    assert 'h("video", { src: props.rawUrl, controls: true' in source
    assert 'h("audio", { src: props.rawUrl, controls: true' in source
    assert "function LibraryPdfPreview" in source
    assert "window.pdfjsSetupViewer(container)" in source
    assert "window.pdfjsLoadPdf(props.url, viewer, abortLoader.signal)" in source
    assert ".wb-lib-pdf-preview" in styles
    assert 'rightTab === "content" && selectedId' in source
    assert "markSelectedRead(selectedId)" in source
    assert "onWheelCapture" in source
    assert 'loading: "lazy", onLoad: props.onViewed' in source
    assert "暂无可显示内容。" in source
    assert ".wb-lib-media-preview" in styles
    assert "max-height: calc(100vh - 210px)" in styles
    assert "window.DOMPurify.sanitize(window.marked.parse" in source
    assert 'className: "wb-lib-markdown"' in source
    assert ".wb-lib-markdown h1" in styles
    assert "renderSafeHtmlDocument" in source
    assert 'srcDoc: safeHtml' in source
    assert 'sandbox: ""' in source
    assert ".wb-lib-html-preview iframe" in styles
    assert "createCollection" in source
    assert '"新建收藏夹"' in source
    assert '"将文献加入收藏夹"' in source
    assert "collection_ids: collectionIds" in source
    assert ".wb-lib-collection-chips" in styles
    assert "scrollRef.current.scrollTop = 0" in source
    assert 'className: "wb-lib-state-action"' in source
    assert 'actionIcon: query || activeFilters || scope.type !== "all" ? "restore" : "upload"' in source
    assert ".wb-lib-state-action {" in styles
    assert "props.onContentViewed" in source
    assert 'client.update(viewedId, { reading_status: "read" })' in source
