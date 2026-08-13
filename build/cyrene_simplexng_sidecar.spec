# -*- mode: python ; coding: utf-8 -*-
"""x64 SimpleXNG compatibility sidecar for the native WoA backend."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve()
datas = []
binaries = []
hiddenimports = []
for package in (
    "simplexng", "waitress", "flask", "brotli", "lxml", "msgspec",
    "fasttext", "yaml", "babel", "flask_babel", "whitenoise", "winloop",
    "httpx", "httpcore", "httpx_socks", "anyio", "sniffio", "certifi",
    "platformdirs", "dateutil", "rich", "typer", "valkey", "markdown_it",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(root / "run_simplexng_sidecar.py")],
    pathex=[str(root.parent / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(root)],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="CyreneSimpleXNG", console=True)
