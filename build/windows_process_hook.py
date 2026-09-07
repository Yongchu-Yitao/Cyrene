"""Run before PyInstaller dependency hooks and the frozen application imports."""

from cyrene.platform.windows_process import hide_background_console_windows

hide_background_console_windows()
