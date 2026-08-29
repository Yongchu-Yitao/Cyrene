# Installation

[English](installation.md) · [简体中文](installation.zh-CN.md)

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended), Conda, or `venv`
- Git
- Node.js 22.12+ when building Web UI assets or running Electron from source

## Linux / macOS

```bash
git clone https://github.com/Yongchu-Yitao/Cyrene.git
cd Cyrene
uv sync

# Build JSX assets when the checkout does not already contain compiled output.
cd src/cyrene/workbench/webui
npm install
node build-jsx.mjs
cd ../../../..

uv run python -m cyrene
```

Conda/pip remains supported:

```bash
conda create -n cyrene python=3.12 -y
conda activate cyrene
pip install -e .
```

On first run, the onboarding wizard will ask for your API key and guide you through personality setup.

> You do **not** need to create a `.env` file. Configuration is stored in an encrypted config store and managed through the Web UI or onboarding wizard. A legacy `.env.example` is still provided for backward compatibility.

### Pre-built Linux packages

Linux releases provide a portable `AppImage`, a Debian package, and an RPM package:

```bash
# Portable AppImage
chmod +x Cyrene-*-x64.AppImage
./Cyrene-*-x64.AppImage

# Debian / Ubuntu
sudo apt install ./Cyrene-*-x64.deb

# Fedora / RHEL / Rocky Linux / AlmaLinux
sudo dnf install ./Cyrene-*-x64.rpm
```

The desktop app uses hardware acceleration by default on Linux so that the
Workbench's glass and compositing effects remain responsive. If an older Mesa,
Wayland, or virtual-GPU stack produces a blank Electron window, launch Cyrene
with `CYRENE_DISABLE_HARDWARE_ACCELERATION=1` to use the compatibility software
renderer instead.

## Windows

Windows releases include two packages for each supported architecture:

- `Cyrene-<version>-win-<arch>.exe` — the standard installer.
- `Cyrene-<version>-win-<arch>-portable.exe` — a single-file portable app;
  download it and run it directly, with no installation or administrator access.

The portable executable keeps application data in the normal Windows AppData
directories, so moving or updating the executable does not lose settings. Its
in-app updater downloads the matching portable package and replaces the original
executable after Cyrene exits; it does not convert the app into an installed copy.

There is a current upstream packaging limitation for source environments:
SimpleXNG declares Unix-only `uvloop` without a Windows environment marker.
Consequently, a generic `pip install -e .` or lockfile sync can try to install
`uvloop` and fail before Cyrene's runtime compatibility launcher is available.

For Windows development, use the dependency-install sequence in the checked-in
[release workflow](../.github/workflows/release.yml) as the canonical recipe.
It installs `winloop`, installs SimpleXNG with `--no-deps`, explicitly installs
its transitive dependencies, and installs Cyrene without re-resolving those
dependencies. After that environment is prepared:

```bash
cd src/cyrene/workbench/webui
npm install
npm run build
cd ../../../..

uv run python -m cyrene
```

> **Tip for China users:** Use Tsinghua mirror for faster downloads:
> ```bash
> pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
> ```

Do not patch `site-packages` pre-emptively. Cyrene launches SimpleXNG through
`cyrene.plugins.builtin.cyrene_content.simplexng_child`, which supplies the Windows `uvloop`
and multiprocessing compatibility behavior at runtime and ensures JSON search
output is enabled. This launcher solves runtime incompatibilities after
installation; it cannot repair an earlier package-resolution failure.

### Alternative: External SearXNG

If a particular SimpleXNG release still cannot start, point `SEARXNG_URL` at an
external SearXNG instance and set `SEARXNG_AUTO_START=0` in the encrypted
configuration. Capture the child-process error before changing dependencies;
manual edits to vendored packages are not part of the current supported setup.

## Verify Installation

```bash
conda activate cyrene
cd /path/to/Cyrene
uv run python -m cyrene
```

Open `http://localhost:4242`. You should see the onboarding wizard on first launch.

The `cyrene_tools` self-management package is available only when the Electron
host and a Workbench renderer are both connected. A browser-only Web UI or CLI
process can still run normal agent tools, but it cannot borrow an Electron UI
surface that is not registered to that runtime.

The active runtime database is `store/cyrene.runtime.database`. If an older
checkout has `store/cyrene.db`, first startup migrates it with SQLite's backup
API, verifies the new database, and retains the old file for rollback. A
populated new database is never overwritten.

To test the agent without the web server:

```bash
python -m cyrene.runtime.host
```

## Optional Extras

- **Browser live view & login takeover outside Electron**: `pip install -e ".[browser]"` then `playwright install chromium` (desktop releases use embedded Chromium)
- **Locked full development/test environment**: `uv sync --all-extras`
- **Electron development app**: `cd electron && npm install && npm run dev`

## Next Steps

- Read [Usage](usage.md) for Web UI and CLI guides
- Read [Configuration](configuration.md) for environment variables
