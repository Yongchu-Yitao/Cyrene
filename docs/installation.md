# Installation

[English](installation.md) · [简体中文](installation.zh-CN.md)

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended), Conda, or `venv`
- Git
- Node.js 20+ when building Web UI assets or running Electron from source

## Linux / macOS

```bash
git clone https://github.com/Yongchu-Yitao/Cyrene.git
cd Cyrene
uv sync

# Build JSX assets when the checkout does not already contain compiled output.
cd src/webui
npm install
node build-jsx.mjs
cd ../..

python -m cyrene --workbench
```

Conda/pip remains supported:

```bash
conda create -n cyrene python=3.12 -y
conda activate cyrene
pip install -e .
```

On first run, the onboarding wizard will ask for your API key and guide you through personality setup.

> You do **not** need to create a `.env` file. Configuration is stored in an encrypted config store and managed through the Web UI or onboarding wizard. A legacy `.env.example` is still provided for backward compatibility.

## Windows

The pre-built Windows installer is recommended. Source installs include
`winloop`, but some SimpleXNG releases still vendor Unix-specific SearXNG code;
use the following compatibility steps only when a source launch reports the
corresponding import/process error.

### 1. Environment

```bash
conda create -n cyrene python=3.12 -y
conda activate cyrene
```

### 2. Dependencies

```bash
pip install aiosqlite apscheduler croniter fastapi httpx jinja2 python-dotenv python-telegram-bot requests sniffio uvicorn "mcp>=1.27.0"
pip install winloop  # uvloop replacement for Windows
pip install simplexng --no-deps
pip install babel brotli clideps flask flask-babel httpx-socks isodate lxml markdown-it-py msgspec platformdirs pyyaml rich setproctitle typer-slim valkey whitenoise
pip install -e . --no-build-isolation
```

> **Tip for China users:** Use Tsinghua mirror for faster downloads:
> ```bash
> pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
> ```

### 3. Windows Compatibility Patches

These patches fix SimpleXNG's vendored code for Windows:

**Replace uvloop with winloop**
Edit `Lib/site-packages/simplexng/_vendor/searx/network/client.py`:
```python
# Replace:
import uvloop
uvloop.install()
# With:
import sys
if sys.platform == 'win32':
    import winloop as uvloop
else:
    import uvloop
uvloop.install()
```

**Replace fork with spawn**
Edit `Lib/site-packages/simplexng/_vendor/searx/plugins/calculator.py`:
```python
# Replace:
mp_fork = multiprocessing.get_context("fork")
# With:
import sys
mp_fork = multiprocessing.get_context("fork" if sys.platform != "win32" else "spawn")
```

**Create pwd stub**
Create `Lib/site-packages/pwd.py`:
```python
"""pwd stub for Windows — SearXNG compatibility."""
import os
def getpwuid(uid):
    name = os.environ.get("USERNAME", "unknown")
    return type("pw", (), {"pw_name": name, "pw_uid": uid})()
```

**Enable JSON API in SimpleXNG**
Edit `Lib/site-packages/simplexng/settings/settings_template.yml`:
```yaml
search:
  formats:
    - html
    - json    # ← add this line
```

### 4. Launch

```bash
python -m cyrene --workbench

# Or use the installed background daemon client:
cyrene start
cyrene status
```

The onboarding wizard will run on first launch.

### Alternative: External SearXNG

If you prefer not to patch, set `SEARXNG_URL` in the encrypted config (or `.env`) to point to an external SearXNG instance and set `SEARXNG_AUTO_START=0`.

## Verify Installation

```bash
conda activate cyrene
cd /path/to/Cyrene
python -m cyrene --workbench
```

Open `http://localhost:4242`. You should see the onboarding wizard on first launch.

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
- **Development/test dependencies**: `pip install -e ".[dev]"`
- **Electron development app**: `cd electron && npm install && npm run dev`

## Next Steps

- Read [Usage](usage.md) for Web UI and CLI guides
- Read [Configuration](configuration.md) for environment variables
