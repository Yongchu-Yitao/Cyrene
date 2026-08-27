"""Per-session conversation archiving (Workbench conversations → workspace files)."""

from __future__ import annotations

from pathlib import Path

from agent.plugin.plugin_impl.cyrene_memory.archive import (
    archive_session_exchange,
    session_conversation_file,
    session_conversations_dir,
)


def test_archive_writes_one_file_per_session(tmp_path: Path) -> None:
    ws = str(tmp_path)
    a1 = archive_session_exchange("wbchat_aaa", "hi", "hello", workspace_dir=ws)
    a2 = archive_session_exchange("wbchat_aaa", "more", "sure", workspace_dir=ws)
    b1 = archive_session_exchange("wbchat_bbb", "other", "ok", workspace_dir=ws)

    assert a1 == a2 == session_conversation_file("wbchat_aaa", ws)
    assert b1 != a1
    conv_dir = session_conversations_dir(ws)
    assert conv_dir == tmp_path / ".cyrene" / "conversations"
    assert sorted(p.name for p in conv_dir.iterdir()) == ["wbchat_aaa.md", "wbchat_bbb.md"]


def test_archive_accumulates_and_refreshes_header(tmp_path: Path) -> None:
    ws = str(tmp_path)
    archive_session_exchange("s1", "q1", "r1", workspace_dir=ws)
    path = archive_session_exchange("s1", "q2", "r2", workspace_dir=ws, session_title="标题")
    content = Path(path).read_text(encoding="utf-8")

    # Single header even after a title appears on a later exchange; both turns kept.
    assert content.count("# Conversation s1") == 1
    assert content.count("\n## ") == 2  # two timestamped exchange entries
    assert content.count("**User**:") == 2
    assert "<!-- session_title: 标题 -->" in content
    assert "q1" in content and "q2" in content and "r1" in content and "r2" in content


def test_archive_filename_is_path_safe(tmp_path: Path) -> None:
    ws = str(tmp_path)
    path = archive_session_exchange("../../etc/evil id", "x", "y", workspace_dir=ws)
    assert path is not None
    # Stays inside the conversations dir — separators are stripped, no traversal.
    assert Path(path).parent == session_conversations_dir(ws)
    assert "/" not in Path(path).name


def test_archive_requires_session_id(tmp_path: Path) -> None:
    assert archive_session_exchange("", "x", "y", workspace_dir=str(tmp_path)) is None


def test_archive_is_idempotent_for_one_round(tmp_path: Path) -> None:
    path = archive_session_exchange(
        "chat-round",
        "first",
        "answer",
        workspace_dir=tmp_path,
        round_id="run-1",
    )
    duplicate = archive_session_exchange(
        "chat-round",
        "duplicate",
        "must not append",
        workspace_dir=tmp_path,
        round_id="run-1",
    )

    assert duplicate == path
    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert content.count("<!-- round_id: run-1 -->") == 1
    assert "first" in content
    assert "duplicate" not in content
