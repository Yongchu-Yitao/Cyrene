"""Focused regressions for desktop review findings fixed together."""

from __future__ import annotations

import asyncio
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "src" / "webui" / "frontend"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_electron_auth_header_is_limited_to_the_discovered_backend_port():
    source = _source(ROOT / "electron" / "main.js")
    injector = source.split("function installAuthHeaderInjector()", 1)[1].split(
        "// ---------------------------------------------------------------------------",
        1,
    )[0]

    port_check = "target.port === String(backendPort || '')"
    header_write = "'X-Cyrene-Token': AUTH_TOKEN"
    assert port_check in injector
    assert "if (!isLocalBackend)" in injector
    assert injector.index(port_check) < injector.index(header_write)


def test_settings_requests_share_http_status_enforcement():
    source = _source(FRONTEND / "settings-overlay.jsx")

    assert source.count("window.fetch(") == 1
    assert not re.search(r"(?<![.A-Za-z0-9_])fetch\(", source)
    assert "if (response.ok) return response;" in source
    assert "response.clone().json()" in source
    assert "setRedactSecrets(previousEnabled)" in source
    assert "setCapability(\"redactSecrets\", previousEnabled)" in source


def test_schedule_and_library_async_results_are_selection_scoped():
    schedule = _source(FRONTEND / "workbench-schedule.jsx")
    library = _source(FRONTEND / "workbench-library.jsx")

    assert "var detailRequestSeqRef = useRef(0);" in schedule
    assert schedule.count("requestSeq === detailRequestSeqRef.current") >= 5
    assert "detailRequestSeqRef.current += 1;" in schedule
    assert "var loadMoreSeq = useRef(0);" in library
    assert "if (seq !== loadMoreSeq.current) return;" in library
    assert "loadMoreSeq.current += 1;" in library


def test_schedule_frontend_persists_local_iana_timezone():
    source = _source(FRONTEND / "workbench-schedule.jsx")

    assert 'localStorage.getItem("cyrene-timezone")' in source
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in source
    assert "dateFromTimezoneInput(startVal, scheduleTimezone)" in source
    assert "schedule_timezone: spec.schedule_timezone" in source
    assert "startDate.getHours()" in source
    assert "startDate.getUTCMinutes()" not in source


def test_scheduled_task_timezone_round_trips_through_database(tmp_path):
    from cyrene.runtime import database

    db_path = str(tmp_path / "cyrene.sqlite3")

    async def exercise() -> None:
        await database.init_db(db_path)
        task_id = await database.create_task(
            db_path,
            -1,
            "morning reminder",
            "cron",
            "0 9 * * *",
            "2026-03-08T13:00:00+00:00",
            schedule_timezone="America/New_York",
        )
        tasks = await database.get_all_tasks(db_path)
        task = next(item for item in tasks if item["id"] == task_id)
        assert task["schedule_timezone"] == "America/New_York"
        assert task["origin_session_id"] == ""
        assert task["action_type"] == "agent_task"

    asyncio.run(exercise())


def test_calendar_expansion_uses_the_same_timezone_across_dst():
    from route.workbench.schedule import _expand_task

    task = {
        "schedule_type": "cron",
        "schedule_value": "0 9 * * *",
        "schedule_timezone": "America/New_York",
    }
    occurrences = _expand_task(
        task,
        datetime(2026, 3, 7, tzinfo=timezone.utc),
        datetime(2026, 3, 10, tzinfo=timezone.utc),
    )
    local_times = [value.astimezone(ZoneInfo("America/New_York")) for value in occurrences]

    assert len(local_times) == 3
    assert [value.hour for value in local_times] == [9, 9, 9]
    assert [value.utcoffset().total_seconds() for value in local_times] == [-18000, -14400, -14400]


@pytest.mark.parametrize(
    "filename",
    [
        "settings-overlay.jsx",
        "workbench-schedule.jsx",
        "workbench-library.jsx",
        "workbench.jsx",
        "workbench-chat.jsx",
        "workbench-quick-chat.jsx",
    ],
)
def test_changed_frontend_sources_compile_with_esbuild(filename, tmp_path):
    esbuild = ROOT / "src" / "webui" / "node_modules" / ".bin" / "esbuild"
    if not esbuild.exists():
        pytest.skip("frontend dependencies are not installed")
    subprocess.run(
        [
            str(esbuild),
            str(FRONTEND / filename),
            "--loader:.jsx=jsx",
            f"--outfile={tmp_path / (filename + '.js')}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
