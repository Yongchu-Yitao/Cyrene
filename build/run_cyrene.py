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
        "google.genai": None,
        "uvicorn": None,
        "fastapi": None,
        "pydantic_core": None,
        "starlette": None,
        "numpy": None,
        "numpy._core._multiarray_umath": None,
        "onnxruntime": None,
    }
    import os
    import platform
    if os.name == "nt" and platform.machine().lower() in {"arm64", "aarch64"}:
        _smoke_imports["onnxruntime_qnn"] = None
    if os.name == "nt":
        _smoke_imports["winpty"] = None
    if sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        _smoke_imports["mlx"] = None
        _smoke_imports["mlx_lm"] = None
    for _name in _smoke_imports:
        try:
            mod = importlib.import_module(_name)
            _smoke_imports[_name] = getattr(mod, "__version__", "ok")
        except Exception as exc:
            raise RuntimeError(f"critical frozen import {_name!r} failed: {exc}") from exc
    print(f"Cyrene smoke test OK: v{get_version()}")
    for name, version in modules.items():
        print(f"{name}={version}")
    for _name, _ver in _smoke_imports.items():
        print(f"{_name}={_ver}")
    print(f"legacy_module_aliases={len(compatibility_aliases)}")

    # The Codex CLI is no longer bundled: codex_cli.py downloads the wheel
    # on demand (the SDK's openai-codex-cli-bin metadata dependency is
    # satisfied at runtime by that downloader, not by PyInstaller).
    # Smoke-test only that the on-demand downloader and the SDK adapter
    # import cleanly in the frozen build.
    from openai_codex import CodexConfig

    import cyrene.model_runtime.codex_cli as _codex_cli

    if not hasattr(_codex_cli, "status") or not hasattr(_codex_cli, "start_download"):
        raise RuntimeError("On-demand Codex CLI downloader is incomplete")
    print("codex_runtime=on-demand")
    print(f"codex_config={CodexConfig.__name__}")

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


def _run_terminal_smoke_test() -> None:
    """Exercise the frozen daemon and a real ConPTY-backed cmd session."""
    import asyncio
    import base64
    import contextlib
    import os
    import shutil
    import tempfile
    import time
    from pathlib import Path

    if os.name != "nt":
        raise RuntimeError("terminal smoke test is Windows-only")

    async def run() -> None:
        from cyrene.terminal.client import TerminalDaemonClient

        state_dir = Path(tempfile.mkdtemp(prefix="cyrene-terminal-smoke-"))
        client = TerminalDaemonClient(state_dir=state_dir)
        terminal_id = ""
        try:
            from cyrene.tooling.backends.shell_runtime import interactive_argv

            shell, argv = interactive_argv()
            created = None
            for attempt in range(3):
                try:
                    created = await client._request(
                        "create",
                        projectId="release-smoke",
                        cwd=str(state_dir),
                        defaultCwd=str(state_dir),
                        shell=shell,
                        argv=argv,
                        title="ConPTY smoke",
                        cols=100,
                        rows=30,
                        createdBy="release-smoke",
                        launchMode="interactive",
                        activate=True,
                    )
                    break
                except ConnectionError:
                    # The daemon may finish a request just as the short-lived
                    # smoke client connection is being replaced. Recover the
                    # created session before deciding whether to retry.
                    await asyncio.sleep(0.25)
                    with contextlib.suppress(Exception):
                        listed = await client.list("release-smoke")
                        recovered = next(
                            (
                                item
                                for item in list(listed.get("terminals") or [])
                                if item.get("title") == "ConPTY smoke"
                            ),
                            None,
                        )
                        if recovered:
                            created = {"terminal": recovered}
                            break
                    if attempt == 2:
                        raise
            if created is None:
                raise RuntimeError("ConPTY session was not created")
            terminal = dict(created.get("terminal") or {})
            terminal_id = str(terminal.get("id") or "")
            if not terminal_id or terminal.get("status") != "running":
                raise RuntimeError(f"ConPTY session did not stay running: {terminal!r}")
            before = client._connection_info() or {}
            daemon_pid = int(before.get("pid") or 0)
            marker = "CYRENE_WINDOWS_TERMINAL_SMOKE_OUTPUT"
            deadline = time.monotonic() + 30
            next_probe = 0.0
            output = b""
            while time.monotonic() < deadline:
                now = time.monotonic()
                try:
                    if now >= next_probe:
                        await client.input(
                            terminal_id, f"echo {marker}\r\n", actor="user"
                        )
                        next_probe = now + 2
                    snapshot = await client.scrollback(
                        terminal_id, cursor=0, max_bytes=512 * 1024
                    )
                except ConnectionError:
                    await asyncio.sleep(0.1)
                    continue
                output = base64.b64decode(str(snapshot.get("data") or ""))
                if marker.encode() in output:
                    break
                await asyncio.sleep(0.05)
            if marker.encode() not in output:
                raise RuntimeError("ConPTY output did not reach durable scrollback")
            listed = await client.list("release-smoke")
            after = client._connection_info() or {}
            terminals = list(listed.get("terminals") or [])
            if (
                int(after.get("pid") or 0) != daemon_pid
                or not terminals
                or terminals[0].get("status") != "running"
            ):
                raise RuntimeError("terminal daemon restarted during ConPTY smoke test")
            print("CYRENE_WINDOWS_TERMINAL_SMOKE=ok")
        except Exception:
            daemon_log = state_dir / "daemon.log"
            with contextlib.suppress(OSError):
                diagnostics = daemon_log.read_text(
                    encoding="utf-8", errors="replace"
                )[-16_384:]
                if diagnostics.strip():
                    print(f"TERMINAL DAEMON LOG:\n{diagnostics}", file=sys.stderr)
            raise
        finally:
            if terminal_id:
                with contextlib.suppress(Exception):
                    await client.remove(terminal_id)
            info = client._connection_info()
            if info:
                with contextlib.suppress(Exception):
                    await client._recorded_request(info, "shutdown")
                    await client._wait_for_graceful_retirement(
                        int(info.get("pid") or 0)
                    )
            with contextlib.suppress(OSError):
                shutil.rmtree(state_dir)

    asyncio.run(run())


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
        try:
            _run_smoke_test()
        except Exception as _exc:
            # A frozen app's unhandled exception must not pass CI silently;
            # PyInstaller's bootloader can swallow the exit code.
            _write_crash_log(_exc)
            print(f"SMOKE TEST FAILED: {_exc!r}", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)

    if "--terminal-smoke-test" in sys.argv:
        try:
            _run_terminal_smoke_test()
        except Exception as _exc:
            _write_crash_log(_exc)
            print(f"TERMINAL SMOKE TEST FAILED: {_exc!r}", file=sys.stderr)
            raise SystemExit(1)
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

    if "--launch-terminal-daemon" in sys.argv:
        sys.argv.remove("--launch-terminal-daemon")
        from cyrene.terminal.daemon import main as _run_terminal_daemon
        _run_terminal_daemon()
        raise SystemExit(0)

    if "--launch-plugin-host" in sys.argv:
        sys.argv.remove("--launch-plugin-host")
        from cyrene.plugins.host import main as _run_plugin_host

        _run_plugin_host()
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
