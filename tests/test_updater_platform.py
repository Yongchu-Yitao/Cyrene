"""Updater asset selection — pick the right per-platform GitHub release asset.

Regression test for the Windows bug: ``_platform_filter()`` returned the stale
``win64.exe`` token, which is NOT a substring of the architecture-qualified CI
asset names (``Cyrene-<ver>-win-x64.exe`` / ``-win-arm64.exe`` — note the hyphen
and the ``x``). The match failed, so the update check fell back to ``assets[0]``
— the macOS ``.dmg`` — and Windows users were offered a macOS disk image.

Also pins the latent case-sensitivity bug: the old Linux token ``x64.AppImage``
was compared with ``key in name.lower()``, so it never matched the lowercased
asset name either and fell through to the same ``.dmg`` fallback.

Asset names mirror the electron-builder ``artifactName`` templates in
electron/package.json:
    - macOS:        Cyrene-<ver>-mac.dmg
    - Windows x64:  Cyrene-<ver>-win-x64.exe
    - Windows ARM:  Cyrene-<ver>-win-arm64.exe
    - Linux:        Cyrene-<ver>-x64.AppImage
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cyrene.runtime.updater as updater

VERSION = "0.6.0b2"


def test_fix_release_label_maps_to_pep440_local_version():
    from cyrene.runtime.updater import _release_version

    assert str(_release_version("0.6.16-fix")) == "0.6.16+fix"
    assert _release_version("0.6.17") > _release_version("0.6.16-fix")


# Ordered exactly as GitHub returns them: the macOS .dmg sorts first, which is
# precisely the asset a broken platform match falls back to via assets[0].
RELEASE_ASSETS = [
    {"name": f"Cyrene-{VERSION}-mac.dmg", "browser_download_url": "https://dl/mac.dmg", "size": 11, "digest": "sha256:" + "a" * 64},
    {"name": f"Cyrene-{VERSION}-win-arm64-portable.exe", "browser_download_url": "https://dl/win-arm64-portable.exe", "size": 21, "digest": "sha256:" + "e" * 64},
    {"name": f"Cyrene-{VERSION}-win-arm64.exe", "browser_download_url": "https://dl/win-arm64.exe", "size": 22, "digest": "sha256:" + "b" * 64},
    {"name": f"Cyrene-{VERSION}-win-x64-portable.exe", "browser_download_url": "https://dl/win-x64-portable.exe", "size": 32, "digest": "sha256:" + "f" * 64},
    {"name": f"Cyrene-{VERSION}-win-x64.exe", "browser_download_url": "https://dl/win-x64.exe", "size": 33, "digest": "sha256:" + "c" * 64},
    {"name": f"Cyrene-{VERSION}-x64.AppImage", "browser_download_url": "https://dl/x64.AppImage", "size": 44, "digest": "sha256:" + "d" * 64},
]
URL_BY_NAME = {a["name"]: a["browser_download_url"] for a in RELEASE_ASSETS}
SIZE_BY_NAME = {a["name"]: a["size"] for a in RELEASE_ASSETS}
SHA_BY_NAME = {a["name"]: str(a["digest"]).split(":", 1)[1] for a in RELEASE_ASSETS}


def _set_platform(monkeypatch, platform_name: str, machine: str) -> None:
    """Pretend we are running on ``platform_name`` with CPU arch ``machine``."""
    monkeypatch.delenv("PORTABLE_EXECUTABLE_FILE", raising=False)
    monkeypatch.setattr(updater.sys, "platform", platform_name)
    monkeypatch.setattr(updater.platform, "machine", lambda: machine)


@pytest.fixture
def fake_release(monkeypatch):
    """Stub the GitHub fetch so check_for_update() runs its real selection logic.

    The canned tag is far ahead of any real version so ``available`` is always
    True and we reach the asset-selection branch.
    """
    async def _fake_fetch(client, include_prerelease):
        return {
            "tag_name": "v99.0.0",
            "assets": RELEASE_ASSETS,
            "body": "release notes",
            "published_at": "2026-06-01T12:00:00Z",
        }

    monkeypatch.setattr(updater, "_fetch_target_release", _fake_fetch)


# --- the actual bug: which asset does check_for_update() hand back? -----------

@pytest.mark.parametrize(
    "platform_name, machine, expected",
    [
        ("darwin", "arm64", f"Cyrene-{VERSION}-mac.dmg"),
        ("darwin", "x86_64", f"Cyrene-{VERSION}-mac.dmg"),
        ("win32", "AMD64", f"Cyrene-{VERSION}-win-x64.exe"),
        ("win32", "ARM64", f"Cyrene-{VERSION}-win-arm64.exe"),
        ("linux", "x86_64", f"Cyrene-{VERSION}-x64.AppImage"),
    ],
)
async def test_check_for_update_selects_platform_asset(
    monkeypatch, fake_release, platform_name, machine, expected
):
    _set_platform(monkeypatch, platform_name, machine)

    info = await updater.check_for_update(include_prerelease=False)

    assert info.available is True
    assert info.asset_name == expected
    assert info.download_url == URL_BY_NAME[expected]
    assert info.asset_size == SIZE_BY_NAME[expected]
    assert info.asset_sha256 == SHA_BY_NAME[expected]
    assert info.published_at == "2026-06-01T12:00:00Z"


def test_windows_never_falls_back_to_dmg(monkeypatch, fake_release):
    """Explicit regression guard for the reported symptom (Win → macOS .dmg)."""
    import asyncio

    for machine in ("AMD64", "ARM64"):
        _set_platform(monkeypatch, "win32", machine)
        info = asyncio.run(updater.check_for_update(include_prerelease=False))
        assert info.asset_name.endswith(".exe")
        assert not info.asset_name.endswith(".dmg")


def test_current_version_keeps_release_notes(monkeypatch):
    import asyncio

    async def _fetch(client, include_prerelease):
        return {
            "tag_name": "v1.0.0",
            "assets": [],
            "body": "current release notes",
            "published_at": "2026-06-29T11:30:28Z",
        }

    monkeypatch.setattr(updater, "_fetch_target_release", _fetch)
    monkeypatch.setattr(updater, "_current_version", lambda: "1.0.0")

    info = asyncio.run(updater.check_for_update(include_prerelease=False))

    assert info.available is False
    assert info.latest_version == "1.0.0"
    assert info.release_notes == "current release notes"


# --- the filter token itself, in isolation ------------------------------------

@pytest.mark.parametrize(
    "platform_name, machine, expected_token",
    [
        ("darwin", "arm64", ".dmg"),
        ("darwin", "x86_64", ".dmg"),
        ("win32", "AMD64", "win-x64.exe"),
        ("win32", "ARM64", "win-arm64.exe"),
        ("win32", "aarch64", "win-arm64.exe"),  # be liberal in arch naming
        ("linux", "x86_64", "x64.appimage"),
    ],
)
def test_platform_filter_token(monkeypatch, platform_name, machine, expected_token):
    monkeypatch.delenv("PORTABLE_EXECUTABLE_FILE", raising=False)
    _set_platform(monkeypatch, platform_name, machine)
    token = updater._platform_filter()
    assert token == expected_token
    # The token must be lowercase so the case-insensitive substring match holds.
    assert token == token.lower()


def test_filter_token_is_substring_of_real_asset_name(monkeypatch):
    """The whole point: each platform's token must actually occur in its asset."""
    cases = {
        ("darwin", "arm64"): f"Cyrene-{VERSION}-mac.dmg",
        ("win32", "AMD64"): f"Cyrene-{VERSION}-win-x64.exe",
        ("win32", "ARM64"): f"Cyrene-{VERSION}-win-arm64.exe",
        ("linux", "x86_64"): f"Cyrene-{VERSION}-x64.AppImage",
    }
    for (platform_name, machine), asset_name in cases.items():
        _set_platform(monkeypatch, platform_name, machine)
        token = updater._platform_filter()
        assert token in asset_name.lower(), f"{token!r} not in {asset_name.lower()!r}"


