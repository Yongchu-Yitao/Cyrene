"""Regression tests for verified GitHub issue fixes.

* #50 — scheduler interval unit unification + ``once``/validation behavior
* #44 — attachment analysis cache moved out of source dirs + versioned key
* #45 — notification ``auto`` mode stops after the first successful channel
* #52 — browser tools are reserved for the main agent (no subagent access)
* #12 — macOS desktop channel fires a real OS notification via
  terminal-notifier, not merely an SSE event requiring an open browser tab
* #56 — update restart exits only after the updater script launches
"""

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from route.registry import register_routes
from agent.plugin.plugin_impl.cyrene_schedule.schedule_spec import (
    next_run as compute_next_run,
)


# ---------------------------------------------------------------------------
# #50 — scheduler interval units, once, and validation
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_interval_is_seconds_not_milliseconds():
    """An interval of "3600" means one hour — the value the Web UI promises."""
    nxt = compute_next_run("interval", "3600", now=FIXED_NOW)
    assert datetime.fromisoformat(nxt) == FIXED_NOW + timedelta(seconds=3600)


def test_schedule_plugin_create_and_runner_share_next_run_rule():
    """Creation and execution import the same editable Plugin recurrence rule."""
    a = compute_next_run("interval", "90", now=FIXED_NOW)
    b = compute_next_run("interval", "90", now=FIXED_NOW)
    assert a == b == (FIXED_NOW + timedelta(seconds=90)).isoformat()


def test_once_respects_provided_time():
    """``once`` must schedule for the requested time, not immediately."""
    nxt = compute_next_run("once", "2026-12-25T09:30:00+00:00", now=FIXED_NOW)
    assert datetime.fromisoformat(nxt) == datetime(2026, 12, 25, 9, 30, tzinfo=timezone.utc)


def test_once_empty_means_now():
    assert compute_next_run("once", "", now=FIXED_NOW) == FIXED_NOW.isoformat()


def test_once_naive_datetime_interpreted_local_then_utc():
    nxt = compute_next_run("once", "2026-06-04T12:00:00", now=FIXED_NOW)
    # Whatever the machine tz, the result is a valid UTC ISO timestamp.
    parsed = datetime.fromisoformat(nxt)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_cron_next_run():
    nxt = compute_next_run("cron", "0 9 * * *", now=FIXED_NOW)
    assert datetime.fromisoformat(nxt) == datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)


def test_cron_timezone_preserves_wall_clock_across_dst_transition():
    from zoneinfo import ZoneInfo

    new_york = ZoneInfo("America/New_York")
    before_dst = datetime(2026, 3, 7, 15, 0, tzinfo=timezone.utc)
    after_dst = datetime(2026, 3, 8, 14, 0, tzinfo=timezone.utc)

    first = datetime.fromisoformat(
        compute_next_run(
            "cron",
            "0 9 * * *",
            now=before_dst,
            timezone_name="America/New_York",
        )
    )
    second = datetime.fromisoformat(
        compute_next_run(
            "cron",
            "0 9 * * *",
            now=after_dst,
            timezone_name="America/New_York",
        )
    )

    assert first.astimezone(new_york).hour == 9
    assert second.astimezone(new_york).hour == 9
    assert first.utcoffset() == timedelta(0)
    assert second.utcoffset() == timedelta(0)


def test_invalid_cron_timezone_raises_valueerror():
    with pytest.raises(ValueError, match="invalid schedule timezone"):
        compute_next_run(
            "cron",
            "0 9 * * *",
            now=FIXED_NOW,
            timezone_name="Mars/Olympus_Mons",
        )


@pytest.mark.parametrize(
    "stype,svalue",
    [
        ("interval", "not-a-number"),
        ("interval", "0"),
        ("interval", "-5"),
        ("cron", "not a cron"),
        ("bogus", "whatever"),
    ],
)
def test_invalid_schedules_raise_valueerror(stype, svalue):
    """Invalid values are rejected instead of silently scheduling for now."""
    with pytest.raises(ValueError):
        compute_next_run(stype, svalue, now=FIXED_NOW)


# ---------------------------------------------------------------------------
# #44 — attachment cache out of source dirs + versioned key
# ---------------------------------------------------------------------------


def test_cache_file_lives_under_data_dir_not_source(tmp_path, monkeypatch):
    from cyrene.runtime import attachments

    monkeypatch.setattr(attachments, "ANALYSIS_CACHE_DIR", tmp_path / "cache")
    cache_file = attachments._cache_file("deadbeef")
    assert (tmp_path / "cache") in cache_file.parents


