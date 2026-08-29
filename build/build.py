#!/usr/bin/env python3
"""Cyrene 构建脚本 — 三平台打包自动化。

用法:
    python build/build.py          # 构建当前平台
    python build/build.py --clean  # 仅清理
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import struct
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from playwright_bundle import has_required_chromium_bundles

if TYPE_CHECKING:
    from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
SPEC_FILE = BUILD_DIR / "cyrene.spec"
OCR_SIDECAR_SPEC = BUILD_DIR / "cyrene_ocr_sidecar.spec"
SIMPLEXNG_SIDECAR_SPEC = BUILD_DIR / "cyrene_simplexng_sidecar.spec"
PLAYWRIGHT_BROWSERS_DIR = BUILD_DIR / ".playwright-browsers"
WEB_LOGO_PATH = PROJECT_ROOT / "src" / "cyrene" / "workbench" / "webui" / "static" / "app" / "logo-mark.png"

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def get_version() -> str:
    """从 pyproject.toml 读取版本号。"""
    import tomllib
    pyproject = PROJECT_ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def get_electron_version() -> str:
    """Read the SemVer-compatible version used in Electron artifact names."""
    package_json = PROJECT_ROOT / "electron" / "package.json"
    with open(package_json, encoding="utf-8") as f:
        data = json.load(f)
    return str(data["version"])


def _mac_dmg_version_aliases() -> tuple[str, ...]:
    """Return every current-version spelling that may name a macOS DMG."""
    return tuple(dict.fromkeys((get_version(), get_electron_version())))


def clean() -> None:
    """清理构建产物。"""
    for d in (BUILD_DIR / "build", DIST_DIR):
        if d.exists():
            shutil.rmtree(d)
            print(f"  cleaned: {d}")
    for f in BUILD_DIR.glob("*.pyc"):
        f.unlink()


def _generate_icns(img: "Image.Image", out_path: Path) -> None:
    """纯 Python 生成 .icns 文件（无需 iconutil）。"""
    import io
    from PIL import Image

    types = {
        16: b"icp4", 32: b"icp5", 64: b"icp6",
        128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10",
    }
    entries = []
    for size, icn_type in types.items():
        buf = io.BytesIO()
        img.resize((size, size), Image.LANCZOS).save(buf, format="PNG")
        png_data = buf.getvalue()
        entries.append(icn_type + struct.pack(">I", len(png_data) + 8) + png_data)
    out_path.write_bytes(b"icns" + struct.pack(">I", sum(len(e) for e in entries) + 8) + b"".join(entries))


def _load_logo_image() -> "Image.Image | None":
    try:
        from PIL import Image, ImageDraw
        import numpy as np
    except ImportError:
        print("  [warn] Pillow not installed, skipping icon generation")
        return None

    logo_src = BUILD_DIR / "logo-source.png"
    if logo_src.exists():
        img = Image.open(logo_src).convert("RGBA")
        arr = np.array(img)
        h, w, _ = arr.shape

        # If the corners are near-white, treat the surrounding margin as
        # background and flood-fill it transparent so the rounded-rectangle
        # icon keeps its shape on macOS / other platforms.
        tol = 15
        near_white = (
            (arr[:, :, 0] >= 255 - tol) &
            (arr[:, :, 1] >= 255 - tol) &
            (arr[:, :, 2] >= 255 - tol)
        )
        corners = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
        if all(near_white[y, x] for y, x in corners):
            visited = np.zeros((h, w), dtype=bool)
            stack = [(y, x) for y, x in corners if near_white[y, x]]
            for y, x in stack:
                visited[y, x] = True
            while stack:
                y, x = stack.pop()
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and near_white[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            arr[visited, 3] = 0
            img = Image.fromarray(arr)

        return img.resize((1024, 1024), Image.LANCZOS)

    size = 512
    img = Image.new("RGBA", (size, size), (30, 30, 50, 255))
    draw = ImageDraw.Draw(img)
    margin = size // 6
    draw.ellipse([margin, margin, size - margin, size - margin], fill=(80, 160, 220, 255))
    draw.ellipse([margin * 2, margin * 2, size - margin * 2, size - margin * 2], fill=(30, 30, 50, 200))
    return img


def generate_icons() -> None:
    """Generate icons from the checked-in logo source when available."""
    from PIL import Image

    icon_png = BUILD_DIR / "icon.png"
    img = _load_logo_image()
    if img is None:
        return
    img.save(icon_png)
    print(f"  generated: {icon_png}")

    _generate_icns(img, BUILD_DIR / "icon.icns")
    print(f"  generated: {BUILD_DIR / 'icon.icns'}")

    img.resize((256, 256), Image.LANCZOS).save(BUILD_DIR / "icon.ico", format="ICO")
    print(f"  generated: {BUILD_DIR / 'icon.ico'}")

    WEB_LOGO_PATH.parent.mkdir(parents=True, exist_ok=True)
    img.resize((256, 256), Image.LANCZOS).save(WEB_LOGO_PATH)
    print(f"  generated: {WEB_LOGO_PATH}")


def build_webui_js() -> None:
    """编译 JSX → compiled/*.js，供 PyInstaller 打包进静态资源。

    compiled/ 在 .gitignore 中不入库，必须在打包前先编译，否则
    Python frozen binary 里 static/app/compiled/ 为空，前端全 404。
    """
    webui_dir = PROJECT_ROOT / "src" / "cyrene" / "workbench" / "webui"
    build_script = webui_dir / "build-jsx.mjs"
    if not build_script.exists():
        print("  [warn] build-jsx.mjs not found, skipping JSX build")
        return

    print("\n[WebUI] Compiling JSX...")
    # Install npm deps if node_modules is absent
    if not (webui_dir / "node_modules").exists():
        result = subprocess.run(["npm", "install"], cwd=str(webui_dir))
        if result.returncode != 0:
            print("  [error] npm install failed in src/cyrene/workbench/webui")
            sys.exit(1)

    result = subprocess.run(["node", "build-jsx.mjs"], cwd=str(webui_dir))
    if result.returncode != 0:
        print("  [error] JSX build failed")
        sys.exit(1)
    print("  [ok] JSX compiled")


def _pe_machine(path: Path) -> int:
    """Return the PE machine field for a Windows native binary."""
    with path.open("rb") as stream:
        stream.seek(0x3C)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset + 4)
        return struct.unpack("<H", stream.read(2))[0]


def _ensure_windows_arm_runtime_dlls() -> None:
    """Replace PyInstaller's VC runtime with the official ARM64X redistributable."""
    target = DIST_DIR / "Cyrene" / "_internal" / "vcruntime140_1.dll"
    if not target.is_file() or _pe_machine(target) in {0xAA64, 0xA641}:
        return

    candidates: list[Path] = []
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidates.append(Path(system_root) / "System32" / target.name)
    redist_root = os.environ.get("VCToolsRedistDir")
    if redist_root:
        candidates.extend(Path(redist_root).glob(f"arm64/**/{target.name}"))
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if program_files_x86:
        vs_root = Path(program_files_x86) / "Microsoft Visual Studio" / "2022"
        candidates.extend(
            vs_root.glob(f"*/VC/Redist/MSVC/*/arm64/**/{target.name}")
        )

    native = next(
        (
            candidate
            for candidate in candidates
            # The official ARM64 redistributable uses an ARM64X final DLL,
            # which may carry the AMD64 machine value while remaining loadable
            # by both native ARM64 and x64 processes.
            if candidate.is_file() and _pe_machine(candidate) in {0x8664, 0xAA64}
        ),
        None,
    )
    if native is None:
        raise SystemExit(f"Native ARM64 {target.name} was not found on the build host")
    shutil.copy2(native, target)
    print(f"  [ok] replaced emulated VC runtime with {native}")


