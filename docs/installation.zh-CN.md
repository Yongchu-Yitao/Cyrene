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

预构建 Windows Installer 是当前面向最终用户的受支持路径。

Windows 源码 Environment 仍有一个上游 Packaging 限制：SimpleXNG 没有用
Windows Environment Marker 排除 Unix-only `uvloop`。因此通用
`pip install -e .` 或 Lockfile Sync 可能在 Cyrene Runtime Compatibility
Launcher 生效前就因安装 `uvloop` 失败。

Windows 开发请以仓库中的
[Release Workflow](../.github/workflows/release.yml) Dependency-install Step
为 Canonical Recipe：它会安装 `winloop`，用 `--no-deps` 安装 SimpleXNG，
显式安装其 Transitive Dependency，再避免重新解析上述依赖来安装 Cyrene。
Environment 准备好后：

```bash
cd src/webui
npm install
npm run build
cd ../..

python -m cyrene --workbench
```

不要预先手工修改 `site-packages`。Cyrene 通过
`cyrene.tooling.backends.simplexng_child` 启动 SimpleXNG，在 Runtime 提供
Windows `uvloop`、Multiprocessing Compatibility，并确保启用 JSON Search
Output。这个 Launcher 解决的是安装完成后的 Runtime Compatibility，无法修复
发生在更早阶段的 Package-resolution Failure。

如果特定 SimpleXNG Release 仍无法启动，可在加密配置中把 `SEARXNG_URL`
指向外部 SearXNG，并设置 `SEARXNG_AUTO_START=0`。调整 Dependency 前先保存
Child Process Error；手工改 Vendored Package 不属于当前支持的安装流程。

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
- Locked 完整开发测试 Environment：`uv sync --all-extras`
- Electron：
  `cd electron && npm install && npm run dev`

## 下一步

- [使用](usage.zh-CN.md)
- [配置](configuration.zh-CN.md)
- [架构](architecture.zh-CN.md)
