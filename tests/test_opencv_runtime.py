from __future__ import annotations

import builtins
import json
import sys
import zipfile
from pathlib import Path

import pytest

from cyrene.plugins.builtin.cyrene_knowledge import opencv_runtime


@pytest.mark.parametrize(
    ("platform", "machine", "expected"),
    [
        ("darwin", "arm64", "macosx_13_0_arm64"),
        ("darwin", "x86_64", "macosx_14_0_x86_64"),
        ("win32", "AMD64", "win_amd64"),
        ("linux", "x86_64", "manylinux_2_17_x86_64"),
        ("linux", "aarch64", "manylinux_2_17_aarch64"),
    ],
)
def test_platform_wheel_tag(monkeypatch, platform, machine, expected) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(opencv_runtime.platform, "machine", lambda: machine)
    assert opencv_runtime._platform_wheel_tag() == expected


def test_platform_wheel_tag_rejects_woa_local_runtime(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(opencv_runtime.platform, "machine", lambda: "ARM64")
    with pytest.raises(RuntimeError, match="sidecar"):
        opencv_runtime._platform_wheel_tag()


def _make_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cv2/__init__.py", "__version__ = '5.0.0.93'\n")
        archive.writestr("cv2/cv2.abi3.so", b"binary")
        archive.writestr("cv2/other.py", "x = 1\n")
    return path


def test_install_wheel_extracts_cv2_package(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    wheel = _make_wheel(tmp_path / "wheel.whl")

    root = opencv_runtime._install_wheel(wheel, "5.0.0.93")

    assert root == tmp_path / "versions" / "5.0.0.93"
    assert (root / "cv2" / "cv2.abi3.so").is_file()
    assert not (tmp_path / ".staging-5.0.0.93").exists()


def test_install_wheel_rejects_missing_cv2(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    bad_wheel = tmp_path / "bad.whl"
    with zipfile.ZipFile(bad_wheel, "w") as archive:
        archive.writestr("other/__init__.py", "")

    with pytest.raises(RuntimeError, match="missing the cv2 package"):
        opencv_runtime._install_wheel(bad_wheel, "5.0.0.93")
    assert not (tmp_path / "versions").exists()


def test_ensure_requires_installed_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    with pytest.raises(opencv_runtime.OpencvRuntimeMissingError):
        opencv_runtime.ensure()
    assert opencv_runtime.installed_root() is None


def test_installed_root_requires_marker_and_importable_cv2(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    (tmp_path / "versions" / "5.0.0.93" / "cv2").mkdir(parents=True)

    assert opencv_runtime.installed_root() is None

    (tmp_path / "installed.json").write_text(
        json.dumps({"version": "5.0.0.93"}), encoding="utf-8"
    )
    # An empty cv2 directory cannot import, so the runtime is not usable.
    assert opencv_runtime.installed_root() is None

    monkeypatch.setattr(opencv_runtime, "_ensure_on_path", lambda version: True)
    assert opencv_runtime.installed_root() == (
        tmp_path / "versions" / "5.0.0.93"
    )


def test_failed_cv2_import_removes_runtime_from_sys_path(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    runtime = tmp_path / "versions" / "5.0.0.93"
    (runtime / "cv2").mkdir(parents=True)
    isolated_path = list(sys.path)
    isolated_path.insert(0, str(runtime))
    monkeypatch.setattr(opencv_runtime.sys, "path", isolated_path)
    original_import = builtins.__import__

    def reject_cv2(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("broken native module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_cv2)

    assert opencv_runtime._ensure_on_path("5.0.0.93") is False
    assert str(runtime) not in sys.path


def test_installed_root_revalidates_preexisting_runtime_path(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    runtime = tmp_path / "versions" / "5.0.0.93"
    (runtime / "cv2").mkdir(parents=True)
    (tmp_path / "installed.json").write_text(
        json.dumps({"version": "5.0.0.93"}), encoding="utf-8"
    )
    isolated_path = [str(runtime), *sys.path]
    monkeypatch.setattr(opencv_runtime.sys, "path", isolated_path)
    checked = []
    monkeypatch.setattr(
        opencv_runtime,
        "_ensure_on_path",
        lambda version: checked.append(version) or False,
    )

    assert opencv_runtime.installed_root() is None
    assert checked == ["5.0.0.93"]


async def test_failed_download_validation_does_not_write_installed_marker(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    version = "5.0.0.93"
    wheel = tmp_path / ".downloads" / f"opencv-{version}.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"cached wheel")
    runtime = tmp_path / "versions" / version
    (tmp_path / "installed.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )

    monkeypatch.setattr(
        opencv_runtime, "_resolve_wheel", lambda _version: ("https://unused", 12)
    )

    def install(_wheel, _version):
        (runtime / "cv2").mkdir(parents=True)
        return runtime

    monkeypatch.setattr(opencv_runtime, "_install_wheel", install)
    monkeypatch.setattr(opencv_runtime, "_drop_imported_cv2", lambda: None)
    monkeypatch.setattr(opencv_runtime, "_ensure_on_path", lambda _version: False)
    try:
        with pytest.raises(RuntimeError, match="failed to import"):
            await opencv_runtime._download(version)

        assert not (tmp_path / "installed.json").exists()
    finally:
        opencv_runtime._PROGRESS.pop("opencv", None)


def test_status_reflects_install_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)

    state = opencv_runtime.status()
    assert state["installed"] is False
    assert state["downloading"] is False
    assert state["pinned_version"] == "5.0.0.93"

    (tmp_path / "versions" / "5.0.0.93" / "cv2").mkdir(parents=True)
    (tmp_path / "installed.json").write_text(
        json.dumps({"version": "5.0.0.93"}), encoding="utf-8"
    )
    assert opencv_runtime.status()["installed"] is False

    monkeypatch.setattr(opencv_runtime, "_ensure_on_path", lambda version: True)
    assert opencv_runtime.status()["installed"] is True
    assert opencv_runtime.status()["version"] == "5.0.0.93"


def test_remove_other_versions_keeps_only_active(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path)
    versions = tmp_path / "versions"
    (versions / "5.0.0.93").mkdir(parents=True)
    (versions / "4.11.0.86").mkdir(parents=True)

    opencv_runtime._remove_other_versions("5.0.0.93")

    assert (versions / "5.0.0.93").is_dir()
    assert not (versions / "4.11.0.86").exists()


def _legacy_bundle(tmp_path) -> Path:
    bundle = tmp_path / "legacy-bundle"
    cv2_dir = bundle / "cv2"
    cv2_dir.mkdir(parents=True)
    (cv2_dir / "__init__.py").write_text("__version__ = '5.0.0.93'\n")
    (cv2_dir / "cv2.abi3.so").write_bytes(b"binary")
    (cv2_dir / "__dot__dylibs").mkdir()
    (cv2_dir / "__dot__dylibs" / "libavcodec.dylib").write_bytes(b"dylib")
    return bundle


def test_migrate_legacy_bundle_copies_into_cache(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path / "cache")
    bundle = _legacy_bundle(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(opencv_runtime, "_ensure_on_path", lambda version: True)
    monkeypatch.setattr(opencv_runtime, "_legacy_cv2_version", lambda: "5.0.0.93")

    assert opencv_runtime.migrate_legacy_bundle() is True

    assert opencv_runtime.installed_version() == "5.0.0.93"
    root = opencv_runtime.installed_root()
    assert root is not None
    assert (root / "cv2" / "cv2.abi3.so").is_file()
    assert (root / "cv2" / "__dot__dylibs" / "libavcodec.dylib").is_file()


def test_migrate_legacy_bundle_is_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path / "cache")
    bundle = _legacy_bundle(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(opencv_runtime, "_ensure_on_path", lambda version: True)

    assert opencv_runtime.migrate_legacy_bundle() is True
    assert opencv_runtime.migrate_legacy_bundle() is False


def test_migrate_legacy_bundle_missing_source_returns_false(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path / "cache")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty"), raising=False)

    assert opencv_runtime.migrate_legacy_bundle() is False


def test_migrate_legacy_bundle_validation_failure_is_quiet(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path / "cache")
    bundle = _legacy_bundle(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(opencv_runtime, "_ensure_on_path", lambda version: False)

    assert opencv_runtime.migrate_legacy_bundle() is False
    # The failed copy leaves no marker, so the cache stays "not installed".
    assert opencv_runtime.installed_root() is None


def test_migrate_legacy_bundle_skips_when_already_installed(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(opencv_runtime, "OPENCV_ROOT", tmp_path / "cache")
    bundle = _legacy_bundle(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(opencv_runtime, "_ensure_on_path", lambda version: True)

    (opencv_runtime.OPENCV_ROOT / "versions" / "5.0.0.93" / "cv2").mkdir(
        parents=True
    )
    (opencv_runtime.OPENCV_ROOT / "installed.json").write_text(
        json.dumps({"version": "5.0.0.93"}), encoding="utf-8"
    )

    assert opencv_runtime.migrate_legacy_bundle() is False
