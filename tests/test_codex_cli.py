from __future__ import annotations

import asyncio
import json
import sys
import zipfile
from pathlib import Path

import pytest

from cyrene.model_runtime import codex_cli


@pytest.mark.parametrize(
    ("platform", "machine", "expected"),
    [
        ("darwin", "arm64", "macosx_11_0_arm64"),
        ("darwin", "x86_64", "macosx_10_9_x86_64"),
        ("win32", "AMD64", "win_amd64"),
        ("win32", "ARM64", "win_arm64"),
        ("linux", "x86_64", "manylinux_2_17_x86_64"),
        ("linux", "aarch64", "manylinux_2_17_aarch64"),
    ],
)
def test_platform_wheel_tag(monkeypatch, platform, machine, expected) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(
        codex_cli.platform, "machine", lambda: machine
    )
    assert codex_cli._platform_wheel_tag() == expected


def test_platform_wheel_tag_rejects_unknown_platform(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "plan9")
    with pytest.raises(RuntimeError, match="not available"):
        codex_cli._platform_wheel_tag()


def test_wheel_for_version_matches_platform_tag() -> None:
    payload = {
        "releases": {
            "0.144.4": [
                {"filename": "openai_codex_cli_bin-0.144.4-py3-none-win_amd64.whl", "size": 100},
                {"filename": "openai_codex_cli_bin-0.144.4-py3-none-macosx_11_0_arm64.whl", "size": 50},
                {"filename": "openai_codex_cli_bin-0.144.4-py3-none-macosx_10_9_x86_64.whl", "size": 40},
            ]
        }
    }
    wheel = codex_cli._wheel_for_version(payload, "0.144.4", "macosx_11_0_arm64")
    assert wheel["filename"].endswith("macosx_11_0_arm64.whl")


def test_wheel_for_version_missing_tag_raises() -> None:
    payload = {
        "releases": {
            "0.144.4": [
                {"filename": "openai_codex_cli_bin-0.144.4-py3-none-win_amd64.whl", "size": 1}
            ]
        }
    }
    with pytest.raises(RuntimeError, match="no macosx_11_0_arm64 wheel"):
        codex_cli._wheel_for_version(payload, "0.144.4", "macosx_11_0_arm64")


def _make_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "codex_cli_bin/__init__.py",
            "def bundled_codex_path():\n    return __file__\n",
        )
        archive.writestr("codex_cli_bin/bin/codex", "#!/bin/sh\necho codex\n")
        archive.writestr("codex_cli_bin/codex-path/rg", "rg\n")
    return path


def test_install_wheel_extracts_and_marks_executable(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)
    wheel = _make_wheel(tmp_path / "wheel.whl")

    binary = codex_cli._install_wheel(wheel, "0.144.4")

    assert binary == tmp_path / "versions" / "0.144.4" / "codex_cli_bin" / "bin" / "codex"
    assert binary.is_file()
    if sys.platform != "win32":
        assert binary.stat().st_mode & 0o111
    assert (
        tmp_path / "versions" / "0.144.4" / "codex_cli_bin" / "codex-path" / "rg"
    ).is_file()
    assert not (tmp_path / ".staging-0.144.4").exists()


def test_install_wheel_rejects_missing_binary(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)
    empty_wheel = tmp_path / "empty.whl"
    with zipfile.ZipFile(empty_wheel, "w") as archive:
        archive.writestr("codex_cli_bin/__init__.py", "")

    with pytest.raises(RuntimeError, match="missing"):
        codex_cli._install_wheel(empty_wheel, "0.144.4")
    assert not (tmp_path / "versions").exists()


def test_installed_cli_requires_marker_and_executable(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)
    binary = tmp_path / "versions" / "0.144.4" / "codex_cli_bin" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")

    assert codex_cli.installed_cli_path() is None

    (tmp_path / "installed.json").write_text(
        json.dumps({"version": "0.144.4"}), encoding="utf-8"
    )
    assert codex_cli.installed_cli_path() is None

    binary.chmod(0o755)
    assert codex_cli.installed_cli_path() == binary
    assert codex_cli.ensure_cli() == binary


def test_ensure_cli_raises_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)
    with pytest.raises(codex_cli.CodexCliMissingError):
        codex_cli.ensure_cli()


