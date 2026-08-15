"""Auto-download behavior for auto_update-enabled update checks.

The update flow is: background check finds a newer release → when the user has
``auto_update`` enabled, download the checksum-pinned package in the background
→ push a "ready" notification → the user explicitly restarts to install.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cyrene.runtime.updater as updater

SHA = "a" * 64


def _info(sha256: str = SHA, size: int = 1000) -> updater.UpdateInfo:
    return updater.UpdateInfo(
        available=True,
        current_version="1.0.0",
        latest_version="2.0.0",
        published_at="2026-06-01T12:00:00Z",
        download_url="https://dl/Cyrene-2.0.0-mac.dmg",
        asset_name="Cyrene-2.0.0-mac.dmg",
        asset_size=size,
        asset_sha256=sha256,
    )


@pytest.fixture
def fresh_auto_state(monkeypatch):
    updater._auto_download_task = None
    updater._notified_update_keys.clear()
    updater._state_restored = False
    # 阻止测试期间的真实持久化写入（各测试自行 patch get/set_ 模拟存储）。
    monkeypatch.setattr("cyrene.runtime.settings_store.set_", lambda *a, **k: None)
    updater._download_progress.update({
        "downloaded": 0, "total": 0, "done": False,
        "path": "", "expected_sha256": "", "actual_sha256": "",
        "verified": False, "verification_error": "",
    })
    yield
    task = updater._auto_download_task
    updater._auto_download_task = None
    if task is not None and not task.done():
        task.cancel()


def _fake_download(size: int = 1000, sha256: str = SHA):
    async def fake(url, progress_callback=None):
        if progress_callback:
            progress_callback(size, size)
        return updater.DownloadResult(path=Path("/tmp/pkg"), size=size, sha256=sha256)

    return fake


def test_auto_update_enabled_default_true():
    assert updater._auto_update_enabled() is True


def test_auto_update_enabled_off(monkeypatch):
    monkeypatch.setattr("cyrene.runtime.settings_store.get", lambda key, default: False)
    assert updater._auto_update_enabled() is False


async def test_auto_download_runs_when_enabled(fresh_auto_state, monkeypatch):
    downloaded = []
    async def fake(url, progress_callback=None):
        downloaded.append(url)
        if progress_callback:
            progress_callback(500, 1000)
        return updater.DownloadResult(path=Path("/tmp/pkg"), size=1000, sha256=SHA)

    monkeypatch.setattr(updater, "download_update", fake)
    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is not None
    await updater._auto_download_task

    assert downloaded == ["https://dl/Cyrene-2.0.0-mac.dmg"]
    assert updater._download_progress["done"] is True
    assert updater._download_progress["verified"] is True
    assert updater._download_progress["path"] == "/tmp/pkg"


async def test_auto_download_disabled_skips(fresh_auto_state, monkeypatch):
    monkeypatch.setattr("cyrene.runtime.settings_store.get", lambda key, default: False)
    monkeypatch.setattr(updater, "download_update", lambda *a, **k: pytest.fail("should not download"))

    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is None


def test_auto_download_skips_without_checksum(fresh_auto_state, monkeypatch):
    monkeypatch.setattr(updater, "download_update", lambda *a, **k: pytest.fail("should not download"))

    updater._maybe_auto_download(_info(sha256=""))
    assert updater._auto_download_task is None


def test_auto_download_skips_when_verified_package_exists(fresh_auto_state, monkeypatch):
    monkeypatch.setattr(updater, "download_update", lambda *a, **k: pytest.fail("should not re-download"))
    updater._download_progress.update({
        "downloaded": 1000, "total": 1000, "done": True,
        "path": "/tmp/pkg", "expected_sha256": SHA, "actual_sha256": SHA,
        "verified": True, "verification_error": "",
    })

    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is None


async def test_auto_download_not_started_twice_while_running(fresh_auto_state, monkeypatch):
    started = asyncio.Event()

    async def fake(url, progress_callback=None):
        started.set()
        await asyncio.sleep(3600)
        return updater.DownloadResult(path=Path("/tmp/pkg"), size=1000, sha256=SHA)

    monkeypatch.setattr(updater, "download_update", fake)
    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is not None
    await started.wait()

    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is not None and not updater._auto_download_task.done()
    updater._auto_download_task.cancel()


async def test_auto_download_failure_keeps_retryable_state(fresh_auto_state, monkeypatch):
    async def fake(url, progress_callback=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(updater, "download_update", fake)
    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is not None
    await updater._auto_download_task

    progress = updater._download_progress
    assert progress["done"] is True
    assert progress["verified"] is False
    assert "network down" in progress["verification_error"]
    # 失败后允许再次下载（task 已完成，不再挡路）。
    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is not None


async def test_run_update_check_once_auto_downloads_and_notifies(fresh_auto_state, monkeypatch, tmp_path):
    from cyrene.workbench import notifications

    store = tmp_path / "workbench_notifications.json"
    monkeypatch.setattr(notifications, "_NOTIFICATIONS_STORE", store)
    monkeypatch.setattr(notifications, "DATA_DIR", tmp_path)
    monkeypatch.setattr(notifications, "_STORE_DB_PATH", "")
    monkeypatch.setattr(updater, "download_update", _fake_download(size=11))

    async def _fake_fetch(client, include_prerelease):
        return {
            "tag_name": "v99.0.0",
            "assets": [{"name": "Cyrene-99.0.0-mac.dmg", "browser_download_url": "https://dl/pkg.dmg", "size": 11, "digest": "sha256:" + SHA}],
            "body": "release notes",
            "published_at": "2026-06-01T12:00:00Z",
        }

    monkeypatch.setattr(updater, "_fetch_target_release", _fake_fetch)
    monkeypatch.setattr(updater.sys, "platform", "darwin")

    info = await updater._run_update_check_once()
    assert info.available is True
    assert updater._auto_download_task is not None
    await updater._auto_download_task

    assert updater._download_progress["verified"] is True
    titles = [item["title"] for item in notifications.list_notifications()["items"]]
    assert any("已就绪" in title for title in titles), titles


def test_persist_then_restore_verified_state(fresh_auto_state, monkeypatch, tmp_path):
    """成功下载的包状态落盘；模拟重启后恢复，避免重复下载几百 MB。"""
    stored: dict = {}

    def fake_set(key, value):
        stored[key] = value

    monkeypatch.setattr("cyrene.runtime.settings_store.get", lambda key, default: stored.get(key, default))
    # fresh_auto_state 已把 set_ 置为 no-op；这里覆盖为 fake_set 记录写入。
    monkeypatch.setattr("cyrene.runtime.settings_store.set_", fake_set)

    pkg = tmp_path / "Cyrene-2.0.0-mac.dmg"
    pkg.write_bytes(b"x")
    result = updater.DownloadResult(path=pkg, size=1000, sha256=SHA)
    updater._persist_download_state(_info(), result)
    assert stored[updater._UPDATE_STATE_KEY]["verified"] is True

    # 模拟进程重启：内存进度清空，从持久化状态恢复。
    updater._download_progress.update({
        "downloaded": 0, "total": 0, "done": False,
        "path": "", "expected_sha256": "", "actual_sha256": "",
        "verified": False, "verification_error": "",
    })
    updater._state_restored = False
    progress = updater.get_download_progress()

    assert progress["done"] is True
    assert progress["verified"] is True
    assert progress["path"] == str(pkg)
    assert progress["actual_sha256"] == SHA


def test_restore_skips_when_package_file_missing(fresh_auto_state, monkeypatch, tmp_path):
    def fake_get(key, default):
        if key == updater._UPDATE_STATE_KEY:
            return {"verified": True, "sha256": SHA, "path": str(tmp_path / "gone.dmg"), "downloaded": 1, "total": 1}
        return default

    monkeypatch.setattr("cyrene.runtime.settings_store.get", fake_get)

    updater._state_restored = False
    progress = updater.get_download_progress()
    assert progress["verified"] is False


async def test_restored_verified_package_skips_redownload(fresh_auto_state, monkeypatch, tmp_path):
    stored = {updater._UPDATE_STATE_KEY: {"verified": True, "sha256": SHA, "path": str(tmp_path / "pkg.dmg"), "downloaded": 1000, "total": 1000}}

    def fake_get(key, default):
        return stored.get(key, default)

    monkeypatch.setattr("cyrene.runtime.settings_store.get", fake_get)
    monkeypatch.setattr(updater, "download_update", lambda *a, **k: pytest.fail("should not re-download"))
    (tmp_path / "pkg.dmg").write_bytes(b"x")

    updater._maybe_auto_download(_info())
    assert updater._auto_download_task is None


class FakeResp:
    """Minimal httpx stream response double for download_update tests."""

    def __init__(self, status_code=200, headers=None, body=b"", gate=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._gate = gate

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self, size):
        if self._gate:
            self._gate.set()
            await self._gate.wait_for_release()
        yield self._body

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 416:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeGate:
    def __init__(self):
        self._entered = asyncio.Event()
        self._release = asyncio.Event()

    def set(self):
        self._entered.set()

    async def wait_for_release(self):
        await self._release.wait()


class FakeClient:
    def __init__(self, responses=None, *a, **k):
        self._responses = list(responses) if responses else [FakeResp(body=b"abc")]
        self._requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, headers=None):
        self._requests.append((url, headers))
        resp = self._responses.pop(0)
        resp.request_headers = headers
        return resp


async def test_download_update_rejects_concurrent_call(monkeypatch, tmp_path):
    gate = FakeGate()
    resp = FakeResp(headers={"content-length": "3"}, body=b"abc", gate=gate)
    client = FakeClient(responses=[resp])
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(updater, "TEMP_DIR", tmp_path)

    first = asyncio.create_task(updater.download_update("https://dl/pkg"))
    await gate._entered.wait()
    with pytest.raises(ValueError, match="already in progress"):
        await updater.download_update("https://dl/pkg")
    gate._release.set()
    result = await first
    assert result.size == 3
    assert result.sha256 == hashlib_sha256(b"abc")
    assert updater._download_in_progress is False


def hashlib_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


async def test_download_resumes_from_existing_partial(monkeypatch, tmp_path):
    """目标已有部分内容时发 Range 续传，最终 sha256 覆盖整个文件。"""
    import hashlib

    dest = tmp_path / "updates" / "pkg.dmg"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"ab")

    resp = FakeResp(
        status_code=206,
        headers={"content-range": "bytes 2-2/3", "content-length": "1"},
        body=b"c",
    )
    client = FakeClient(responses=[resp])
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(updater, "TEMP_DIR", tmp_path)

    result = await updater.download_update("https://dl/pkg.dmg")

    assert client._requests[0][1] == {"Range": "bytes=2-"}
    assert dest.read_bytes() == b"abc"
    assert result.size == 3
    assert result.sha256 == hashlib.sha256(b"abc").hexdigest()


async def test_download_416_uses_complete_local_file(monkeypatch, tmp_path):
    """本地文件已完整（Range 超界）时直接复用，不再下载。"""
    import hashlib

    dest = tmp_path / "updates" / "pkg.dmg"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"abc")

    resp = FakeResp(status_code=416, headers={"content-range": "bytes */3"})
    client = FakeClient(responses=[resp])
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(updater, "TEMP_DIR", tmp_path)

    result = await updater.download_update("https://dl/pkg.dmg")

    assert len(client._requests) == 1
    assert result.size == 3
    assert result.sha256 == hashlib.sha256(b"abc").hexdigest()


async def test_download_416_mismatch_redownloads_from_scratch(monkeypatch, tmp_path):
    """本地部分与服务器总大小不符时删除并从头下载。"""
    import hashlib

    dest = tmp_path / "updates" / "pkg.dmg"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"abcd")  # 4 bytes, server total is 3

    resp416 = FakeResp(status_code=416, headers={"content-range": "bytes */3"})
    resp200 = FakeResp(headers={"content-length": "3"}, body=b"xyz")
    client = FakeClient(responses=[resp416, resp200])
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(updater, "TEMP_DIR", tmp_path)

    result = await updater.download_update("https://dl/pkg.dmg")

    assert len(client._requests) == 2
    assert client._requests[1][1] is None  # 重下时不再带 Range
    assert dest.read_bytes() == b"xyz"
    assert result.size == 3
    assert result.sha256 == hashlib.sha256(b"xyz").hexdigest()


async def test_download_server_ignores_range_rewrites(monkeypatch, tmp_path):
    """服务器忽略 Range（200 全量）时覆盖已有部分，不残留旧数据。"""
    import hashlib

    dest = tmp_path / "updates" / "pkg.dmg"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"zzzz")  # 旧的部分内容，比新包更长

    resp = FakeResp(headers={"content-length": "3"}, body=b"abc")
    client = FakeClient(responses=[resp])
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(updater, "TEMP_DIR", tmp_path)

    result = await updater.download_update("https://dl/pkg.dmg")

    assert dest.read_bytes() == b"abc"
    assert result.size == 3
    assert result.sha256 == hashlib.sha256(b"abc").hexdigest()


def test_restore_pending_partial_download(fresh_auto_state, monkeypatch, tmp_path):
    """进程重启后恢复未完成的下载（部分文件 + 元数据），供续传。"""
    pkg = tmp_path / "updates" / "pkg.dmg"
    pkg.parent.mkdir(parents=True)
    pkg.write_bytes(b"ab")

    def fake_get(key, default):
        if key == updater._UPDATE_PENDING_KEY:
            return {"version": "2.0.0", "sha256": SHA, "total": 3, "path": str(pkg)}
        return default

    monkeypatch.setattr("cyrene.runtime.settings_store.get", fake_get)

    updater._state_restored = False
    progress = updater.get_download_progress()

    assert progress["done"] is False
    assert progress["downloaded"] == 2
    assert progress["total"] == 3
    assert progress["path"] == str(pkg)
    assert progress["expected_sha256"] == SHA


async def test_restored_pending_download_resumes(fresh_auto_state, monkeypatch, tmp_path):
    """恢复的未完成下载会触发后台任务，经 download_update 续传而不是重下。"""
    import hashlib

    abc_sha = hashlib.sha256(b"abc").hexdigest()
    # 文件名必须与 _info().download_url 派生的 dest 名一致，续传才找得到部分文件。
    pkg = tmp_path / "updates" / "Cyrene-2.0.0-mac.dmg"
    pkg.parent.mkdir(parents=True)
    pkg.write_bytes(b"ab")

    def fake_get(key, default):
        if key == updater._UPDATE_PENDING_KEY:
            return {"version": "2.0.0", "sha256": abc_sha, "total": 3, "path": str(pkg)}
        return default

    monkeypatch.setattr("cyrene.runtime.settings_store.get", fake_get)

    resp = FakeResp(status_code=206, headers={"content-range": "bytes 2-2/3", "content-length": "1"}, body=b"c")
    client = FakeClient(responses=[resp])
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(updater, "TEMP_DIR", tmp_path)

    updater._maybe_auto_download(_info(sha256=abc_sha, size=3))
    assert updater._auto_download_task is not None
    await updater._auto_download_task

    assert client._requests[0][1] == {"Range": "bytes=2-"}
    assert pkg.read_bytes() == b"abc"
    assert updater._download_progress["verified"] is True
    assert updater._download_progress["actual_sha256"] == abc_sha
