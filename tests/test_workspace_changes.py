from __future__ import annotations

import asyncio


def test_workspace_snapshot_records_created_modified_deleted_and_binary(tmp_path):
    from cyrene.workbench.workspace_changes import (
        capture_workspace_snapshot,
        compare_workspace_snapshots,
    )

    modified = tmp_path / "src" / "app.py"
    modified.parent.mkdir()
    modified.write_text("old\nkeep\n", encoding="utf-8")
    deleted = tmp_path / "old.txt"
    deleted.write_text("remove me\n", encoding="utf-8")
    binary = tmp_path / "asset.bin"
    binary.write_bytes(b"\x00\x01")

    before = capture_workspace_snapshot(tmp_path)
    modified.write_text("new\nkeep\n", encoding="utf-8")
    deleted.unlink()
    (tmp_path / "new.md").write_text("# New\n", encoding="utf-8")
    binary.write_bytes(b"\x00\x02")
    after = capture_workspace_snapshot(tmp_path)

    by_path = {
        item["path"]: item
        for item in compare_workspace_snapshots(before, after)
    }
    assert by_path["src/app.py"]["changeType"] == "modified"
    assert "-old" in by_path["src/app.py"]["diff"]
    assert "+new" in by_path["src/app.py"]["diff"]
    assert by_path["new.md"]["changeType"] == "created"
    assert "--- /dev/null" in by_path["new.md"]["diff"]
    assert by_path["old.txt"]["changeType"] == "deleted"
    assert "+++ /dev/null" in by_path["old.txt"]["diff"]
    assert by_path["asset.bin"]["binary"] is True
    assert "diff" not in by_path["asset.bin"]


def test_workspace_snapshot_ignores_git_and_detects_dotfiles(tmp_path):
    from cyrene.workbench.workspace_changes import (
        capture_workspace_snapshot,
        compare_workspace_snapshots,
    )

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "index").write_text("before", encoding="utf-8")
    dotfile = tmp_path / ".editorconfig"
    dotfile.write_text("root=false\n", encoding="utf-8")
    before = capture_workspace_snapshot(tmp_path)
    (tmp_path / ".git" / "index").write_text("after", encoding="utf-8")
    dotfile.write_text("root=true\n", encoding="utf-8")
    after = capture_workspace_snapshot(tmp_path)

    changes = compare_workspace_snapshots(before, after)
    assert [item["path"] for item in changes] == [".editorconfig"]


def test_workspace_snapshot_ignores_cyrene_managed_run_state(tmp_path):
    from cyrene.workbench.workspace_changes import (
        capture_workspace_snapshot,
        compare_workspace_snapshots,
        is_cyrene_managed_workspace_path,
    )

    before = capture_workspace_snapshot(tmp_path)
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / "wbchat_1.md").write_text(
        "# Conversation\n", encoding="utf-8"
    )
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "plan_1.md").write_text("# Plan\n", encoding="utf-8")
    user_file = tmp_path / "docs" / "conversations" / "notes.md"
    user_file.parent.mkdir(parents=True)
    user_file.write_text("keep tracking this\n", encoding="utf-8")
    after = capture_workspace_snapshot(tmp_path)

    assert is_cyrene_managed_workspace_path("conversations/wbchat_1.md") is True
    assert is_cyrene_managed_workspace_path("plan/plan_1.md") is True
    assert is_cyrene_managed_workspace_path("docs/conversations/notes.md") is False
    assert [item["path"] for item in compare_workspace_snapshots(before, after)] == [
        "docs/conversations/notes.md"
    ]


def test_workspace_diff_keeps_rows_separate_without_final_newline(tmp_path):
    from cyrene.workbench.workspace_changes import (
        capture_workspace_snapshot,
        compare_workspace_snapshots,
    )

    target = tmp_path / "plain.txt"
    target.write_text("old", encoding="utf-8")
    before = capture_workspace_snapshot(tmp_path)
    target.write_text("new", encoding="utf-8")
    after = capture_workspace_snapshot(tmp_path)

    diff = compare_workspace_snapshots(before, after)[0]["diff"]
    assert "\n-old\n+new\n" in diff


def test_incremental_workspace_snapshot_reuses_unchanged_file_state(tmp_path):
    from cyrene.workbench.workspace_changes import capture_workspace_snapshot

    unchanged = tmp_path / "unchanged.txt"
    changed = tmp_path / "changed.txt"
    unchanged.write_text("same\n", encoding="utf-8")
    changed.write_text("before\n", encoding="utf-8")
    before = capture_workspace_snapshot(tmp_path)

    changed.write_text("after and a different size\n", encoding="utf-8")
    after = capture_workspace_snapshot(tmp_path, previous=before)

    assert after.files["unchanged.txt"] is before.files["unchanged.txt"]
    assert after.files["changed.txt"] is not before.files["changed.txt"]
    assert after.files["changed.txt"].text == "after and a different size\n"


