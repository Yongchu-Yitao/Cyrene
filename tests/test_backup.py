import asyncio
import json
import sqlite3
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest


def _create_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT NOT NULL)")
        db.execute("DELETE FROM marker")
        db.execute("INSERT INTO marker(value) VALUES (?)", (value,))
        db.commit()


def _read_db(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return str(db.execute("SELECT value FROM marker").fetchone()[0])


@pytest.fixture
def backup_sandbox(monkeypatch, tmp_path):
    from cyrene.runtime import backup

    base = tmp_path / "runtime"
    data = base / "data"
    store = base / "store"
    workspace = base / "workspace"
    temp = base / "temp"
    backups = base / "backups"
    for directory in (base, data, store, workspace, temp, backups):
        directory.mkdir(exist_ok=True)

    managed = [
        (workspace / "conversations", "workspace/conversations"),
        (workspace / "patterns", "workspace/patterns"),
        (workspace / "plan", "workspace/plan"),
        (workspace / "deliverables", "workspace/deliverables"),
        (workspace / "projects", "workspace/projects"),
        (data / "sessions", "data/sessions"),
        (data / "inbox", "data/inbox"),
        (data / "installed_skills", "data/installed_skills"),
        (data / "learned_skill_scripts", "data/learned_skill_scripts"),
        (data / "behavior-media", "data/behavior-media"),
        (data / "webui_uploads", "data/webui_uploads"),
        (data / "webui_exports", "data/webui_exports"),
    ]

    monkeypatch.setattr(backup, "BASE_DIR", base)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "STORE_DIR", store)
    monkeypatch.setattr(backup, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(backup, "TEMP_DIR", temp)
    monkeypatch.setattr(backup, "_BACKUP_DIR", backups)
    monkeypatch.setattr(backup, "_ALLOWED_ROOTS", [store.resolve(), data.resolve(), workspace.resolve()])
    monkeypatch.setattr(backup, "_MANAGED_DIRECTORIES", managed)
    monkeypatch.setattr(backup, "_RESTORABLE_REPLACE_ROOTS", {arc for _, arc in managed})
    monkeypatch.setattr(backup, "_backup_operation_lock", asyncio.Lock())

    snapshot = {"env": {"OPENAI_API_KEY": "secret"}, "settings": {"app_language": "zh"}}
    activated: list[dict] = []
    monkeypatch.setattr(
        backup,
        "_config_snapshot_bytes",
        lambda: json.dumps(snapshot).encode("utf-8"),
    )

    def prepare(raw: bytes):
        restored = json.loads(raw.decode("utf-8"))
        return restored, b"encrypted-on-destination"

    monkeypatch.setattr(backup, "_prepare_config_restore", prepare)
    monkeypatch.setattr(backup, "_activate_config_snapshot", lambda restored: activated.append(restored))
    return {
        "backup": backup,
        "base": base,
        "data": data,
        "store": store,
        "workspace": workspace,
        "backups": backups,
        "snapshot": snapshot,
        "activated": activated,
    }


async def test_backup_round_trip_is_exact_and_restores_sqlite_and_config(backup_sandbox):
    env = backup_sandbox
    backup = env["backup"]
    data = env["data"]
    store = env["store"]
    conversations = env["workspace"] / "conversations"
    conversations.mkdir()
    (conversations / "old.md").write_text("archived", encoding="utf-8")
    (data / "state.json").write_text('{"state":"old"}', encoding="utf-8")
    _create_db(store / "cyrene.runtime.database", "old-db")

    exported = await backup.export_backup()
    assert exported["ok"] is True
    archive = Path(exported["path"])
    with ZipFile(archive) as zf:
        assert zf.testzip() is None
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["version"] == "0.5"
    assert all(item.get("sha256") for item in manifest["entries"])

    (data / "state.json").write_text('{"state":"new"}', encoding="utf-8")
    (conversations / "newer.md").write_text("must disappear", encoding="utf-8")
    _create_db(store / "cyrene.runtime.database", "new-db")

    restored = await backup.restore_backup(str(archive))
    assert restored["ok"] is True
    assert restored["restart_required"] is True
    assert (data / "state.json").read_text(encoding="utf-8") == '{"state":"old"}'
    assert _read_db(store / "cyrene.runtime.database") == "old-db"
    assert sorted(path.name for path in conversations.iterdir()) == ["old.md"]
    assert (data / "config.enc").read_bytes() == b"encrypted-on-destination"
    assert env["activated"] == [env["snapshot"]]


async def test_backup_restores_chat_exports_and_deliverables_referenced_by_ui(
    backup_sandbox,
):
    env = backup_sandbox
    backup = env["backup"]
    exports = env["data"] / "webui_exports"
    deliverables = env["workspace"] / "deliverables"
    plans = env["workspace"] / "plan"
    project_deliverables = (
        env["workspace"] / "projects" / "project_deadbeef" / "deliverables"
    )
    exports.mkdir()
    deliverables.mkdir()
    plans.mkdir()
    project_deliverables.mkdir(parents=True)
    exported_pdf = exports / "paper_deadbeef.pdf"
    exported_html = exports / "report_deadbeef.html"
    deliverable_html = deliverables / "report.html"
    plan_markdown = plans / "plan_deadbeef.md"
    project_artifact = project_deliverables / "analysis.html"
    exported_pdf.write_bytes(b"%PDF-1.4\nbackup fixture\n")
    exported_html.write_text("<h1>render me</h1>", encoding="utf-8")
    deliverable_html.write_text("<h1>source</h1>", encoding="utf-8")
    plan_markdown.write_text("# Persisted plan", encoding="utf-8")
    project_artifact.write_text("<h1>project artifact</h1>", encoding="utf-8")

    result = await backup.export_backup(include_db=False)
    assert result["ok"] is True
    with ZipFile(result["path"]) as archive:
        names = set(archive.namelist())
    assert "data/webui_exports/paper_deadbeef.pdf" in names
    assert "data/webui_exports/report_deadbeef.html" in names
    assert "workspace/deliverables/report.html" in names
    assert "workspace/plan/plan_deadbeef.md" in names
    assert (
        "workspace/projects/project_deadbeef/deliverables/analysis.html" in names
    )

    exported_pdf.unlink()
    exported_html.write_text("<h1>newer, must be rolled back</h1>", encoding="utf-8")
    deliverable_html.unlink()
    plan_markdown.unlink()
    project_artifact.unlink()

    restored = await backup.restore_backup(result["path"])
    assert restored["ok"] is True
    assert exported_pdf.read_bytes() == b"%PDF-1.4\nbackup fixture\n"
    assert exported_html.read_text(encoding="utf-8") == "<h1>render me</h1>"
    assert deliverable_html.read_text(encoding="utf-8") == "<h1>source</h1>"
    assert plan_markdown.read_text(encoding="utf-8") == "# Persisted plan"
    assert (
        project_artifact.read_text(encoding="utf-8")
        == "<h1>project artifact</h1>"
    )


async def test_restore_rolls_back_all_path_changes_on_commit_failure(backup_sandbox, monkeypatch):
    env = backup_sandbox
    backup = env["backup"]
    data = env["data"]
    (data / "a.json").write_text("backup-a", encoding="utf-8")
    (data / "b.json").write_text("backup-b", encoding="utf-8")
    exported = await backup.export_backup(include_db=False)
    assert exported["ok"] is True

    (data / "a.json").write_text("current-a", encoding="utf-8")
    (data / "b.json").write_text("current-b", encoding="utf-8")
    real_replace = backup._replace_from_stage

    def fail_b(source: Path, target: Path) -> None:
        if target.name == "b.json":
            raise OSError("injected commit failure")
        real_replace(source, target)

    monkeypatch.setattr(backup, "_replace_from_stage", fail_b)
    result = await backup.restore_backup(exported["path"])
    assert result["ok"] is False
    assert "injected commit failure" in result["error"]
    assert (data / "a.json").read_text(encoding="utf-8") == "current-a"
    assert (data / "b.json").read_text(encoding="utf-8") == "current-b"
    assert env["activated"] == []


async def test_manifest_mismatch_is_rejected_before_restore(backup_sandbox):
    env = backup_sandbox
    backup = env["backup"]
    (env["data"] / "state.json").write_text("old", encoding="utf-8")
    exported = await backup.export_backup(include_db=False)
    assert exported["ok"] is True

    with ZipFile(exported["path"], "a", ZIP_DEFLATED) as zf:
        zf.writestr("data/unlisted.json", "not declared")
    result = await backup.restore_backup(exported["path"], dry_run=True)
    assert result["ok"] is False
    assert "does not match manifest" in result["error"]


async def test_manifest_size_is_checked_before_it_is_read(backup_sandbox):
    env = backup_sandbox
    backup = env["backup"]
    archive = env["base"] / "oversized-manifest.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", b"x" * (backup._MAX_MANIFEST_BYTES + 1))
    result = await backup.restore_backup(str(archive), dry_run=True)
    assert result["ok"] is False
    assert "manifest exceeds size limit" in result["error"]


async def test_default_backup_names_do_not_collide(backup_sandbox):
    backup = backup_sandbox["backup"]
    first = await backup.export_backup(include_db=False)
    second = await backup.export_backup(include_db=False)
    assert first["ok"] is True and second["ok"] is True
    assert first["path"] != second["path"]
    assert len(list(backup_sandbox["backups"].glob("*.zip"))) == 2


async def test_legacy_v04_archive_remains_restoreable(backup_sandbox):
    env = backup_sandbox
    backup = env["backup"]
    archive = env["base"] / "legacy.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
        zf.writestr("conversations/legacy.md", "legacy")
        zf.writestr(
            "manifest.json",
            json.dumps({"version": "0.4", "entries": ["conversations/legacy.md"]}),
        )
    result = await backup.restore_backup(str(archive))
    assert result["ok"] is True
    assert (env["workspace"] / "conversations" / "legacy.md").read_text() == "legacy"
