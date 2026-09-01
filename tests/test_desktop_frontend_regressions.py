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
FRONTEND = ROOT / "src" / "cyrene" / "workbench" / "webui" / "frontend"
ESBUILD = ROOT / "src" / "cyrene" / "workbench" / "webui" / "node_modules" / ".bin" / "esbuild"


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
    source = _source(FRONTEND / "features" / "settings" / "shared.jsx")
    overlay = _source(FRONTEND / "settings-overlay.jsx")

    assert source.count("window.fetch(") == 1
    assert not re.search(r"(?<![.A-Za-z0-9_])fetch\(", source)
    assert "if (response.ok) return response;" in source
    assert "response.clone().json()" in source
    assert "setRedactSecrets(previousEnabled)" in overlay
    assert "setCapability(\"redactSecrets\", previousEnabled)" in overlay


def test_schedule_and_library_async_results_are_selection_scoped():
    schedule = _source(FRONTEND / "workbench-schedule.jsx")
    library = _source(FRONTEND / "workbench-library.jsx")

    assert "var detailRequestSeqRef = useRef(0);" in schedule
    assert schedule.count("requestSeq === detailRequestSeqRef.current") >= 5
    assert "detailRequestSeqRef.current += 1;" in schedule
    assert "var loadMoreSeq = useRef(0);" in library
    assert "if (seq !== loadMoreSeq.current) return;" in library
    assert "loadMoreSeq.current += 1;" in library


def test_context_inspector_background_refresh_preserves_visible_content():
    source = _source(FRONTEND / "features" / "chat" / "context-indicator.jsx")
    details_hook = source.split("function useWbcContextDetails(", 1)[1].split(
        "function WbcComposerContextIndicator(", 1
    )[0]

    assert 'var [snapshot, setSnapshot] = useWbcState({ chatId: "", data: null });' in details_hook
    assert "var hasCurrentDetails = !!(" in details_hook
    assert "if (!hasCurrentDetails) setLoading(true);" in details_hook
    assert "&& !hasCurrentDetails) setErrorChatId(chatId);" in details_hook
    assert "loading: loading || (!!open && !currentDetails && !currentError)" in details_hook
    assert details_hook.count("setLoading(true)") == 1


def test_schedule_frontend_persists_local_iana_timezone():
    source = _source(FRONTEND / "workbench-schedule.jsx")

    assert 'localStorage.getItem("cyrene-timezone")' in source
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in source
    assert "dateFromTimezoneInput(startVal, scheduleTimezone)" in source
    assert "schedule_timezone: spec.schedule_timezone" in source
    assert "startDate.getHours()" in source
    assert "startDate.getUTCMinutes()" not in source


def test_scheduled_task_timezone_round_trips_through_plugin_repository(tmp_path):
    from cyrene.plugins.builtin.cyrene_schedule.service import ScheduleRuntimeService

    db_path = str(tmp_path / "cyrene.sqlite3")

    async def exercise() -> None:
        service = ScheduleRuntimeService(db_path)
        await service.ensure_ready()
        task_id = await service.repository.create(
            chat_id=-1,
            prompt="morning reminder",
            schedule_type="cron",
            schedule_value="0 9 * * *",
            next_run="2026-03-08T13:00:00+00:00",
            schedule_timezone="America/New_York",
        )
        task = await service.repository.get(task_id)
        assert task is not None
        assert task.schedule_timezone == "America/New_York"
        assert task.origin_session_id == ""
        assert task.action_type == "agent_task"

    asyncio.run(exercise())


def test_calendar_expansion_uses_the_editable_plugin_timezone_rule_across_dst():
    from cyrene.plugins.builtin.cyrene_schedule.schedule_spec import expand_task

    task = {
        "schedule_type": "cron",
        "schedule_value": "0 9 * * *",
        "schedule_timezone": "America/New_York",
    }
    occurrences = expand_task(
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
        "settings-model-configuration.jsx",
        "workbench-schedule.jsx",
        "workbench-library.jsx",
        "workbench.jsx",
        "workbench-chat.jsx",
        "workbench-quick-chat.jsx",
        "features/chat/context-indicator.jsx",
    ],
)
@pytest.mark.skipif(
    not ESBUILD.is_file(),
    reason="frontend esbuild dependency is not installed",
)
def test_changed_frontend_sources_compile_with_esbuild(filename, tmp_path):
    subprocess.run(
        [
            str(ESBUILD),
            str(FRONTEND / filename),
            "--loader:.jsx=jsx",
            f"--outfile={tmp_path / (filename + '.js')}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