def run_pyinstaller(arch: str = "x64") -> None:
    """运行 PyInstaller。"""
    print("\n[PyInstaller] Building...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / "build"),
        "--noconfirm",
        str(SPEC_FILE),
    ]
    if sys.platform == "win32" and arch == "arm64":
        if platform.machine().lower() not in {"arm64", "aarch64"}:
            raise SystemExit("Windows ARM64 core must be built by an ARM64 Python runtime")
        os.environ["PYINSTALLER_TARGET_ARCH"] = "ARM64"
        os.environ["CYRENE_WOA_NATIVE_CORE"] = "1"
        print("  [target] native ARM64 core backend")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print("  [error] PyInstaller failed")
        sys.exit(1)
    if sys.platform == "win32" and arch == "arm64":
        _ensure_windows_arm_runtime_dlls()
    print("  [ok] PyInstaller done")


def stage_woa_x64_sidecars() -> None:
    """Build x64-only optional services and stage them for electron-builder."""
    if not (IS_WIN and platform.machine().lower() in {"amd64", "x86_64"}):
        raise SystemExit("WoA compatibility sidecars must be built by x64 Python")
    output_root = DIST_DIR / "x64-sidecars"
    for name, spec in (("ocr", OCR_SIDECAR_SPEC), ("simplexng", SIMPLEXNG_SIDECAR_SPEC)):
        target = output_root / name
        target.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "PyInstaller", "--distpath", str(target),
            "--workpath", str(BUILD_DIR / "build" / f"sidecar-{name}"),
            "--noconfirm", str(spec),
        ]
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            raise SystemExit(result.returncode)
    print(f"  [ok] WoA x64 sidecars staged at {output_root}")


