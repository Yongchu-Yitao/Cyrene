"""Compatibility entry point for direct source and module execution.

The implementation lives in :mod:`cyrene.runtime.host`.  This small launcher
remains because Electron development builds and existing installations execute
``src/cyrene/local_cli.py`` directly.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_source_checkout() -> None:
    if __package__:
        return
    src_dir = Path(__file__).resolve().parents[1]
    src_text = str(src_dir)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)


_bootstrap_source_checkout()

if __name__ == "cyrene.local_cli":
    # Keep imports of the historical module fully patch-compatible.  A plain
    # ``from ... import *`` facade would copy names, so replacing
    # ``cyrene.local_cli._cli_loop`` would not affect the function globals in
    # ``runtime.host``.
    from cyrene.runtime.module_compat import alias_module

    alias_module(__name__, "cyrene.runtime.host")
else:
    from cyrene.runtime.host import main

    __all__ = ["main"]

    if __name__ == "__main__":
        main()