def test_change_store_keeps_diff_private_until_file_fetch(tmp_path):
    from cyrene.workbench.workspace_changes import (
        get_chat_file_change,
        list_chat_change_sets,
        save_change_set,
    )

    db_path = str(tmp_path / "cyrene.runtime.database")
    change_set = {
        "id": "run_1",
        "chatId": "chat_1",
        "runId": "run_1",
        "completedAt": "2026-01-01T00:00:00+00:00",
        "fileCount": 1,
        "additions": 1,
        "deletions": 1,
        "files": [{
            "id": "file_1",
            "path": "app.py",
            "changeType": "modified",
            "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
        }],
    }
    save_change_set(db_path, change_set)

    listed = list_chat_change_sets(db_path, "chat_1")
    assert listed[0]["files"][0]["path"] == "app.py"
    assert "diff" not in listed[0]["files"][0]
    assert "workspacePath" not in listed[0]
    fetched = get_chat_file_change(db_path, "chat_1", "run_1", "app.py")
    assert fetched is not None
    assert "+new" in fetched["diff"]


def test_change_store_hides_historical_cyrene_managed_records(tmp_path):
    from cyrene.workbench.workspace_changes import (
        _write_store,
        get_chat_file_change,
        list_chat_change_sets,
    )

    db_path = str(tmp_path / "cyrene.runtime.database")
    mixed = {
        "id": "run_mixed",
        "chatId": "chat_1",
        "runId": "run_mixed",
        "completedAt": "2026-01-02T00:00:00+00:00",
        "fileCount": 3,
        "additions": 30,
        "deletions": 0,
        "files": [
            {"path": "conversations/wbchat_1.md", "additions": 13, "deletions": 0, "diff": "+chat\n"},
            {"path": "plan/plan_1.md", "additions": 7, "deletions": 0, "diff": "+plan\n"},
            {"path": "src/app.py", "additions": 10, "deletions": 0, "diff": "+code\n"},
        ],
    }
    internal_only = {
        "id": "run_internal_only",
        "chatId": "chat_2",
        "runId": "run_internal_only",
        "completedAt": "2026-01-02T00:00:00+00:00",
        "fileCount": 1,
        "additions": 13,
        "deletions": 0,
        "files": [{
            "path": "conversations/wbchat_2.md",
            "additions": 13,
            "deletions": 0,
            "diff": "+chat\n",
        }],
    }
    _write_store(db_path, {"changeSets": [mixed, internal_only]})

    listed = list_chat_change_sets(db_path, "chat_1")
    assert len(listed) == 1
    assert listed[0]["fileCount"] == 1
    assert listed[0]["additions"] == 10
    assert [item["path"] for item in listed[0]["files"]] == ["src/app.py"]
    assert list_chat_change_sets(db_path, "chat_2") == []
    assert get_chat_file_change(
        db_path, "chat_1", "run_mixed", "conversations/wbchat_1.md"
    ) is None


def test_change_store_never_persists_new_cyrene_managed_records(tmp_path):
    from cyrene.workbench.workspace_changes import _read_store, save_change_set

    db_path = str(tmp_path / "cyrene.runtime.database")
    ignored = save_change_set(db_path, {
        "id": "run_internal",
        "chatId": "chat_1",
        "files": [{
            "path": "conversations/wbchat_1.md",
            "additions": 13,
            "deletions": 0,
        }],
    })
    assert ignored["fileCount"] == 0
    assert _read_store(db_path)["changeSets"] == []

    save_change_set(db_path, {
        "id": "run_mixed",
        "chatId": "chat_1",
        "files": [
            {"path": "plan/plan_1.md", "additions": 7, "deletions": 0},
            {"path": "src/app.py", "additions": 2, "deletions": 1},
        ],
    })
    stored = _read_store(db_path)["changeSets"][0]
    assert stored["fileCount"] == 1
    assert stored["additions"] == 2
    assert stored["deletions"] == 1
    assert [item["path"] for item in stored["files"]] == ["src/app.py"]


def test_workspace_change_baselines_allow_overlapping_runs_in_same_workspace(tmp_path):
    from cyrene.workbench import chat as chat_routes

    async def exercise_overlap():
        first = await chat_routes._capture_workspace_changes_baseline(
            tmp_path, "run_first"
        )
        second = await asyncio.wait_for(
            chat_routes._capture_workspace_changes_baseline(
                tmp_path, "run_second"
            ),
            timeout=1,
        )
        assert first.overlapping_run_ids == {"run_second"}
        assert second.overlapping_run_ids == {"run_first"}

        (tmp_path / "shared.txt").write_text("changed\n", encoding="utf-8")
        first_after = await chat_routes._complete_workspace_changes_baseline(
            first, tmp_path
        )
        assert first_after is not None
        workspace_key = str(tmp_path.resolve())
        assert set(chat_routes._WORKSPACE_CHANGES_LOCKS[workspace_key].active) == {
            "run_second"
        }
        second_after = await chat_routes._complete_workspace_changes_baseline(
            second, tmp_path
        )
        assert second_after is not None

    asyncio.run(exercise_overlap())
    assert str(tmp_path.resolve()) not in chat_routes._WORKSPACE_CHANGES_LOCKS


def test_overlapping_change_set_reports_nonexclusive_attribution(tmp_path):
    from cyrene.workbench.workspace_changes import build_change_set, capture_workspace_snapshot

    before = capture_workspace_snapshot(tmp_path)
    (tmp_path / "shared.txt").write_text("changed\n", encoding="utf-8")
    after = capture_workspace_snapshot(tmp_path)
    change_set = build_change_set(
        chat_id="chat_first",
        run_id="run_first",
        before=before,
        after=after,
        status="completed",
        attribution="overlapping",
        overlapping_run_ids=["run_second"],
    )

    assert change_set["attribution"] == "overlapping"
    assert change_set["overlappingRunIds"] == ["run_second"]
