"""Direct source and module entry point.

The implementation lives in :mod:`cyrene.runtime.host`.  This small launcher
remains because Electron development builds and existing installations execute
``src/cyrene/local_cli.py`` directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_source_checkout() -> None:
    """Make direct-file execution use the checkout and its virtual environment."""
    if __package__:
        return

    entrypoint = Path(__file__).resolve()
    src_dir = entrypoint.parents[1]
    project_dir = entrypoint.parents[2]
    src_text = str(src_dir)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)

    if os.environ.get("CYRENE_LOCAL_CLI_BOOTSTRAPPED") == "1":
        return

    venv_dir = project_dir / ".venv"
    venv_python = (
        venv_dir / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv_dir / "bin" / "python3"
    )
    try:
        already_in_checkout_venv = Path(sys.prefix).resolve() == venv_dir.resolve()
    except OSError:
        already_in_checkout_venv = False
    if not venv_python.is_file() or already_in_checkout_venv:
        return

    child_env = os.environ.copy()
    child_env["CYRENE_LOCAL_CLI_BOOTSTRAPPED"] = "1"
    child_env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (src_text, child_env.get("PYTHONPATH", ""))
        if part
    )
    os.execve(
        str(venv_python),
        [str(venv_python), str(entrypoint), *sys.argv[1:]],
        child_env,
    )


_bootstrap_source_checkout()

if __name__ == "cyrene.local_cli":
    from cyrene.runtime.host import main
else:
    if __name__ == "__main__" and any(
        flag in sys.argv[1:] for flag in ("--help", "-h")
    ):
        from cyrene.cli import main
    else:
        from cyrene.runtime.host import main

    __all__ = ["main"]

    if __name__ == "__main__":
        main()
