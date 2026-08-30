from __future__ import annotations

import asyncio
import subprocess
import zipfile
from pathlib import Path

import pytest

from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.platform.log_repository import LogRepository, LogRepositoryError
from cyrene.workbench.artifacts import code_format_service as format_module
from cyrene.plugins.builtin.cyrene_code.code_format_service import CodeFormatService
from cyrene.plugins.builtin.cyrene_code.project_files import ProjectFileService
from cyrene.plugins.builtin.cyrene_code.workspace_diff_service import WorkspaceDiffService


def _file_service(root: Path) -> ProjectFileService:
    async def resolve_project_workspace(_project):
        return str(root)

    def resolve_active(path: str) -> Path:
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            raise ValueError("outside workspace")
        return target

    return ProjectFileService(
        find_project=lambda _project_id: {"id": "project_1"},
        resolve_workspace=lambda _project: str(root),
        resolve_workspace_async=resolve_project_workspace,
        resolve_active_path=resolve_active,
        resolve_active_write_target=resolve_active,
    )


@pytest.mark.asyncio
async def test_project_file_service_owns_utf8_read_and_write(tmp_path):
    service = _file_service(tmp_path)

    written = await service.write_code_file("src/example.py", "print('ok')\n")
    loaded = await service.read_code_file("src/example.py")

    assert written["status"] == "ok"
    assert loaded == {
        "content": "print('ok')\n",
        "language": "python",
        "size": 12,
        "path": str((tmp_path / "src/example.py").resolve()),
    }


@pytest.mark.asyncio
async def test_project_file_write_preserves_existing_final_newline_and_bom(tmp_path):
    service = _file_service(tmp_path)
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    target.write_bytes(b"\xef\xbb\xbfprint('before')\r\n")

    await service.write_code_file("src/example.py", "print('after')")

    assert target.read_bytes() == b"\xef\xbb\xbfprint('after')\r\n"


@pytest.mark.asyncio
async def test_project_file_write_adds_final_newline_to_new_text_file(tmp_path):
    service = _file_service(tmp_path)

    await service.write_code_file("src/example.py", "print('ok')")

    assert (tmp_path / "src" / "example.py").read_bytes() == b"print('ok')\n"


@pytest.mark.asyncio
async def test_editable_save_returns_the_normalized_persisted_content(tmp_path):
    service = _file_service(tmp_path)
    target = tmp_path / "example.py"
    target.write_bytes(b"print('before')\n")
    opened = service.read_editable("project_1", "example.py")

    saved = await service.save_editable(
        "project_1",
        "example.py",
        "print('after')",
        expected_version=opened["version"],
    )

    assert saved["content"] == "print('after')\n"
    assert target.read_bytes() == b"print('after')\n"


@pytest.mark.asyncio
async def test_workspace_diff_synthesizes_untracked_text_file(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "notes.txt"
    target.write_text("first\nsecond\n", encoding="utf-8")
    service = WorkspaceDiffService(_file_service(tmp_path), tmp_path)

    result = await service.git_diff("notes.txt")

    assert result["has_changes"] is True
    assert "--- /dev/null" in result["diff"]
    assert "+++ b/notes.txt" in result["diff"]
    assert "+second" in result["diff"]


def test_debug_event_repository_skips_bad_jsonl_and_preserves_order(tmp_path):
    log_file = tmp_path / "debug_20260101.jsonl"
    log_file.write_text(
        "not-json\n"
        '{"type":"llm_call","event_id":"disk","timestamp":"2026-01-02",'
        '"messages":[{}],"context_trace":{"included":[{}],"total_tokens_est":7}}\n',
        encoding="utf-8",
    )
    recent = [{
        "type": "llm_call",
        "event_id": "recent",
        "timestamp": "2026-01-03",
        "messages": [],
        "context_trace": {"included": [], "total_tokens_est": 3},
    }]

    async def subscribe_events(session_id: str = ""):
        if False:
            yield {"session_id": session_id}

    repository = DebugEventRepository(
        tmp_path,
        recent_events=lambda _limit: recent,
        full_event=lambda _event_id: None,
        subscribe_events=subscribe_events,
    )

    result = repository.context_events(10)

    assert [event["id"] for event in result["events"]] == ["recent", "disk"]
    assert result["events"][1]["source_log"] == log_file.name
    assert repository.malformed_line_policy == "skip"


def test_log_repository_packages_only_rolling_logs(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "cyrene.log").write_text("current", encoding="utf-8")
    (log_dir / "cyrene.log.1").write_text("older", encoding="utf-8")
    (tmp_path / "debug_secret.jsonl").write_text("secret", encoding="utf-8")
    repository = LogRepository(tmp_path)

    archive = repository.create_export()
    try:
        with zipfile.ZipFile(archive.path) as payload:
            assert set(payload.namelist()) == {"cyrene.log", "cyrene.log.1"}
    finally:
        repository.remove_export(archive.path)


def test_log_repository_reports_empty_store(tmp_path):
    with pytest.raises(LogRepositoryError) as caught:
        LogRepository(tmp_path).create_export()

    assert caught.value.status_code == 404
    assert caught.value.code == "no_logs"


@pytest.mark.asyncio
async def test_code_format_service_owns_formatter_process_and_temp_cleanup(
    monkeypatch,
    tmp_path,
):
    calls = []

    class FakeProcess:
        async def communicate(self):
            target = Path(calls[0][-1])
            target.write_text('print("ok")\n', encoding="utf-8")
            return b"", b""

    async def create_process(*args, **kwargs):
        calls.append(args)
        assert kwargs == {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        return FakeProcess()

    monkeypatch.setattr(format_module.shutil, "which", lambda _name: "/bin/ruff")
    monkeypatch.setattr(format_module.asyncio, "create_subprocess_exec", create_process)
    service = CodeFormatService(tmp_path)

    result = await service.format("print( 'ok' )", "python")

    assert result == {"formatted": 'print("ok")\n', "changed": True}
    assert calls[0][:-1] == ("ruff", "format", "--quiet")
    assert list(tmp_path.iterdir()) == []
