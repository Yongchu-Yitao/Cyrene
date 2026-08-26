# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Cyrene — macOS / Windows / Linux 三平台支持。"""

import os
import platform
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

sys.path.insert(0, str(Path(SPECPATH).resolve()))
from playwright_bundle import collect_browser_toc

_PROJECT_ROOT = Path(SPECPATH).resolve().parent
_SRC = _PROJECT_ROOT / "src"
_ENTRY = str(Path(SPECPATH).resolve() / "run_cyrene.py")
_IS_MAC = sys.platform == "darwin"
_IS_MAC_ARM = _IS_MAC and platform.machine().lower() in {"arm64", "aarch64"}
_IS_WIN = sys.platform == "win32"
_BUNDLE_PLAYWRIGHT = os.environ.get("CYRENE_BUNDLE_PLAYWRIGHT") == "1"
_WOA_NATIVE_CORE = _IS_WIN and os.environ.get("CYRENE_WOA_NATIVE_CORE") == "1"

# 从 pyproject.toml 读取版本号
import tomllib
with open(_PROJECT_ROOT / "pyproject.toml", "rb") as _f:
    _version = tomllib.load(_f)["project"]["version"]

# ---- 静态数据文件 ----
_datas = []
_binaries = []

# webui static
_static_dir = _SRC / "webui" / "static"
if _static_dir.is_dir():
    for f in _static_dir.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            dest = str(f.relative_to(_SRC).parent)
            _datas.append((str(f), dest))

# PowerPoint Office.js task-pane assets live with the bridge package so the
# wheel and frozen desktop app use the same source. PyInstaller does not infer
# non-Python files from the dynamic-module scan below, so include them here.
_office_static_dir = _SRC / "cyrene" / "office" / "static"
if _office_static_dir.is_dir():
    for f in _office_static_dir.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            dest = str(f.relative_to(_SRC).parent)
            _datas.append((str(f), dest))

# .env 模板（打包模式首次启动时复制到用户数据目录）
_env_tpl = _PROJECT_ROOT / ".env.example"
if _env_tpl.exists():
    _datas.append((str(_env_tpl), "."))

# pyproject（供打包后读取当前版本号）
_pyproject = _PROJECT_ROOT / "pyproject.toml"
if _pyproject.exists():
    _datas.append((str(_pyproject), "."))

# ---- 本地包模块自动枚举 ----
# agent/cyrene/webui/route 大量使用 importlib.import_module() 动态加载（tool_impl、
# Plugin 子包等），PyInstaller 静态分析无法追踪。直接扫描 src/ 下所有 .py 文件生成
# 完整列表，避免手动维护漏项。
def _enumerate_local_package(src: Path, pkg: str) -> list:
    root = src / pkg
    if not root.is_dir():
        return []
    names = []
    for f in sorted(root.rglob("*.py")):
        if "__pycache__" in f.parts or "node_modules" in f.parts:
            continue
        rel = f.relative_to(src)
        mod = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        names.append(mod)
    return names

# ---- 隐藏导入 ----
_hidden = (
    _enumerate_local_package(_SRC, "agent")
    + _enumerate_local_package(_SRC, "cyrene")
    + _enumerate_local_package(_SRC, "webui")
    + _enumerate_local_package(_SRC, "route")
)
_hidden += [
    "jinja2", "jinja2.ext",
    "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.logging",
    "anyio", "websockets", "aiosqlite", "apscheduler", "croniter",
    "httpx", "python_multipart", "sniffio",
    "fastapi", "pydantic", "pydantic_core", "pydantic_core._pydantic_core",
    "starlette", "typing_extensions", "annotated_types",
    "dotenv", "telegram", "mcp", "httpx_sse", "sse_starlette", "requests",
    "google.genai",
    "packaging", "pypdf", "pypdfium2", "reportlab", "PIL",
    "numpy", "onnxruntime", "sherpa_onnx", "soundfile", "tokenizers",
    # simplexng runtime deps (vendored searx pulls these in transitively;
    # listed explicitly so PyInstaller collects compiled extensions correctly)
    "waitress", "flask", "brotli", "lxml", "msgspec",
    # fasttext-predict installs the module as `fasttext` (with the
    # fasttext_pybind extension); the dist-info name is not importable.
    "fasttext",
    # PIL C extensions — listed explicitly in case collect_all("PIL")
    # fails silently on some platforms
    "PIL._imaging",
]
if _WOA_NATIVE_CORE:
    _hidden.append("onnxruntime_qnn")
    _hidden = [
        name for name in _hidden
        if name not in {"brotli", "fasttext", "waitress", "flask"}
    ]