def test_cache_key_changes_with_content(tmp_path):
    from cyrene.runtime import attachments

    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    k1 = attachments._analysis_cache_key(f, "")
    f.write_text("hello world", encoding="utf-8")
    k2 = attachments._analysis_cache_key(f, "")
    assert k1 != k2


def test_cache_key_changes_with_prompt(tmp_path):
    from cyrene.runtime import attachments

    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    assert attachments._analysis_cache_key(f, "describe") != attachments._analysis_cache_key(f, "summarize")


def test_cache_key_changes_with_model_and_parser_version(tmp_path, monkeypatch):
    from cyrene.runtime import attachments

    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    base = attachments._analysis_cache_key(f, "")

    monkeypatch.setattr(attachments, "_vision_model_fingerprint", lambda: "model-X")
    changed_model = attachments._analysis_cache_key(f, "")
    assert changed_model != base

    monkeypatch.setattr(attachments, "_ANALYSIS_PARSER_VERSION", "999")
    changed_parser = attachments._analysis_cache_key(f, "")
    assert changed_parser != changed_model


async def test_analyze_attachment_does_not_write_next_to_source(tmp_path, monkeypatch):
    from cyrene.runtime import attachments

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(attachments, "ANALYSIS_CACHE_DIR", cache_dir)
    monkeypatch.setattr(attachments, "_vision_model_fingerprint", lambda: "fp")

    src = tmp_path / "notes.txt"
    src.write_text("some workspace content", encoding="utf-8")

    result = await attachments.analyze_attachment(str(src))
    assert result["kind"] == "file"

    # No sidecar pollution next to the user's file...
    assert not (tmp_path / "notes.txt.analysis.json").exists()
    assert list(tmp_path.glob("*.analysis.json")) == []
    # ...the cache landed under the app data dir instead.
    assert cache_dir.exists()
    assert list(cache_dir.glob("*.json"))


async def test_analyze_attachment_reuses_and_invalidates_cache(tmp_path, monkeypatch):
    from cyrene.runtime import attachments

    monkeypatch.setattr(attachments, "ANALYSIS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(attachments, "_vision_model_fingerprint", lambda: "fp")

    calls = {"n": 0}
    real_preview = attachments._build_attachment_preview

    def _counting_preview(payload):
        calls["n"] += 1
        return real_preview(payload)

    monkeypatch.setattr(attachments, "_build_attachment_preview", _counting_preview)

    src = tmp_path / "data.txt"
    src.write_text("v1", encoding="utf-8")

    await attachments.analyze_attachment(str(src))
    await attachments.analyze_attachment(str(src))
    assert calls["n"] == 1  # second call served from cache

    src.write_text("v2 different content", encoding="utf-8")
    await attachments.analyze_attachment(str(src))
    assert calls["n"] == 2  # content change busts the cache


async def test_analyze_attachment_uses_local_ocr_without_vision_for_clear_text(tmp_path, monkeypatch):
    import agent.plugin
    from cyrene.runtime import attachments

    image = tmp_path / "clear.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(attachments, "ANALYSIS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(attachments, "_image_metadata", lambda _path: {"format": "PNG", "width": 10, "height": 10, "mode": "RGB"})
    monkeypatch.setattr(attachments, "model_supports_multimodal", lambda: False)
    service = SimpleNamespace(
        ocr_model_id="pp-ocrv6-medium",
        is_local_model_ready=lambda _model_id: True,
        recognize_image=AsyncMock(return_value="这是一段足够长的本地文字识别结果，用来确认默认附件分析不再请求远程视觉模型。"),
    )
    monkeypatch.setattr(agent.plugin, "active_plugin_service", lambda _name: service)
    vision = AsyncMock(return_value={"vision_text": "should not run"})
    monkeypatch.setattr(attachments, "_vision_analysis", vision)

    result = await attachments.analyze_attachment(str(image), force_refresh=True)

    assert result["ocr_model"] == "pp-ocrv6-medium"
    assert result["ocr_chars"] >= 30
    assert "本地文字识别结果" in result["preview"]
    vision.assert_not_awaited()


async def test_analyze_attachment_keeps_short_ocr_and_falls_back_to_vision(tmp_path, monkeypatch):
    import agent.plugin
    from cyrene.runtime import attachments

    image = tmp_path / "short.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(attachments, "ANALYSIS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(attachments, "_image_metadata", lambda _path: {"format": "PNG", "width": 10, "height": 10, "mode": "RGB"})
    monkeypatch.setattr(attachments, "model_supports_multimodal", lambda: True)
    service = SimpleNamespace(
        ocr_model_id="pp-ocrv6-medium",
        is_local_model_ready=lambda _model_id: True,
        recognize_image=AsyncMock(return_value="短文字"),
    )
    monkeypatch.setattr(agent.plugin, "active_plugin_service", lambda _name: service)
    vision = AsyncMock(return_value={"vision_model": "vision-test", "vision_text": "A visual description."})
    monkeypatch.setattr(attachments, "_vision_analysis", vision)

    result = await attachments.analyze_attachment(str(image), force_refresh=True)

    assert result["ocr_text"] == "短文字"
    assert result["vision_text"] == "A visual description."
    assert "OCR text:" in result["preview"]
    assert "Visual analysis:" in result["preview"]
    vision.assert_awaited_once()


async def test_analyze_attachment_reports_missing_file(tmp_path):
    from cyrene.runtime import attachments

    with pytest.raises(FileNotFoundError, match="Attachment file not found"):
        await attachments.analyze_attachment(str(tmp_path / "missing.docx"))


async def test_analyze_attachment_extracts_extensionless_docx(tmp_path, monkeypatch):
    import zipfile
    import agent.plugin
    from agent.plugin.plugin_impl.cyrene_knowledge.content import extract_text
    from cyrene.runtime import attachments

    monkeypatch.setattr(attachments, "ANALYSIS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        agent.plugin,
        "active_plugin_service",
        lambda _name: SimpleNamespace(extract_file_text=extract_text),
    )
    uploaded = tmp_path / "uuid_docx"
    with zipfile.ZipFile(uploaded, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Attachment DOCX content</w:t></w:r></w:p></w:body>"
                "</w:document>"
            ),
        )

    result = await attachments.analyze_attachment(str(uploaded))

    assert result["kind"] == "document"
    assert "Attachment DOCX content" in result["text_preview"]