def _codesign_mac(app_path: Path) -> None:
    """macOS 代码签名 — 与 v0.3.6 完全相同的方式。"""
    dev_id = os.environ.get("APPLE_DEVELOPER_ID", "")
    if dev_id:
        print(f"  signing with: {dev_id}")
        subprocess.run([
            "codesign", "--deep", "--force", "--options", "runtime",
            "--sign", dev_id, str(app_path),
        ], check=True)
    else:
        print("  ad-hoc signing...")
        subprocess.run([
            "codesign", "--deep", "--force", "--sign", "-", str(app_path),
        ], check=True)
    print("  [ok] signed")


def package_mac() -> Path:
    """macOS: .app → .dmg。"""
    version = get_version()
    app_path = DIST_DIR / "Cyrene.app"

    if not app_path.exists():
        print("  [error] .app not found, check PyInstaller output")
        sys.exit(1)

    _codesign_mac(app_path)

    dmg_path = DIST_DIR / f"Cyrene-{version}.dmg"
    print(f"\n[DMG] Creating {dmg_path.name}...")
    with tempfile.TemporaryDirectory(prefix="cyrene-dmg-") as tmp_dir:
        staging_dir = Path(tmp_dir) / "Cyrene"
        staging_dir.mkdir(parents=True, exist_ok=True)

        staged_app = staging_dir / "Cyrene.app"
        shutil.copytree(app_path, staged_app, symlinks=True)

        apps_link = staging_dir / "Applications"
        if apps_link.exists() or apps_link.is_symlink():
            apps_link.unlink()
        apps_link.symlink_to("/Applications")

        subprocess.run([
            "hdiutil", "create",
            "-volname", "Cyrene",
            "-srcfolder", str(staging_dir),
            "-ov", "-format", "UDZO",
            str(dmg_path),
        ], check=True)
    print(f"  [ok] {dmg_path}")
    return dmg_path


def package_win(arch: str = "x64") -> Path:
    """Windows: onedir → .zip。"""
    version = get_version()
    dir_path = DIST_DIR / "Cyrene"

    if not dir_path.exists():
        print("  [error] Cyrene dir not found, check PyInstaller output")
        sys.exit(1)

    suffix = "win64" if arch == "x64" else "win-arm64"
    zip_path = DIST_DIR / f"Cyrene-{version}-{suffix}.zip"
    print(f"\n[ZIP] Creating {zip_path.name}...")
    shutil.make_archive(
        str(zip_path.with_suffix("")),
        "zip",
        str(DIST_DIR),
        "Cyrene",
    )
    print(f"  [ok] {zip_path}")
    return zip_path


