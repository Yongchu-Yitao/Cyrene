# -*- mode: python ; coding: utf-8 -*-
"""Minimal x64-only OCR compatibility sidecar for Windows on ARM."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve()
datas = []
binaries = []
hiddenimports = []
for package in (
    "rapidocr", "onnxruntime", "numpy", "PIL", "pyclipper", "cv2",
    "shapely", "yaml", "omegaconf", "tqdm", "colorlog", "requests", "six",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(root / "run_ocr_sidecar.py")],
    pathex=[str(root.parent / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "paddle", "openvino", "tensorrt"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CyreneOcr",
    console=True,
)