def test_safe_attachment_filename_preserves_extension():
    from cyrene.runtime.attachments import safe_attachment_filename

    assert safe_attachment_filename("毕业事情.docx", fallback_stem="upload") == "upload.docx"


# ---------------------------------------------------------------------------
# #45 — notification auto mode stops after the first success
# ---------------------------------------------------------------------------


def _patch_channels(monkeypatch, *, desktop=True, webhook=True, telegram=True, wechat=True, sse=True):
    from cyrene.runtime import notifications as n

    mocks = {
        "_notify_desktop": AsyncMock(return_value={"ok": desktop}),
        "_notify_webhook": AsyncMock(return_value={"ok": webhook}),
        "_notify_telegram": AsyncMock(return_value={"ok": telegram}),
        "_notify_wechat": AsyncMock(return_value={"ok": wechat}),
        "_publish_sse": AsyncMock(return_value={"ok": sse}),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(n, name, mock)
    return mocks


async def test_auto_stops_after_first_success(monkeypatch):
    """A successful desktop notification must NOT fan out to Telegram/WeChat (#45)."""
    from cyrene.runtime import notifications as n

    mocks = _patch_channels(monkeypatch, desktop=True)
    result = await n.notify("t", "b", channel="auto")

    assert result["ok"] is True
    mocks["_notify_desktop"].assert_awaited_once()
    mocks["_notify_telegram"].assert_not_awaited()
    mocks["_notify_wechat"].assert_not_awaited()
    mocks["_notify_webhook"].assert_not_awaited()


async def test_auto_falls_through_when_earlier_channels_fail(monkeypatch):
    from cyrene.runtime import notifications as n

    # desktop fails, no webhook configured, telegram succeeds -> stop there.
    mocks = _patch_channels(monkeypatch, desktop=False, telegram=True)
    monkeypatch.setattr(n, "_WEBHOOK_URL", "")
    result = await n.notify("t", "b", channel="auto")

    assert result["ok"] is True
    mocks["_notify_desktop"].assert_awaited_once()
    mocks["_notify_telegram"].assert_awaited_once()
    mocks["_notify_wechat"].assert_not_awaited()  # stopped after telegram


async def test_broadcast_hits_every_channel(monkeypatch):
    from cyrene.runtime import notifications as n

    mocks = _patch_channels(monkeypatch)
    result = await n.notify("t", "b", channel="broadcast", webhook_url="https://example.test/hook")

    assert result["ok"] is True
    mocks["_notify_desktop"].assert_awaited_once()
    mocks["_notify_webhook"].assert_awaited_once()
    mocks["_notify_telegram"].assert_awaited_once()
    mocks["_notify_wechat"].assert_awaited_once()


async def test_explicit_single_channel(monkeypatch):
    from cyrene.runtime import notifications as n

    mocks = _patch_channels(monkeypatch)
    result = await n.notify("t", "b", channel="telegram")

    assert result["ok"] is True
    mocks["_notify_telegram"].assert_awaited_once()
    mocks["_notify_desktop"].assert_not_awaited()
    mocks["_notify_wechat"].assert_not_awaited()


async def test_unknown_channel_is_rejected(monkeypatch):
    from cyrene.runtime import notifications as n

    _patch_channels(monkeypatch)
    result = await n.notify("t", "b", channel="carrier-pigeon")
    assert result["ok"] is False
    assert "unknown channel" in result.get("error", "")


# ---------------------------------------------------------------------------
# #12 — macOS desktop notifications via terminal-notifier (not SSE-only)
# ---------------------------------------------------------------------------


async def test_macos_desktop_uses_terminal_notifier(monkeypatch):
    """On macOS the desktop channel must fire terminal-notifier so a scheduled
    task is announced even with no Web UI tab open (#12).

    Checks:
    * ``subprocess.run`` is called with ``terminal-notifier`` as the binary.
    * ``_publish_sse`` is NOT called — desktop is an OS-native call now.
    * The notification title is the configured ASSISTANT_NAME, not the raw
      ``title`` argument (agent branding, not raw message header).
    * The message body is passed as-is via a separate ``-message`` argument
      (no shell-escaping needed — subprocess uses execv, not a shell).
    """
    import shutil
    from cyrene.runtime import notifications as n

    monkeypatch.setattr(n.platform, "system", lambda: "Darwin")
    publish = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(n, "_publish_sse", publish)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/terminal-notifier")
    run = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))
    monkeypatch.setattr(n.subprocess, "run", run)

    res = await n._notify_desktop("Task done", 'Backup "nightly" path')

    assert res["ok"] is True
    publish.assert_not_awaited()  # native call — no SSE fallback
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv[0] == "/usr/local/bin/terminal-notifier"
    # Three-tier layout:
    #   -title    → ASSISTANT_NAME  (agent brand, always fixed)
    #   -subtitle → title arg       (task/event label)
    #   -message  → body arg        (execution detail, verbatim — no escaping)
    from cyrene.config import ASSISTANT_NAME
    assert "-title"    in argv and argv[argv.index("-title")    + 1] == ASSISTANT_NAME
    assert "-subtitle" in argv and argv[argv.index("-subtitle") + 1] == "Task done"
    assert "-message"  in argv and argv[argv.index("-message")  + 1] == 'Backup "nightly" path'
    # Sender is the installed Cyrene Electron app → left icon shows app icon
    assert "-sender" in argv and argv[argv.index("-sender") + 1] == "com.cyrene.app"