def package_linux() -> list[Path]:
    """Linux: onedir → .tar.gz + .AppImage。"""
    version = get_version()
    dir_path = DIST_DIR / "Cyrene"

    if not dir_path.exists():
        print("  [error] Cyrene dir not found, check PyInstaller output")
        sys.exit(1)

    outputs = []

    # tar.gz
    tar_path = DIST_DIR / f"Cyrene-{version}-x86_64.tar.gz"
    print(f"\n[TAR] Creating {tar_path.name}...")
    shutil.make_archive(
        str(tar_path.with_suffix("").with_suffix("")),
        "gztar",
        str(DIST_DIR),
        "Cyrene",
    )
    outputs.append(tar_path)
    print(f"  [ok] {tar_path}")

    # AppImage
    appimage_path = _create_appimage(dir_path, version)
    if appimage_path:
        outputs.append(appimage_path)

    return outputs


def _create_appimage(dir_path: Path, version: str) -> Path | None:
    """创建 AppImage。"""
    appimagetool = shutil.which("appimagetool")
    if not appimagetool:
        print("  [warn] appimagetool not found, skipping AppImage")
        return None

    appdir = DIST_DIR / "Cyrene.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(dir_path, appdir)

    # 创建 .desktop 文件
    desktop = appdir / "cyrene.desktop"
    desktop.write_text("""[Desktop Entry]
Type=Application
Name=Cyrene
Comment=AI Agent That Evolves
Exec=Cyrene
Icon=cyrene
Terminal=false
Categories=Utility;ArtificialIntelligence;
""")

    # 复制图标
    icon_src = BUILD_DIR / "icon.png"
    if icon_src.exists():
        shutil.copy(icon_src, appdir / "cyrene.png")

    # 创建 AppRun
    apprun = appdir / "AppRun"
    apprun.write_text("""#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/Cyrene" "$@"
""")
    apprun.chmod(0o755)

    output_path = DIST_DIR / f"Cyrene-{version}-x86_64.AppImage"
    print(f"\n[AppImage] Creating {output_path.name}...")
    appimage_env = os.environ.copy()
    appimage_env.setdefault("ARCH", _appimage_arch())
    result = subprocess.run([
        appimagetool, str(appdir), str(output_path),
    ], check=False, env=appimage_env)

    shutil.rmtree(appdir, ignore_errors=True)

    require_appimage = os.environ.get("CYRENE_REQUIRE_APPIMAGE") == "1"
    if result.returncode != 0:
        print(f"  [error] appimagetool failed with exit code {result.returncode}")
        if require_appimage:
            sys.exit(result.returncode)

    if output_path.exists():
        print(f"  [ok] {output_path}")
        return output_path

    if require_appimage:
        print("  [error] AppImage output missing after appimagetool completed")
        sys.exit(1)
    return None


def _appimage_arch() -> str:
    machine = platform.machine().lower()
    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    return arch_map.get(machine, machine or "x86_64")


