import json

import httpx
import pytest

from cyrene.platform import settings_store, update_diagnostics, updater
from cyrene.workbench.application import notifications


def test_installer_failure_is_reported_once_and_kept(tmp_path, monkeypatch):
    report = tmp_path / "last-install.json"
    report.write_text(json.dumps({
        "status": "failed", "stage": "installing", "error": "disk full",
        "exit_code": 37, "log_path": "update.log",
    }), encoding="utf-8-sig")
    saved = {}
    notices = []
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(settings_store, "set_", lambda key, value: saved.update({key: value}))
    monkeypatch.setattr(notifications, "append_notification", lambda **kwargs: notices.append(kwargs))
    update_diagnostics.notify_install_failure(report)
    update_diagnostics.notify_install_failure(report)
    assert len(notices) == 1
    assert "disk full" in notices[0]["body"]
    assert "37" in notices[0]["body"]
    assert report.is_file()


@pytest.mark.parametrize("content", ['{"status":"completed"}', '{"status":"running"}', 'broken', '[]'])
def test_no_false_failure_notification(tmp_path, monkeypatch, content):
    report = tmp_path / "last-install.json"
    report.write_text(content)
    notices = []
    monkeypatch.setattr(notifications, "append_notification", lambda **kwargs: notices.append(kwargs))
    update_diagnostics.notify_install_failure(report)
    assert not notices


def test_notification_failure_can_be_retried(tmp_path, monkeypatch):
    report = tmp_path / "last-install.json"
    report.write_text('{"status":"failed"}')
    saved = []
    monkeypatch.setattr(settings_store, "get", lambda *args: "")
    monkeypatch.setattr(settings_store, "set_", lambda *args: saved.append(args))

    def fail(**kwargs):
        raise OSError("database busy")

    monkeypatch.setattr(notifications, "append_notification", fail)
    update_diagnostics.notify_install_failure(report)
    assert not saved


async def test_update_check_exposes_network_failure(monkeypatch):
    async def fetch(*args):
        raise httpx.ConnectTimeout("secret proxy URL")

    monkeypatch.setattr(updater, "_fetch_target_release", fetch)
    info = await updater.check_for_update(False)
    assert info.error == update_diagnostics.describe_error(httpx.ConnectTimeout("timeout"))
    assert info.error
    assert "secret" not in info.error
    assert not info.available


def test_http_status_survives_error_formatting():
    response = httpx.Response(403, request=httpx.Request("GET", "https://example.test"))
    assert "403" in update_diagnostics.describe_error(httpx.HTTPStatusError("failed", request=response.request, response=response))
