# 安装

[English](installation.md) · [简体中文](installation.zh-CN.md)

## 前置条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（推荐）、Conda 或 `venv`
- Git
- 构建 Web UI/Electron 时需要 Node.js 22.12+

## Linux / macOS

```bash
git clone https://github.com/Yongchu-Yitao/Cyrene.git
cd Cyrene
uv sync

# Checkout 未包含编译产物时构建 JSX
cd src/cyrene/workbench/webui
npm install
node build-jsx.mjs
cd ../../../..

uv run python -m cyrene
```

也可以使用 Conda/pip：

```bash
conda create -n cyrene python=3.12 -y
conda activate cyrene
pip install -e .
```

首次运行会请求 API Key 并引导设置人格。正常使用不需要 `.env`；配置保存在
加密 Store，由 Onboarding/Settings 管理。

### Linux 预构建包

Linux Release 同时提供便携 AppImage、Debian 安装包和 RPM 安装包：

```bash
# 便携 AppImage
chmod +x Cyrene-*-x64.AppImage
./Cyrene-*-x64.AppImage

# Debian / Ubuntu
sudo apt install ./Cyrene-*-x64.deb

# Fedora / RHEL / Rocky Linux / AlmaLinux
sudo dnf install ./Cyrene-*-x64.rpm
```

Linux 桌面版默认使用硬件加速，保证 Workbench 的毛玻璃与合成效果流畅。
如果较旧的 Mesa、Wayland 或虚拟 GPU 环境出现 Electron 纯白窗口，可设置
`CYRENE_DISABLE_HARDWARE_ACCELERATION=1` 启用兼容性软件渲染。

## Windows

Windows Release 会为每种受支持的架构同时提供两种包：

- `Cyrene-<版本>-win-<架构>.exe`：标准安装版。
- `Cyrene-<版本>-win-<架构>-portable.exe`：单文件便携版；下载后直接双击运行，
  无需安装，也不需要管理员权限。

便携版仍将应用数据保存在 Windows 的标准 AppData 目录，因此移动或更新 exe
不会丢失设置。应用内更新会下载相同架构的便携包，并在 Cyrene 退出后原位替换
原始 exe，不会把便携版转换成安装版。

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
cd src/cyrene/workbench/webui
npm install
npm run build
cd ../../../..

uv run python -m cyrene
```

不要预先手工修改 `site-packages`。Cyrene 通过
`cyrene.plugins.builtin.cyrene_content.simplexng_child` 启动 SimpleXNG，在 Runtime 提供
Windows `uvloop`、Multiprocessing Compatibility，并确保启用 JSON Search
Output。这个 Launcher 解决的是安装完成后的 Runtime Compatibility，无法修复
发生在更早阶段的 Package-resolution Failure。

如果特定 SimpleXNG Release 仍无法启动，可在加密配置中把 `SEARXNG_URL`
指向外部 SearXNG，并设置 `SEARXNG_AUTO_START=0`。调整 Dependency 前先保存
Child Process Error；手工改 Vendored Package 不属于当前支持的安装流程。

## 验证安装

```bash
uv run python -m cyrene
```

打开 `http://localhost:4242`。首次启动应显示 Onboarding。

`cyrene_tools` 自管理工具包只在 Electron Host 与 Workbench Renderer 都已连接时
可用。仅浏览器 Web UI 或 CLI 仍可使用普通 Agent Tool，但不能借用未向该
Runtime 注册的 Electron UI Surface。

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
