# Windows 更新失败与命令行窗口排查

日期：2026-09-07。基于当前工作区源码与本地 electron-builder NSIS 模板检查；未取得用户 Windows 故障日志，未在 Windows 实机复现。

## 已确认的代码路径

1. 原更新脚本只等待 3 秒。Electron 接受更新操作后延迟 1 秒调用 `app.quit()`，而 `before-quit` 可能等待扩展任务检查和用户确认。因此安装器可能在应用尚未退出时运行，便携版也可能在文件仍被占用时替换失败。
2. 冻结版终端守护进程通过 `sys.executable --launch-terminal-daemon` 启动，与后端一样名为 `Cyrene.exe`。它被设计为跨普通应用退出存活。NSIS 的 `allowOnlyOneInstallerInstance.nsh` 按应用可执行文件名查找进程，并在重试关闭失败后要求用户手动关闭应用。因此关闭主窗口不代表安装器认定的所有 Cyrene 进程都已结束。这是与用户反馈一致的明确故障路径，但不能仅凭源码认定每次报错都由它引起。
3. 旧的 Python 无窗口补丁在 `_run_electron_mode()` 内才安装，覆盖不到此前依赖导入阶段的子进程。冻结入口还会加载大量第三方依赖。启动黑窗的具体进程尚未实机确认。
4. 更新器使用 `DETACHED_PROCESS`，而全局补丁又叠加 `CREATE_NO_WINDOW`；Windows 明确规定两者一起使用时后者被忽略。此组合本身不证明必然弹窗，但不能依靠它保证无窗口。[Microsoft 进程创建标志](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)

## 本次修改

- Electron 将自身 PID 传给后端。更新脚本等待后端和 Electron 实际退出，最多 120 秒；超时记录失败并停止安装。
- 主程序退出后，通过用户终端守护进程连接记录中的 token 和协议版本发送认证 shutdown 请求，等待其进程退出，避免前端请求将它重新拉起。只在更新流程执行；普通退出仍保留终端。更新会结束终端内运行的命令。
- 便携版文件替换最多重试 60 秒，覆盖包装进程清理和短暂文件锁；失败保留下载包。
- PowerShell 显式使用 `CREATE_NO_WINDOW`、隐藏窗口参数和独立标准流，工作目录设为下载目录。
- 冻结程序使用 PyInstaller runtime hook 提前安装无窗口补丁；源码 Electron 模式也提前到应用依赖导入之前。补丁重复调用不会重复包装。

## 验证及剩余边界

执行 `uv run pytest tests/test_updater_platform.py tests/test_updater_auto_download.py tests/test_update_application_service.py tests/test_windows_process.py tests/test_windows_portable_packaging.py -q`：55 passed，2 skipped。两个跳过项是 Windows PowerShell 执行测试，覆盖中文/空格/单引号路径、便携包锁定重试和安装器错误码传播。当前主机为 macOS，尚未验证真实 Windows 安装、UAC、终端关闭和黑窗行为。

安装版仍使用 `-Verb RunAs`；用户取消 UAC、提权使用其他账户、其他安装实例或其他捆绑进程占用等问题仍需结合 `%TEMP%\cyrene_update.log` 排查。当前“自动更新”实际上自动下载并校验，安装需要用户选择“重启更新”。后续补充：更新安装脚本将失败阶段、错误信息、退出码和日志路径保存到下载目录的 `last-install.json`；下一次启动的后台更新检查会先向通知中心报告失败，再进行联网检查，同一记录只通知一次。检查更新及下载错误现在保留网络超时、HTTP 状态码和文件操作错误类别。脚本无法启动、被强行终止或报告文件无法写入的情况，仍可能无法形成失败记录。

本次仅修改源码，没有构建或发布 Windows 安装包。旧版本执行更新时仍使用旧版本自带的更新逻辑；本次修复需随新构建交付，旧版若无法更新，需要手动安装一次新构建。
