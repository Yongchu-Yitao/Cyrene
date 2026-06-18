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

import cyrene.updater as updater

VERSION = "0.6.0b2"

# Ordered exactly as GitHub returns them: the macOS .dmg sorts first, which is
# precisely the asset a broken platform match falls back to via assets[0].
RELEASE_ASSETS = [
    {"name": f"Cyrene-{VERSION}-mac.dmg", "browser_download_url": "https://dl/mac.dmg", "size": 11},
    {"name": f"Cyrene-{VERSION}-win-arm64.exe", "browser_download_url": "https://dl/win-arm64.exe", "size": 22},
    {"name": f"Cyrene-{VERSION}-win-x64.exe", "browser_download_url": "https://dl/win-x64.exe", "size": 33},
    {"name": f"Cyrene-{VERSION}-x64.AppImage", "browser_download_url": "https://dl/x64.AppImage", "size": 44},
]
URL_BY_NAME = {a["name"]: a["browser_download_url"] for a in RELEASE_ASSETS}
SIZE_BY_NAME = {a["name"]: a["size"] for a in RELEASE_ASSETS}


def _set_platform(monkeypatch, platform_name: str, machine: str) -> None:
    """Pretend we are running on ``platform_name`` with CPU arch ``machine``."""
    monkeypatch.setattr(updater.sys, "platform", platform_name)
    monkeypatch.setattr(updater.platform, "machine", lambda: machine)


@pytest.fixture
def fake_release(monkeypatch):
    """Stub the GitHub fetch so check_for_update() runs its real selection logic.

    The canned tag is far ahead of any real version so ``available`` is always
    True and we reach the asset-selection branch.
    """
    async def _fake_fetch(client, include_prerelease):
        return {"tag_name": "v99.0.0", "assets": RELEASE_ASSETS, "body": "release notes"}

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


def test_windows_never_falls_back_to_dmg(monkeypatch, fake_release):
    """Explicit regression guard for the reported symptom (Win → macOS .dmg)."""
    import asyncio

    for machine in ("AMD64", "ARM64"):
        _set_platform(monkeypatch, "win32", machine)
        info = asyncio.run(updater.check_for_update(include_prerelease=False))
        assert info.asset_name.endswith(".exe")
        assert not info.asset_name.endswith(".dmg")


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
