import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
sys.path.insert(0, str(BUILD_DIR))

from playwright_bundle import (  # noqa: E402
    collect_browser_toc,
    find_bundled_browser_dir,
    has_required_chromium_bundles,
)


def _load_build_module():
    spec = importlib.util.spec_from_file_location("cyrene_build", BUILD_DIR / "build.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_required_browser_bundles_include_default_headless_shell(tmp_path):
    root = tmp_path / "ms-playwright"
    (root / "chromium-1234").mkdir(parents=True)
    assert has_required_chromium_bundles(root) is False

    (root / "chromium_headless_shell-1234").mkdir()
    assert has_required_chromium_bundles(root) is True


def test_browser_toc_preserves_file_paths_and_symlinks(tmp_path):
    root = tmp_path / "ms-playwright"
    version = root / "chromium-1234" / "Chrome.framework" / "Versions" / "123"
    version.mkdir(parents=True)
    binary = version / "Chrome"
    binary.write_text("binary", encoding="utf-8")
    current = version.parent / "Current"
    current.symlink_to("123", target_is_directory=True)

    entries = collect_browser_toc(root)

    assert (
        os.path.join(
            "ms-playwright",
            "chromium-1234",
            "Chrome.framework",
            "Versions",
            "123",
            "Chrome",
        ),
        str(binary),
        "DATA",
    ) in entries
    assert (
        os.path.join(
            "ms-playwright",
            "chromium-1234",
            "Chrome.framework",
            "Versions",
            "Current",
        ),
        "123",
        "SYMLINK",
    ) in entries


def test_find_bundled_browser_dir_supports_one_folder_layout(tmp_path):
    executable = tmp_path / "python-bundle" / "Cyrene"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")
    browser_dir = executable.parent / "_internal" / "ms-playwright"
    browser_dir.mkdir(parents=True)

    assert find_bundled_browser_dir(executable.parent / "_internal", executable) == browser_dir


def test_find_bundled_browser_dir_supports_macos_bundle_layout(tmp_path):
    executable = tmp_path / "Cyrene.app" / "Contents" / "MacOS" / "Cyrene"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    meipass = executable.parent.parent / "Frameworks"
    meipass.mkdir()
    browser_dir = meipass / "ms-playwright"
    browser_dir.mkdir()

    assert find_bundled_browser_dir(meipass, executable) == browser_dir


def test_ensure_playwright_uses_dedicated_browser_root(tmp_path, monkeypatch):
    build_module = _load_build_module()
    browser_root = tmp_path / "browsers"
    monkeypatch.setattr(build_module, "PLAYWRIGHT_BROWSERS_DIR", browser_root)
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-2:] == ["install", "chromium"]:
            (browser_root / "chromium-1234").mkdir(parents=True)
            (browser_root / "chromium_headless_shell-1234").mkdir()
        return Result()

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)

    assert build_module.ensure_playwright_browsers() == browser_root
    install_call = calls[1]
    assert install_call[0][-3:] == ["playwright", "install", "chromium"]
    assert install_call[1]["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_root)
