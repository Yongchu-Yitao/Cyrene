"""Tests for the profile panel's backend stats: per-tool counts, task-time
aggregation, and the persisted user identity in _build_user()."""

import asyncio
from datetime import datetime, timezone

import pytest

import aiosqlite

from cyrene.runtime import database as cy_db
from cyrene.workbench import runtime as routes


@pytest.mark.asyncio
async def test_tool_stats_counts_and_ordering(tmp_path):
    db_path = str(tmp_path / "stats.db")
    await cy_db.init_db(db_path)

    ts = "2026-06-21T12:00:00+00:00"
    for tool in ["WebSearch", "web_search", "web_search", "run_shell", "read_file", "read_file"]:
        await cy_db.record_tool_call(db_path, ts, tool)
    # A tool_call with no name still bumps the total but must not create a tool row.
    await cy_db.record_tool_call(db_path, ts, "")

    rows = await cy_db.get_tool_counts_range(db_path, "2000-01-01", "2100-01-01", limit=5)
    assert [(r["tool"], r["count"]) for r in rows] == [
        ("web_search", 3),
        ("read_file", 2),
        ("bash", 1),
    ]
    assert all(r["tool"] for r in rows)  # the blank-name call did not leak in


@pytest.mark.asyncio
async def test_token_usage_stats_respects_exact_since_boundary(tmp_path):
    db_path = str(tmp_path / "usage.db")
    await cy_db.init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        rows = [
            ("2026-08-09T23:59:59+00:00", "deepseek-v4-flash", 10, 1, 11, 0.5),
            ("2026-08-10T00:00:00+00:00", "deepseek-v4-flash", 20, 2, 22, 1.0),
            ("2026-08-10T12:00:00+00:00", "deepseek-v4-flash", 30, 3, 33, 1.5),
        ]
        await db.executemany(
            """INSERT INTO token_usage
               (created_at, model, prompt_tokens, completion_tokens, total_tokens, estimated_cost)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await db.commit()

    stats = await cy_db.get_token_usage_stats(
        db_path,
        since=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    assert stats["total"]["requests"] == 2
    assert stats["total"]["prompt_tokens"] == 50
    assert stats["total"]["completion_tokens"] == 5
    assert stats["total"]["total_cost"] == pytest.approx(2.5)
    assert stats["total"]["max_total_tokens"] == 33
    assert stats["total"]["max_cost"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_tool_stats_merges_profile_display_aliases_before_limit(tmp_path):
    db_path = str(tmp_path / "stats.db")
    await cy_db.init_db(db_path)

    ts = "2026-06-21T12:00:00+00:00"
    tool_counts = {
        "browser_navigate": 4,
        "browser_snapshot": 3,
        "浏览器": 2,
        "WebFetch": 5,
        "fetch_url": 2,
        "Bash": 3,
        "StartShell": 2,
        "WebSearch": 4,
        "web_search": 1,
        "Read": 4,
        "read_file": 1,
        "Edit": 4,
    }
    for tool, count in tool_counts.items():
        for _ in range(count):
            await cy_db.record_tool_call(db_path, ts, tool)

    rows = await cy_db.get_tool_counts_range(db_path, "2000-01-01", "2100-01-01", limit=5)

    assert [(r["tool"], r["count"]) for r in rows] == [
        ("browser", 9),
        ("web_fetch", 7),
        ("bash", 5),
        ("read_file", 5),
        ("web_search", 5),
    ]


@pytest.mark.asyncio
async def test_task_time_totals_merges_task_logs_and_goal_runs(tmp_path):
    db_path = str(tmp_path / "tasks.db")
    await cy_db.init_db(db_path)

    # Empty DB → zeroed totals.
    empty = await cy_db.get_task_time_totals(db_path)
    assert empty == {"total_ms": 0, "longest_ms": 0, "runs": 0}

    await cy_db.log_task_run(db_path, "t1", 1000, "ok")
    await cy_db.log_task_run(db_path, "t2", 3000, "ok")

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        for i, secs in enumerate([2.0, 5.0]):
            await db.execute(
                """
                INSERT INTO goal_runs
                  (id, session_id, project_id, objective, plan_definition_revision,
                   max_active_seconds, max_repair_rounds, active_seconds, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"g{i}", f"s{i}", "p", "obj", 1, 600, 3, secs, now, now),
            )
        await db.commit()

    totals = await cy_db.get_task_time_totals(db_path)
    # tasks: 1000 + 3000 = 4000ms; goals: (2 + 5)s = 7000ms
    assert totals["total_ms"] == 11000
    assert totals["longest_ms"] == 5000  # max(3000ms task, 5000ms goal)
    assert totals["runs"] == 4


def test_build_user_prefers_stored_profile(monkeypatch):
    stored = {
        "profile_name": "Ada Lovelace",
        "profile_avatar": "data:image/png;base64,AAAA",
        "profile_avatar_emoji": "",
        "profile_avatar_color": "#1D9E75",
        "profile_bio": "first programmer",
    }
    monkeypatch.setattr("cyrene.runtime.settings_store.get", lambda key, default="": stored.get(key, default))

    user = routes._build_user()
    assert user["name"] == "Ada Lovelace"
    assert user["handle"] == "adalovelace"
    assert user["initials"] == "AL"
    assert user["avatar"].startswith("data:image/")
    assert user["avatar_color"] == "#1D9E75"
    assert user["bio"] == "first programmer"


def test_build_user_falls_back_to_local_name(monkeypatch):
    monkeypatch.setattr("cyrene.runtime.settings_store.get", lambda key, default="": default)
    monkeypatch.setattr(routes, "_resolve_local_username", lambda: "Sam")

    user = routes._build_user()
    assert user["name"] == "Sam"
    assert user["initials"] == "S"
    assert user["avatar"] == ""
    assert user["avatar_emoji"] == ""


def test_bump_activity_sync_increments_correct_bucket(tmp_path):
    db_path = str(tmp_path / "activity.db")
    asyncio.run(cy_db.init_db(db_path))

    # Compute expected day/bucket in local time so the test is tz-agnostic.
    ts = "2026-06-22T16:30:00+00:00"
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone()
    expected_day = local_dt.strftime("%Y-%m-%d")
    expected_col = cy_db._activity_column(int(local_dt.strftime("%H")))

    cy_db.bump_activity_sync(db_path, timestamp=ts)

    async def _query():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            return await (
                await db.execute(
                    "SELECT day, activity_00_04, activity_04_08, activity_08_12, "
                    "activity_12_16, activity_16_20, activity_20_24 FROM daily_stats WHERE day = ?",
                    (expected_day,),
                )
            ).fetchone()

    row = asyncio.run(_query())

    assert row is not None
    assert row[expected_col] == 1
    for col in (
        "activity_00_04",
        "activity_04_08",
        "activity_08_12",
        "activity_12_16",
        "activity_16_20",
        "activity_20_24",
    ):
        if col != expected_col:
            assert row[col] == 0