@pytest.mark.parametrize(
    "machine, expected",
    [
        ("AMD64", f"Cyrene-{VERSION}-win-x64-portable.exe"),
        ("ARM64", f"Cyrene-{VERSION}-win-arm64-portable.exe"),
    ],
)
async def test_portable_windows_selects_portable_update(
    monkeypatch, fake_release, machine, expected
):
    _set_platform(monkeypatch, "win32", machine)
    monkeypatch.setenv("PORTABLE_EXECUTABLE_FILE", rf"C:\Apps\{expected}")

    info = await updater.check_for_update(include_prerelease=False)

    assert info.asset_name == expected
    assert info.download_url == URL_BY_NAME[expected]


def test_portable_windows_restart_script_replaces_original_without_uac(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("PORTABLE_EXECUTABLE_FILE", r"C:\Apps\Cyrene-portable.exe")
    monkeypatch.setenv("CYRENE_APP_EXECUTABLE", r"C:\Apps\Cyrene-portable.exe")

    script = updater.get_restart_script(Path(r"C:\Temp\Cyrene-new-portable.exe"))

    assert 'move /Y "C:\\Apps\\Cyrene-portable.exe.new" "C:\\Apps\\Cyrene-portable.exe"' in script
    assert 'start "" "C:\\Apps\\Cyrene-portable.exe"' in script
    assert "-Verb RunAs" not in script


# --- #47: missing platform assets must never fall back to another package -----

def test_no_compatible_asset_does_not_fall_back(monkeypatch):
    """New version exists but no asset matches this platform → no fallback, clear error.

    Pins #47's acceptance criterion "Missing platform assets never fall back to
    another package." The release deliberately ships only macOS + Linux assets
    while we check from Windows: the old ``assets[0]`` fallback would have handed
    back the macOS .dmg.
    """
    import asyncio

    assets = [
        {"name": f"Cyrene-{VERSION}-mac.dmg", "browser_download_url": "https://dl/mac.dmg", "size": 11},
        {"name": f"Cyrene-{VERSION}-x64.AppImage", "browser_download_url": "https://dl/x64.AppImage", "size": 44},
    ]

    async def _fetch(client, include_prerelease):
        return {"tag_name": "v99.0.0", "assets": assets, "body": "notes"}

    monkeypatch.setattr(updater, "_fetch_target_release", _fetch)
    _set_platform(monkeypatch, "win32", "AMD64")

    info = asyncio.run(updater.check_for_update(include_prerelease=False))

    assert info.available is True          # there really is a newer version
    assert info.download_url == ""         # but nothing installable for this platform
    assert info.asset_name == ""           # no asset was selected
    assert info.error                       # and a clear unsupported-platform error
    assert "win32" in info.error            # naming the platform that has no package


def test_update_available_appends_workbench_notification_once(tmp_path, monkeypatch):
    from cyrene.workbench.application import notifications as notifications

    notifications.configure_store(str(tmp_path / "workbench.sqlite3"))
    updater._notified_update_keys.clear()
    monkeypatch.setattr(updater, "app_language", lambda: "zh")

    info = updater.UpdateInfo(
        available=True,
        current_version="1.0.0",
        latest_version="2.0.0",
        published_at="2026-06-01T12:00:00Z",
        asset_name="Cyrene-2.0.0-mac.dmg",
        asset_size=1024 * 1024,
        asset_sha256="a" * 64,
    )

    updater._append_update_notification(info)
    updater._append_update_notification(info)

    payload = notifications.list_notifications()
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["tab"] == "system"
    assert item["title"] == "Cyrene v2.0.0 可用"
    assert item["language"] == "zh"
    assert "Cyrene-2.0.0-mac.dmg" in item["body"]
    assert item["meta"]["category"] == "app_update"
    assert item["meta"]["checksumAvailable"] is True