# pwd stub (exists only in CI; safe to skip on local builds)
if _IS_WIN:
    _hidden.append("winloop")
    _hidden.append("winpty")
    try:
        import pwd  # noqa: F401
        _hidden.append("pwd")
    except ImportError:
        pass


def _collect_package(name: str) -> None:
    """Collect package modules, data files, and metadata for frozen builds."""
    global _datas, _binaries, _hidden
    try:
        datas, binaries, hiddenimports = collect_all(name)
    except Exception as exc:
        if name in {"google.genai", "openai_codex"}:
            raise SystemExit(
                f"[fatal] collect_all({name!r}) failed for a required runtime package: {exc}"
            ) from exc
        print(f"[warn] collect_all({name!r}) failed: {exc}")
        return

    if name == "rapidocr":
        # The OCR runtime downloads its PP-OCRv6 models on demand (see
        # cyrene.knowledge.ocr / local_models) and always passes explicit
        # model paths, so the ~30 MB of default models bundled in the wheel
        # are dead weight.
        datas = [
            item for item in datas
            if not str(item[1]).startswith("rapidocr/models")
        ]
        # The CLS model is the one exception: ocr.py passes explicit
        # Det/Rec model paths but leaves the Cls config at its defaults, so
        # RapidOCR resolves the CLS model through its default config.  If
        # the bundle lacks it, a frozen app would try a modelscope download
        # into the transient (read-only) bundle dir on every start and fail
        # offline.  Bundle just the single ~0.6 MB CLS onnx; the det/rec
        # small models stay excluded as dead weight.
        import rapidocr as _rapidocr

        _cls_model = (
            Path(_rapidocr.__file__).resolve().parent
            / "models"
            / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
        )
        if not _cls_model.is_file():
            raise SystemExit(
                f"[fatal] rapidocr CLS model not found at {_cls_model}; "
                "frozen OCR would fail offline without it"
            )
        _datas.append((str(_cls_model), "rapidocr/models"))

    _datas.extend(datas)
    _binaries.extend(binaries)
    _hidden.extend(hiddenimports)
    try:
        _datas.extend(copy_metadata(name))
    except Exception:
        pass


for _package in (
    "httpx",
    "httpcore",
    "anyio",
    "certifi",
    "sniffio",
    "h11",
    "idna",
    "jinja2",
    "uvicorn",
    "websockets",
    "python_multipart",
    "aiosqlite",
    "apscheduler",
    "croniter",
    "fastapi",
    "pydantic",
    "pydantic_core",
    "starlette",
    "typing_extensions",
    "annotated_types",
    "dotenv",
    "telegram",
    "mcp",
    "httpx_sse",
    "sse_starlette",
    "requests",
    "google.genai",
    "packaging",
    "pypdf",
    "pypdfium2",
    "reportlab",
    "PIL",
    "numpy",
    "onnxruntime",
    "sherpa_onnx",
    "soundfile",
    "tokenizers",
    "openai_codex",
):
    _collect_package(_package)

if _IS_WIN:
    # pywinpty's Windows wheels carry ConPTY runtime files beside the Python
    # extension. A hidden import alone can omit OpenConsole.exe and conpty.dll,
    # leaving source terminals healthy while frozen terminals cannot emit.
    _collect_package("winpty")
    _winpty_runtime_files = {
        Path(str(item[0])).name.lower()
        for item in (*_datas, *_binaries)
    }
    _missing_winpty_runtime = {
        "openconsole.exe", "conpty.dll"
    } - _winpty_runtime_files
    if _missing_winpty_runtime:
        raise SystemExit(
            "[fatal] Windows bundle is missing pywinpty runtime files: "
            + ", ".join(sorted(_missing_winpty_runtime))
        )

# ``google.genai`` is a namespace package whose distribution name differs
# from its import name.  Some SDK paths query their own package metadata at
# runtime, so keep the dist-info alongside the modules collected above.
try:
    _datas.extend(copy_metadata("google-genai"))
except Exception as exc:
    raise SystemExit(
        "[fatal] PyInstaller could not collect required google-genai metadata; "
        "aborting build"
    ) from exc

