"""Launch the Linux FreeRDP development bridge inside an isolated X server.

The first JSON request is transferred through an anonymous inherited pipe so
one-time RDP credentials never enter argv, environment variables, or a file.
All later line-delimited requests continue over the provider's original stdin.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


def _fail(code: str, error: str) -> None:
    sys.stdout.write(json.dumps({"ok": False, "code": code, "error": error}) + "\n")
    sys.stdout.flush()
    raise SystemExit(1)


def _free_display() -> int:
    for number in range(90, 150):
        path = f"/tmp/.X11-unix/X{number}"
        if os.path.exists(path):
            continue
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(path)
        except OSError:
            return number
        finally:
            probe.close()
    raise RuntimeError("no_free_x11_display")


def main() -> None:
    raw = sys.stdin.buffer.readline(2_500_000)
    if not raw:
        _fail("freerdp_sidecar_invalid_request", "The connect request was not received.")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("freerdp_sidecar_invalid_request", "The connect request is not valid JSON.")
    if not isinstance(request, dict) or str(request.get("method") or "") != "connect":
        _fail("freerdp_sidecar_invalid_request", "The first sidecar request must be connect.")

    electron = str(os.environ.get("CYRENE_ELECTRON_PATH") or "").strip()
    resources = str(os.environ.get("CYRENE_ELECTRON_RESOURCES_DIR") or "").strip()
    sidecar = os.path.join(resources, "remote-desktop-rdp-sidecar.js")
    xvfb = shutil.which("Xvfb")
    if not electron or not os.path.isfile(electron) or not os.path.isfile(sidecar) or not xvfb:
        _fail("freerdp_sidecar_missing", "The Electron FreeRDP development bridge is incomplete.")

    display_number = _free_display()
    display = f":{display_number}"
    xvfb_process = subprocess.Popen(
        [
            xvfb,
            display,
            "-screen",
            "0",
            "3840x2160x24",
            "-nolisten",
            "tcp",
            "-noreset",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    socket_path = f"/tmp/.X11-unix/X{display_number}"
    for _ in range(100):
        if xvfb_process.poll() is not None:
            _fail("freerdp_xvfb_failed", "The isolated X11 display could not start.")
        if os.path.exists(socket_path):
            break
        time.sleep(0.03)
    else:
        xvfb_process.terminate()
        _fail("freerdp_xvfb_timeout", "The isolated X11 display did not become ready.")

    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    try:
        os.write(write_fd, raw)
    finally:
        os.close(write_fd)

    child_env = dict(os.environ)
    child_env.pop("ELECTRON_RUN_AS_NODE", None)
    config_dir = tempfile.mkdtemp(prefix="cyrene-rdp-config-")
    child_env.update(
        {
            "DISPLAY": display,
            # The user-session and system Remote Login services may present
            # different loopback certificates. Never reuse their TOFU state.
            "XDG_CONFIG_HOME": config_dir,
            "CYRENE_RDP_BOOTSTRAP_FD": str(read_fd),
            "CYRENE_RDP_CONFIG_DIR": config_dir,
            "CYRENE_RDP_XVFB_PID": str(xvfb_process.pid),
        }
    )
    # The npm Electron runtime does not ship a root-owned setuid sandbox. The
    # main Linux development app already runs with --no-sandbox for this
    # reason; the isolated RDP media process must use the same launch contract
    # or Chromium aborts before JavaScript can return a diagnostic response.
    argv = [
        electron,
        sidecar,
        "--stdio-json",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
    try:
        os.execvpe(electron, argv, child_env)
    except OSError as exc:
        shutil.rmtree(config_dir, ignore_errors=True)
        try:
            os.killpg(xvfb_process.pid, signal.SIGTERM)
        except OSError:
            pass
        _fail("freerdp_sidecar_start_failed", str(exc))


if __name__ == "__main__":
    main()