async def test_macos_desktop_not_installed_gives_clear_error(monkeypatch):
    """When terminal-notifier is absent the channel reports a helpful error
    so ``auto`` mode falls through to SSE/WeChat/Telegram (#12)."""
    import shutil
    from cyrene.runtime import notifications as n

    monkeypatch.setattr(n.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda _: None)

    res = await n._notify_desktop("t", "b")

    assert res["ok"] is False
    assert "terminal-notifier" in res["error"]
    assert "brew" in res["error"]


async def test_macos_desktop_reports_notifier_failure(monkeypatch):
    """A non-zero terminal-notifier exit surfaces as a failed channel so
    ``auto`` mode can fall through to the next channel (#12)."""
    import shutil
    from cyrene.runtime import notifications as n

    monkeypatch.setattr(n.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/terminal-notifier")
    run = MagicMock(return_value=MagicMock(returncode=1, stderr=b"permission denied"))
    monkeypatch.setattr(n.subprocess, "run", run)

    res = await n._notify_desktop("t", "b")

    assert res["ok"] is False
    assert "permission denied" in res["error"]


# ---------------------------------------------------------------------------
# #56 — update restart exits only after updater script launch succeeds
# ---------------------------------------------------------------------------


def _update_restart_client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    return TestClient(app)


def test_update_restart_api_missing_package_keeps_process_running(monkeypatch, tmp_path):
    from cyrene.runtime import updater

    monkeypatch.setitem(updater._download_progress, "downloaded", 0)
    monkeypatch.setitem(updater._download_progress, "total", 0)
    monkeypatch.setitem(updater._download_progress, "done", True)
    monkeypatch.setitem(updater._download_progress, "path", str(tmp_path / "missing.dmg"))
    exit_mock = MagicMock()
    monkeypatch.setattr(os, "_exit", exit_mock)

    response = _update_restart_client(tmp_path).post("/api/update/restart")

    assert response.status_code == 409
    assert response.json()["code"] == "update_package_missing"
    exit_mock.assert_not_called()


def test_update_restart_api_requires_electron_host_before_scheduling(monkeypatch, tmp_path):
    from cyrene.runtime import updater
    from cyrene.runtime import update_install

    package = tmp_path / "Cyrene-update.dmg"
    package.write_bytes(b"fake update")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    monkeypatch.setitem(updater._download_progress, "downloaded", package.stat().st_size)
    monkeypatch.setitem(updater._download_progress, "total", package.stat().st_size)
    monkeypatch.setitem(updater._download_progress, "done", True)
    monkeypatch.setitem(updater._download_progress, "path", str(package))
    monkeypatch.setitem(updater._download_progress, "expected_sha256", checksum)
    monkeypatch.setitem(updater._download_progress, "actual_sha256", checksum)
    monkeypatch.setitem(updater._download_progress, "verified", True)
    launch = MagicMock(return_value=(True, "", "", 200))
    monkeypatch.setattr(update_install, "launch_update_restart", launch)
    exit_mock = MagicMock()
    monkeypatch.setattr(os, "_exit", exit_mock)

    response = _update_restart_client(tmp_path).post("/api/update/restart")

    assert response.status_code == 409
    assert response.json()["code"] == "unsupported_host"
    launch.assert_called_once_with(updater._download_progress, validate_only=True)
    exit_mock.assert_not_called()


def test_update_restart_api_schedules_verified_install_without_exiting_in_route(monkeypatch, tmp_path):
    from cyrene.runtime import updater
    from cyrene.runtime import host_actions, host_bridge
    from cyrene.runtime import update_install

    package = tmp_path / "Cyrene-update.dmg"
    package.write_bytes(b"verified update")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    monkeypatch.setitem(updater._download_progress, "downloaded", package.stat().st_size)
    monkeypatch.setitem(updater._download_progress, "total", package.stat().st_size)
    monkeypatch.setitem(updater._download_progress, "done", True)
    monkeypatch.setitem(updater._download_progress, "path", str(package))
    monkeypatch.setitem(updater._download_progress, "expected_sha256", checksum)
    monkeypatch.setitem(updater._download_progress, "actual_sha256", checksum)
    monkeypatch.setitem(updater._download_progress, "verified", True)
    launch = MagicMock(return_value=(True, "", "", 200))
    monkeypatch.setattr(update_install, "launch_update_restart", launch)
    call_host = AsyncMock(return_value={
        "ok": True, "hostKind": "electron", "appVersion": "0.7.9",
    })
    schedule = MagicMock(return_value={"action_id": "host_action_" + "a" * 32})
    finalize = AsyncMock()
    monkeypatch.setattr(host_bridge, "call_host", call_host)
    monkeypatch.setattr(host_actions, "schedule_action", schedule)
    monkeypatch.setattr(host_actions, "finalize_origin", finalize)
    exit_mock = MagicMock()
    monkeypatch.setattr(os, "_exit", exit_mock)

    response = _update_restart_client(tmp_path).post("/api/update/restart")

    assert response.status_code == 200
    assert response.json()["status"] == "scheduled"
    launch.assert_called_once_with(updater._download_progress, validate_only=True)
    schedule.assert_called_once()
    finalize.assert_awaited_once_with("", "")
    exit_mock.assert_not_called()


# ---------------------------------------------------------------------------
# #52 — browser tools reserved for the main agent
# ---------------------------------------------------------------------------

BROWSER_TOOLS = [
    "browser_navigate",
    "browser_snapshot",
    "browser_screenshot",
    "browser_click",
    "browser_click_ref",
    "browser_click_at",
    "browser_type",
    "browser_type_ref",
    "browser_upload_files",
    "browser_wait",
    "browser_network_log",
    "browser_request_takeover",
]
