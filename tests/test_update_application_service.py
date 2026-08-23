from pathlib import Path

from cyrene.runtime import updater
from cyrene.runtime.update_service import DownloadCoordinator


class _Progress:
    def __init__(self):
        self.state = {}

    def checksum_missing(self, info):
        self.state = {"expected_sha256": "", "verification_error": "missing"}
        return "missing"

    def begin(self, info):
        self.state = {
            "downloaded": 0,
            "total": info.asset_size,
            "expected_sha256": info.asset_sha256,
            "actual_sha256": "",
            "verification_error": "",
        }

    def progress(self, downloaded, total):
        self.state.update(downloaded=downloaded, total=total)

    def failure(self, message):
        self.state["verification_error"] = message

    def complete(self, result, *, verified, verification_error):
        self.state.update(
            actual_sha256=result.sha256 if result else "",
            downloaded=result.size if result else self.state["downloaded"],
            verified=verified,
            verification_error=verification_error,
        )

    def current(self):
        return dict(self.state)


async def test_download_coordinator_preserves_verified_progress_contract():
    digest = "a" * 64
    info = updater.UpdateInfo(
        available=True,
        current_version="1",
        latest_version="2",
        download_url="https://example.test/update.dmg",
        asset_size=4,
        asset_sha256=digest,
    )
    progress = _Progress()

    async def download(_url, callback):
        callback(4, 4)
        return updater.DownloadResult(Path("/tmp/update.dmg"), 4, digest)

    result = await DownloadCoordinator(
        progress, download, lambda: info, lambda: False
    ).download()

    assert result == {
        "ok": True,
        "path": "/tmp/update.dmg",
        "size": 4,
        "sha256": digest,
        "verified": True,
    }
    assert progress.state["verified"] is True
    assert progress.state["downloaded"] == 4


async def test_download_coordinator_rejects_release_without_checksum():
    info = updater.UpdateInfo(
        available=True,
        current_version="1",
        latest_version="2",
        download_url="https://example.test/update.dmg",
        asset_size=4,
    )
    progress = _Progress()
    coordinator = DownloadCoordinator(
        progress,
        lambda *_args: None,
        lambda: info,
        lambda: False,
    )

    assert await coordinator.download() == {
        "ok": False,
        "error": "missing",
        "code": "update_checksum_missing",
    }