if _WOA_NATIVE_CORE:
    _collect_package("onnxruntime_qnn")

if _IS_MAC_ARM:
    # Qwen embedding imports mlx_lm lazily so PyInstaller cannot reliably
    # discover the complete MLX runtime from the local_onnx module alone.
    # Collect both packages explicitly for Apple Silicon desktop releases.
    _collect_package("mlx")
    _collect_package("mlx_lm")

if not _WOA_NATIVE_CORE:
    for _package in ("simplexng", "rapidocr", "waitress", "flask", "brotli", "fasttext", "lxml", "msgspec"):
        _collect_package(_package)

# Electron owns the desktop browser runtime.  Playwright is intentionally
# excluded from normal release builds and remains opt-in for standalone frozen
# builds that do not have the Electron RPC bridge.
if _BUNDLE_PLAYWRIGHT:
    _collect_package("playwright")

if _IS_WIN:
    _collect_package("winloop")

# The openai-codex SDK is required at startup of the model settings page; a
# build environment missing it must fail the build, not ship a broken app.
# The Codex CLI binary is deliberately NOT bundled: it is downloaded on
# demand by cyrene.model_runtime.codex_cli (and excluded below so the SDK's
# lazy import does not drag the multi-hundred-MB runtime into the package).
for _critical in ("openai_codex",):
    if not any(
        _mod == _critical or _mod.startswith(_critical + ".")
        for _mod in _hidden
    ):
        raise SystemExit(
            f"[fatal] PyInstaller could not collect required package {_critical!r}; "
            "aborting build (is it installed in the build environment?)"
        )

_datas = list(dict.fromkeys(_datas))
_binaries = list(dict.fromkeys(_binaries))
_hidden = list(dict.fromkeys(_hidden))

# ---- Playwright Chromium browser runtime (optional) ----
_playwright_browser_toc = []
_playwright_browser_root = os.environ.get("CYRENE_PLAYWRIGHT_BROWSERS_DIR")
if _BUNDLE_PLAYWRIGHT and _playwright_browser_root:
    try:
        _playwright_browser_toc = collect_browser_toc(Path(_playwright_browser_root))
        print(
            f"[spec] Bundling {len(_playwright_browser_toc)} Playwright browser entries "
            f"from {_playwright_browser_root}"
        )
    except ValueError as exc:
        print(f"[warn] skipping Playwright browser bundle: {exc}")

# ---- 排除 ----
_excludes = [
    "tkinter", "matplotlib", "pandas", "scipy",
    "PIL._tkinter_finder", "curses",
    # Codex CLI runtime is downloaded on demand (see codex_cli.py); the SDK
    # imports it lazily but PyInstaller would otherwise collect the whole
    # multi-hundred-MB binary tree.
    "codex_cli_bin",
    # OpenCV is downloaded on demand by OCR (see opencv_runtime.py). The
    # full wheel (with FFmpeg video codecs) is hard-linked into cv2.abi3.so
    # and cannot be slimmed, so it ships outside the bundle; PyInstaller
    # would otherwise drag it in through rapidocr's `import cv2`.
    "cv2",
]
if not _BUNDLE_PLAYWRIGHT:
    _excludes.append("playwright")
if _WOA_NATIVE_CORE:
    _excludes.extend(["simplexng", "rapidocr", "pyclipper", "cv2", "brotli", "fasttext", "setproctitle"])

# ---- 图标 ----
_icon = None
_icon_dir = Path(SPECPATH).resolve()
if _IS_MAC and (_icon_dir / "icon.icns").exists():
    _icon = str(_icon_dir / "icon.icns")
elif _IS_WIN and (_icon_dir / "icon.ico").exists():
    _icon = str(_icon_dir / "icon.ico")

# ============================
a = Analysis(
    [_ENTRY],
    pathex=[str(_SRC)],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[str(Path(SPECPATH).resolve())],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
)
if _playwright_browser_toc:
    a.datas += _playwright_browser_toc

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cyrene",
    icon=_icon,
    console=False,
    target_arch="arm64" if os.environ.get("PYINSTALLER_TARGET_ARCH") == "ARM64" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Cyrene",
)

if _IS_MAC:
    app = BUNDLE(
        coll,
        name="Cyrene.app",
        icon=_icon,
        bundle_identifier="com.cyrene.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "CFBundleShortVersionString": _version,
            "CFBundleName": "Cyrene",
        },
    )
