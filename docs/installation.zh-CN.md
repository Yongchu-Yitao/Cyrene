# 安装

[English](installation.md) · [简体中文](installation.zh-CN.md)

## 前置条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（推荐）、Conda 或 `venv`
- Git
- 构建 Web UI/Electron 时需要 Node.js 20+

## Linux / macOS

```bash
git clone https://github.com/Yongchu-Yitao/Cyrene.git
cd Cyrene
uv sync

# Checkout 未包含编译产物时构建 JSX
cd src/webui
npm install
node build-jsx.mjs
cd ../..

python -m cyrene --workbench
```

也可以使用 Conda/pip：

```bash
conda create -n cyrene python=3.12 -y
conda activate cyrene
pip install -e .
```

首次运行会请求 API Key 并引导设置人格。正常使用不需要 `.env`；配置保存在
加密 Store，由 Onboarding/Settings 管理。

## Windows

推荐使用预构建 Windows Installer。源码安装已经声明 `winloop`，但部分
SimpleXNG Release 仍包含 Unix-specific SearXNG 代码；只有遇到对应错误时才
需要以下兼容处理。

### 1. Environment

```bash
conda create -n cyrene python=3.12 -y
conda activate cyrene
```

### 2. Dependencies

优先尝试：

```bash
pip install -e .
```

如果 SimpleXNG Dependency Resolution 失败，可单独安装：

```bash
pip install aiosqlite apscheduler croniter fastapi httpx jinja2 \
  python-dotenv python-telegram-bot requests sniffio uvicorn "mcp>=1.27.0"
pip install winloop
pip install simplexng --no-deps
pip install babel brotli clideps flask flask-babel httpx-socks isodate \
  lxml markdown-it-py msgspec platformdirs pyyaml rich setproctitle \
  typer-slim valkey whitenoise
pip install -e . --no-build-isolation
```

中国大陆可配置镜像：

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. SimpleXNG Compatibility（仅需要时）

在 `Lib/site-packages/simplexng/_vendor/searx/network/client.py` 中用
`winloop` 替代 `uvloop`：

```python
import sys
if sys.platform == "win32":
    import winloop as uvloop
else:
    import uvloop
uvloop.install()
```

在 Vendored Calculator 中把 `fork` Context 改为：

```python
import sys
mp_fork = multiprocessing.get_context(
    "fork" if sys.platform != "win32" else "spawn"
)
```

如果依赖要求 `pwd`，创建最小 Windows Stub：

```python
"""pwd stub for Windows."""
import os

def getpwuid(uid):
    name = os.environ.get("USERNAME", "unknown")
    return type("pw", (), {"pw_name": name, "pw_uid": uid})()
```

确保 SimpleXNG Settings Template 允许 JSON：

```yaml
search:
  formats:
    - html
    - json
```

### 4. 启动

```bash
python -m cyrene --workbench

# 或后台 daemon
cyrene start
cyrene status
```

如果不使用内置 Search，可在加密配置中设置外部 `SEARXNG_URL`，并设置
`SEARXNG_AUTO_START=0`。

## 验证安装

```bash
python -m cyrene --workbench
```

打开 `http://localhost:4242`。首次启动应显示 Onboarding。

无 Web 运行 Agent：

```bash
python -m cyrene.runtime.host
```

主数据库为 `store/cyrene.runtime.database`。旧 `store/cyrene.db` 会在新库
没有数据时自动迁移，经过 SQLite 校验后启用，并保留旧文件用于回滚。

## 可选组件

- 非 Electron Browser：
  `pip install -e ".[browser]" && playwright install chromium`
- 开发测试：`pip install -e ".[dev]"`
- Electron：
  `cd electron && npm install && npm run dev`

## 下一步

- [使用](usage.zh-CN.md)
- [配置](configuration.zh-CN.md)
- [架构](architecture.zh-CN.md)