def ensure_playwright_browsers() -> Path | None:
    """Install Playwright and all Chromium runtime bundles used by Cyrene."""
    print("\n[Playwright] Ensuring browser automation runtime...")
    try:
        pip_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright>=1.40"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  [warn] could not install Playwright: {exc}")
        return None
    if pip_result.returncode != 0:
        print(f"  [warn] pip install playwright failed:\n{pip_result.stderr.strip()[:500]}")
        return None

    PLAYWRIGHT_BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
    install_env = os.environ.copy()
    install_env["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_DIR)
    try:
        install_result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=900,
            env=install_env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  [warn] could not install Chromium: {exc}")
        return None
    if install_result.returncode != 0:
        print(f"  [warn] playwright install chromium failed:\n{install_result.stderr.strip()[:500]}")
        return None
    if not has_required_chromium_bundles(PLAYWRIGHT_BROWSERS_DIR):
        print("  [warn] Playwright install did not produce Chromium and Chromium Headless Shell")
        return None

    print(f"  [ok] Browser bundle: {PLAYWRIGHT_BROWSERS_DIR}")
    return PLAYWRIGHT_BROWSERS_DIR


def configure_playwright_bundle(enabled: bool) -> Path | None:
    """Configure whether the frozen Python bundle includes Playwright.

    Electron releases use the host's embedded Chromium through browser RPC, so
    bundling a second Chromium runtime only increases the installer size.  The
    opt-in path remains available for standalone/non-Electron PyInstaller builds.
    """
    os.environ.pop("CYRENE_PLAYWRIGHT_BROWSERS_DIR", None)
    os.environ["CYRENE_BUNDLE_PLAYWRIGHT"] = "0"
    if not enabled:
        print("\n[Playwright] Skipped (Electron uses its embedded browser runtime)")
        return None

    playwright_browsers = ensure_playwright_browsers()
    if playwright_browsers is None:
        print("  [error] --bundle-playwright was requested but Chromium could not be prepared")
        raise SystemExit(1)
    os.environ["CYRENE_PLAYWRIGHT_BROWSERS_DIR"] = str(playwright_browsers)
    os.environ["CYRENE_BUNDLE_PLAYWRIGHT"] = "1"
    return playwright_browsers


def run_electron_builder(arch: str = "x64") -> None:
    """Run electron-builder to package the Electron app around the PyInstaller bundle."""
    electron_dir = PROJECT_ROOT / "electron"
    sidecar_target = DIST_DIR / "x64-sidecars"
    sidecar_target.mkdir(parents=True, exist_ok=True)
    if IS_WIN and arch == "arm64":
        staged = BUILD_DIR / "woa-x64-sidecars"
        if not staged.is_dir():
            raise SystemExit(f"WoA x64 sidecar artifact is missing: {staged}")
        shutil.copytree(staged, sidecar_target, dirs_exist_ok=True)
        required = (
            sidecar_target / "ocr" / "CyreneOcr.exe",
            sidecar_target / "simplexng" / "CyreneSimpleXNG.exe",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise SystemExit("WoA x64 sidecar executable missing: " + ", ".join(missing))

    def find_electron_builder() -> str | None:
        """Locate the electron-builder binary, checking common locations."""
        import shutil
        # 1) check PATH (npx may not be available on Windows CI)
        exe = shutil.which("electron-builder")
        if exe:
            return exe
        # 2) check node_modules/.bin directly
        bin_dir = electron_dir / "node_modules" / ".bin"
        candidates = ["electron-builder", "electron-builder.cmd"]
        for name in candidates:
            p = bin_dir / name
            if p.exists():
                return str(p)
        return None

    eb = find_electron_builder()
    if not eb:
        print("  [warn] electron-builder not found, skipping Electron packaging")
        print("  [hint] Run: cd electron && npm install")
        return

    print("\n[electron-builder] Packaging...")
    cmd = [eb]
    if IS_MAC:
        cmd.append("--mac")
    elif IS_WIN:
        cmd.append("--win")
        if arch == "arm64":
            cmd.append("--arm64")
        else:
            cmd.append("--x64")
    elif IS_LINUX:
        cmd.append("--linux")

    # On Windows, electron-builder is a .cmd file that needs shell=True
    # (otherwise CreateProcess fails with "not a valid Win32 application").
    result = None
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(cmd, cwd=str(electron_dir), shell=IS_WIN)
        if result.returncode == 0:
            break
        if attempt < max_attempts:
            delay = 10 * attempt
            print(
                "  [warn] electron-builder failed "
                f"(attempt {attempt}/{max_attempts}); retrying in {delay}s"
            )
            time.sleep(delay)
    if result is None or result.returncode != 0:
        print(f"  [error] electron-builder failed after {max_attempts} attempts")
        sys.exit(1)
    print("  [ok] electron-builder done")

    # macOS: sign the .app and repackage.
    if IS_MAC:
        mac_apps = sorted((PROJECT_ROOT / "dist-electron").glob("mac*/Cyrene.app"))
        if mac_apps:
            mac_app = mac_apps[0]
            print(f"\n[macOS] Re-signing {mac_app}...")
            _codesign_mac(mac_app)
            # Strict verification: codesign -v --deep --strict checks that
            # EVERY Mach-O in the bundle is properly signed.
            _v = subprocess.run(
                ["codesign", "-v", "--deep", "--strict", str(mac_app)],
                capture_output=True, text=True, timeout=60,
            )
            if _v.returncode == 0:
                print("  [ok] strict verification passed")
            else:
                print(f"  [error] strict verification FAILED:\n{_v.stderr}")
                sys.exit(1)
            # Re-create the DMG — electron-builder's own DMG was built from
            # the unsigned .app (before we signed python-bundle).
            # hdiutil create with staging preserves resource forks and
            # extended attributes (code signatures) on macOS.
            import glob
            version = get_electron_version()
            dmg_path = PROJECT_ROOT / "dist-electron" / f"Cyrene-{version}-mac.dmg"
            _old_dmgs = sorted({
                old_dmg
                for version_alias in _mac_dmg_version_aliases()
                for old_dmg in glob.glob(
                    str(PROJECT_ROOT / "dist-electron" / f"Cyrene-{version_alias}-mac*.dmg")
                )
            })
            with tempfile.TemporaryDirectory(prefix="cyrene-dmg-") as tmp_dir:
                staging_dir = Path(tmp_dir) / "Cyrene"
                staging_dir.mkdir(parents=True, exist_ok=True)
                staged_app = staging_dir / "Cyrene.app"
                # cp -R preserves extended attributes
                subprocess.run(["cp", "-R", str(mac_app), str(staged_app)], check=True)
                apps_link = staging_dir / "Applications"
                apps_link.symlink_to("/Applications")
                subprocess.run([
                    "hdiutil", "create",
                    "-volname", "Cyrene",
                    "-srcfolder", str(staging_dir),
                    "-ov", "-format", "UDZO",
                    str(dmg_path),
                ], check=True)
            for old in _old_dmgs:
                if old != str(dmg_path):
                    Path(old).unlink(missing_ok=True)
            print(f"  created: {dmg_path.name}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build Cyrene")
    parser.add_argument("--clean", action="store_true", help="仅清理构建产物")
    parser.add_argument("--skip-icons", action="store_true", help="跳过图标生成")
    parser.add_argument("--pyinstaller-only", action="store_true", help="只跑 PyInstaller，跳过 Electron 打包")
    parser.add_argument(
        "--bundle-playwright",
        action="store_true",
        help="在独立 PyInstaller 包中包含 Playwright + Chromium（Electron 桌面包不需要）",
    )
    parser.add_argument(
        "--woa-x64-sidecars-only",
        action="store_true",
        help="构建 WoA 使用的 x64 OCR/SimpleXNG sidecar",
    )
    parser.add_argument(
        "--arch",
        choices=["x64", "arm64"],
        default="x64",
        help="目标架构（目前仅 Windows 构建生效）",
    )
    args = parser.parse_args()

    print(f"Cyrene Builder — {sys.platform}")
    print(f"  project: {PROJECT_ROOT}")
    print(f"  arch: {args.arch}")

    if args.clean:
        clean()
        return

    if args.woa_x64_sidecars_only:
        stage_woa_x64_sidecars()
        return

    clean()

    if not args.skip_icons:
        generate_icons()

    build_webui_js()

    configure_playwright_bundle(args.bundle_playwright)

    run_pyinstaller(arch=args.arch)

    if args.pyinstaller_only:
        print(f"\nDone: {DIST_DIR / 'Cyrene'}")
        return

    # Electron 打包
    run_electron_builder(arch=args.arch)

    # 列出产物
    electron_dist = PROJECT_ROOT / "dist-electron"
    if electron_dist.exists():
        print(f"\nDone: {electron_dist}")
        for f in sorted(electron_dist.iterdir()):
            print(f"  {f.name}")


if __name__ == "__main__":
    main()
