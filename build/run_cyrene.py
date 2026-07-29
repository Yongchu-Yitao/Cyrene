"""PyInstaller 入口 — 原生桌面窗口模式启动 Cyrene。"""
import sys

import anyio
import aiosqlite
import apscheduler
import certifi
import croniter
import h11
import httpcore
import httpx
import importlib
import importlib.util
import jinja2
import multipart
import subprocess
import simplexng
import sniffio
import websockets

from playwright_bundle import find_bundled_browser_dir


def _run_smoke_test() -> None:
    """Verify frozen runtime can import critical dependencies before release."""
    from cyrene.runtime.module_compat import LEGACY_MODULE_ALIASES
    from cyrene.runtime.version import get_version

    modules = {
        "httpx": httpx.__version__,
        "httpcore": getattr(httpcore, "__version__", "unknown"),
        "anyio": getattr(anyio, "__version__", "unknown"),
        "certifi": getattr(certifi, "__version__", "unknown"),
        "h11": getattr(h11, "__version__", "unknown"),
        "sniffio": getattr(sniffio, "__version__", "unknown"),
        "websockets": getattr(websockets, "__version__", "unknown"),
        "jinja2": getattr(jinja2, "__version__", "unknown"),
        "aiosqlite": getattr(aiosqlite, "__version__", "unknown"),
        "apscheduler": getattr(apscheduler, "__version__", "unknown"),
        "croniter": getattr(croniter, "__version__", "unknown"),
        "simplexng": getattr(simplexng, "__version__", "unknown"),
        "multipart": getattr(multipart, "__version__", "unknown"),
    }
    compatibility_aliases = {
        **LEGACY_MODULE_ALIASES,
        "webui.workbench_chat_runs": "cyrene.workbench.chat_runs",
        "webui.workbench_goal_loop": "cyrene.workbench.goal_loop",
        "webui.workbench_notifications": "cyrene.workbench.notifications",
    }
    for legacy_name, canonical_name in compatibility_aliases.items():
        legacy_module = importlib.import_module(legacy_name)
        canonical_module = importlib.import_module(canonical_name)
        if legacy_module is not canonical_module:
            raise RuntimeError(
                f"legacy module alias {legacy_name!r} did not resolve to "
                f"{canonical_name!r}"
            )
    # Smoke-test imports for modules with C extensions that are
    # historically fragile in PyInstaller frozen builds.
    _smoke_imports = {
        "PIL": None,
        "pypdf": None,
        "reportlab": None,
        "mcp": None,
        "uvicorn": None,
        "fastapi": None,
        "pydantic_core": None,
        "starlette": None,
    }
    for _name in _smoke_imports:
        try:
            mod = importlib.import_module(_name)
            _smoke_imports[_name] = getattr(mod, "__version__", "ok")
        except Exception as exc:
            _smoke_imports[_name] = f"FAILED: {exc}"
    print(f"Cyrene smoke test OK: v{get_version()}")
    for name, version in modules.items():
        print(f"{name}={version}")
    for _name, _ver in _smoke_imports.items():
        print(f"{_name}={_ver}")
    print(f"legacy_module_aliases={len(compatibility_aliases)}")

    # OAuth model discovery and login depend on the pinned Codex App Server
    # executable shipped by openai-codex-cli-bin. Importing the Python adapter
    # alone is insufficient: PyInstaller must also retain the platform binary,
    # package metadata, and companion PATH tools.
    from codex_cli_bin import bundled_codex_path
    from openai_codex import CodexConfig

    codex_path = bundled_codex_path()
    codex_version = subprocess.run(
        [str(codex_path), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    if not codex_version:
        raise RuntimeError(f"Bundled Codex runtime returned no version: {codex_path}")
    print(f"codex_runtime={codex_version}")
    print(f"codex_config={CodexConfig.__name__}")

    import os

    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                browser.close()
            print("playwright_browser=ok")
        except Exception as exc:
            print(f"playwright_browser=FAILED: {exc}")
            raise SystemExit(1) from exc
    else:
        if importlib.util.find_spec("playwright") is not None:
            print("playwright_package=FAILED: unexpectedly bundled without a browser runtime")
            raise SystemExit(1)
        print("playwright_package=not bundled")
        print("playwright_browser=not bundled")


def _write_crash_log(exc: BaseException) -> None:
    """Write traceback to cyrene_error.log in the OS temp dir.

    On Windows with console=False the process has no console, so Electron's
    stderr pipe may not receive PyInstaller's C-level output. Writing directly
    from Python guarantees a readable crash log on every platform.
    """
    import datetime
    import os
    import tempfile
    import traceback
    log_path = os.path.join(tempfile.gettempdir(), "cyrene_error.log")
    try:
        with open(log_path, "a", encoding="utf-8") as _f:
            _f.write(f"\n--- {datetime.datetime.now().isoformat()} ---\n")
            traceback.print_exc(file=_f)
    except Exception:
        pass


def _setup_playwright_browsers_path() -> None:
    """Point frozen Playwright at the browser runtime shipped with the app."""
    if not getattr(sys, "frozen", False):
        return

    browser_dir = find_bundled_browser_dir(
        getattr(sys, "_MEIPASS", None),
        sys.executable,
    )
    if browser_dir is not None:
        import os

        # The bundled Python driver and browsers must stay on the same revision.
        # Do not allow a stale inherited environment variable to override them.
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)


if __name__ == "__main__":
    _setup_playwright_browsers_path()

    if "--smoke-test" in sys.argv:
        _run_smoke_test()
        raise SystemExit(0)

    # In a PyInstaller frozen build, sys.executable is the app binary itself.
    # External code (searxng_manager, cli) used to call "sys.executable -m ..."
    # which would launch another full instance of the app — recursive spawning.
    # These flags let the frozen binary act as a trampoline for bundled modules.
    if "--launch-simplexng" in sys.argv:
        sys.argv.remove("--launch-simplexng")
        from cyrene.tooling.backends.simplexng_child import main as _run_simplexng_child
        _run_simplexng_child()
        raise SystemExit(0)

    if "--launch-web" in sys.argv:
        sys.argv.remove("--launch-web")
        if "--electron" in sys.argv:
            sys.argv.remove("--electron")
            sys.argv.append("--electron-mode")
        else:
            # Historical buildinfo values such as "agent" or "legacy" are
            # normalized to the sole supported Workbench surface.
            sys.argv.append("--workbench")
        try:
            from cyrene.runtime.host import main
            main()
        except Exception as _exc:
            _write_crash_log(_exc)
            raise
        raise SystemExit(0)

    if "--gui" not in sys.argv:
        sys.argv.append("--gui")

    try:
        from cyrene.runtime.host import main
        main()
    except Exception as _exc:
        _write_crash_log(_exc)
        raise
