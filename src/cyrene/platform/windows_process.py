"""Install the desktop's no-console subprocess default before dependency imports."""

import subprocess
import sys


def hide_background_console_windows() -> None:
    if sys.platform != "win32":
        return
    original = subprocess.Popen.__init__
    if getattr(original, "_cyrene_hidden", False):
        return

    def hidden_init(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        # Windows ignores CREATE_NO_WINDOW with these explicit console modes.
        if not flags & (0x00000008 | 0x00000010):
            kwargs["creationflags"] = flags | 0x08000000
        if kwargs.get("startupinfo") is None:
            info = subprocess.STARTUPINFO()
            info.dwFlags = subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 0
            kwargs["startupinfo"] = info
        original(self, *args, **kwargs)

    hidden_init._cyrene_hidden = True
    subprocess.Popen.__init__ = hidden_init


def terminate_managed_process(process: subprocess.Popen) -> None:
    """Terminate a managed launch, including Windows one-file loader children."""
    if sys.platform != "win32":
        process.terminate()
        return
    result = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=0x08000000, timeout=10,
    )
    if result.returncode and process.poll() is None:
        raise RuntimeError(f"Could not stop managed process tree {process.pid}: {result.returncode}")