def test_status_reflects_install_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)

    state = codex_cli.status()
    assert state["installed"] is False
    assert state["downloading"] is False
    assert state["sdk_pinned_version"] == codex_cli.sdk_pinned_version()

    binary = tmp_path / "versions" / "0.144.4" / "codex_cli_bin" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (tmp_path / "installed.json").write_text(
        json.dumps({"version": "0.144.4"}), encoding="utf-8"
    )

    state = codex_cli.status()
    assert state["installed"] is True
    assert state["version"] == "0.144.4"


def test_remove_other_versions_keeps_only_active(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)
    versions = tmp_path / "versions"
    (versions / "0.144.4").mkdir(parents=True)
    (versions / "0.200.0").mkdir(parents=True)

    codex_cli._remove_other_versions("0.144.4")

    assert (versions / "0.144.4").is_dir()
    assert not (versions / "0.200.0").exists()


def test_sdk_pinned_version_matches_installed_sdk() -> None:
    assert codex_cli.sdk_pinned_version() == "0.144.4"


@pytest.mark.asyncio
async def test_start_download_force_wipes_and_targets_pinned(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)
    binary = tmp_path / "versions" / "0.200.0" / "codex_cli_bin" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (tmp_path / "installed.json").write_text(
        json.dumps({"version": "0.200.0"}), encoding="utf-8"
    )
    scheduled: list[str | None] = []

    async def fake_schedule(version: str | None) -> None:
        scheduled.append(version)

    monkeypatch.setattr(codex_cli, "_schedule_download", fake_schedule)
    monkeypatch.setattr(codex_cli, "sdk_pinned_version", lambda: "0.144.4")

    state = codex_cli.start_download(force=True)
    await asyncio.sleep(0)

    assert state["installed"] is False
    assert state["error"] == ""
    assert not (tmp_path / "installed.json").exists()
    assert not (tmp_path / "versions" / "0.200.0").exists()
    assert scheduled == ["0.144.4"]


@pytest.mark.asyncio
async def test_start_download_without_force_short_circuits_when_installed(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path)
    binary = tmp_path / "versions" / "0.144.4" / "codex_cli_bin" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (tmp_path / "installed.json").write_text(
        json.dumps({"version": "0.144.4"}), encoding="utf-8"
    )

    def unexpected(version: str | None) -> None:
        raise AssertionError("download must not start when already installed")

    monkeypatch.setattr(codex_cli, "_schedule_download", unexpected)

    state = codex_cli.start_download()

    assert state["installed"] is True
    assert state["downloading"] is False


def _legacy_bundle(tmp_path) -> Path:
    bundle = tmp_path / "legacy-bundle"
    binary = bundle / "codex_cli_bin" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho codex\n")
    (bundle / "codex_cli_bin" / "codex-package.json").write_text(
        json.dumps({"version": "0.144.4"}), encoding="utf-8"
    )
    return bundle


def test_migrate_legacy_bundle_copies_into_cache(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path / "cache")
    bundle = _legacy_bundle(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert codex_cli.migrate_legacy_bundle() is True

    assert codex_cli.installed_version() == "0.144.4"
    installed = codex_cli.installed_cli_path()
    assert installed is not None
    assert installed.is_file()
    assert installed != bundle / "codex_cli_bin" / "bin" / "codex"


def test_migrate_legacy_bundle_is_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path / "cache")
    bundle = _legacy_bundle(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert codex_cli.migrate_legacy_bundle() is True
    assert codex_cli.migrate_legacy_bundle() is False


def test_migrate_legacy_bundle_skips_when_already_installed(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path / "cache")
    bundle = _legacy_bundle(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    binary = codex_cli.CODEX_CLI_ROOT / "versions" / "0.9.0" / "codex_cli_bin" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (codex_cli.CODEX_CLI_ROOT / "installed.json").write_text(
        json.dumps({"version": "0.9.0"}), encoding="utf-8"
    )

    assert codex_cli.migrate_legacy_bundle() is False
    assert codex_cli.installed_version() == "0.9.0"


def test_migrate_legacy_bundle_missing_source_returns_false(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path / "cache")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty"), raising=False)

    assert codex_cli.migrate_legacy_bundle() is False


def test_migrate_legacy_bundle_corrupt_source_returns_false(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path / "cache")
    bundle = tmp_path / "legacy-bundle"
    (bundle / "codex_cli_bin" / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert codex_cli.migrate_legacy_bundle() is False
    assert codex_cli.installed_cli_path() is None


def test_migrate_legacy_bundle_requires_frozen_env(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(codex_cli, "CODEX_CLI_ROOT", tmp_path / "cache")

    assert codex_cli.migrate_legacy_bundle() is False
