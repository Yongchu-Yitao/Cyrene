# Installation

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Node.js 20+](https://nodejs.org/) (for WebUI JSX precompilation from source)
- Git

## Linux / macOS

### Using uv (recommended)

```bash
# 1. Clone the repository
git clone https://github.com/Yongchu-Yitao/Cyrene.git
cd Cyrene

# 2. Install dependencies (uv sync reads the locked uv.lock for reproducibility)
uv sync

# 3. Precompile the WebUI JSX → JS
cd src/webui && npm install && node build-jsx.mjs && cd ../..

# 4. Run
python -m cyrene --workbench
```

> The first launch runs an onboarding wizard that guides you through API key and personality setup.

### Using pip / conda

```bash
conda create -n cyrene python=3.12 -y
conda activate cyrene
pip install -e .
cd src/webui && npm install && node build-jsx.mjs && cd ../..
python -m cyrene --workbench
```

> You do **not** need a `.env` file. Configuration is stored in an encrypted config store and managed through the Web UI or onboarding wizard. A legacy `.env.example` is still provided for backward compatibility.

## Windows

Windows requires extra steps because `uvloop` (used by the built-in SimpleXNG) is Unix-only.

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

### 4. Precompile WebUI JSX and launch

```bash
cd src/webui && npm install && node build-jsx.mjs && cd ../..
python -m cyrene --workbench
```

The onboarding wizard will run on first launch.

### Alternative: External SearXNG

If you prefer not to patch, set `SEARXNG_URL` in the encrypted config (or `.env`) to point to an external SearXNG instance and set `SEARXNG_AUTO_START=0`.

## Verify Installation

```bash
cd /path/to/Cyrene
python -m cyrene --workbench
```

Open `http://localhost:4242`. You should see the onboarding wizard on first launch.

To test the agent without the web server:

```bash
python -m cyrene.local_cli
```

## Optional Extras

- **Browser live view & login takeover outside Electron**: `pip install -e ".[browser]"` then `playwright install chromium` (desktop releases use embedded Chromium)
- **Development/test dependencies**: `pip install -e ".[dev]"`

## Next Steps

- Read [Usage](usage.md) for Web UI and CLI guides
- Read [Configuration](configuration.md) for environment variables
