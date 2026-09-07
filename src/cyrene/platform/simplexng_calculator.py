"""Keep SimpleXNG calculator answers available with Windows spawn workers.

The upstream evaluator and formatting remain unchanged. Only process startup is
excluded from the existing computation timeout; runaway expressions are still
terminated in a separate process.
"""

from __future__ import annotations

import importlib
import multiprocessing
import os
import sys
import types


def _calculator():
    # Register the vendored searx import path without starting the web server.
    importlib.import_module("simplexng.simplexng")
    return importlib.import_module("searx.plugins.calculator")


def _evaluate(connection, expression):
    try:
        prepare_windows_runtime()
        calculator = _calculator()
        connection.send("ready")
        if connection.recv() != "evaluate":
            return
        try:
            result = calculator._eval_expr(expression)
        except Exception:
            result = None
        connection.send(result)
    finally:
        connection.close()


def evaluate(expression, timeout):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_evaluate, args=(child, expression), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(10) or parent.recv() != "ready":
            return None
        parent.send("evaluate")
        return parent.recv() if parent.poll(timeout) else None
    except (EOFError, OSError):
        return None
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join()
        process.close()


def _timeout_func(self, timeout, func, *args, **kwargs):
    # The calculator's sole caller passes its upstream evaluator and one string.
    return evaluate(*args, timeout=timeout, **kwargs)


def install():
    _calculator().SXNGPlugin.timeout_func = _timeout_func


def prepare_windows_runtime() -> None:
    """Patch SimpleXNG's vendored SearXNG assumptions for Windows."""
    if sys.platform != "win32":
        return

    try:
        import winloop

        # simplexng._vendor.searx.network.client imports uvloop unconditionally.
        # Windows builds ship winloop instead; exposing it under the uvloop name
        # keeps the vendored import path working without editing site-packages.
        sys.modules.setdefault("uvloop", winloop)
    except Exception:
        pass

    if "pwd" not in sys.modules:
        pwd_stub = types.ModuleType("pwd")

        def getpwuid(uid: int):
            name = os.environ.get("USERNAME", "unknown")
            return type("pw", (), {"pw_name": name, "pw_uid": uid})()

        pwd_stub.getpwuid = getpwuid  # type: ignore[attr-defined]
        sys.modules["pwd"] = pwd_stub

    import multiprocessing

    original_get_context = multiprocessing.get_context

    def get_context(method: str | None = None):
        if method == "fork":
            method = "spawn"
        return original_get_context(method)

    multiprocessing.get_context = get_context
