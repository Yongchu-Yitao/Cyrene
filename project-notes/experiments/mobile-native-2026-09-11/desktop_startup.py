"""Fresh-process backend readiness; no model calls or existing user data.

Run with the project Python. HTTP health includes the real application lifespan.
The first run seeds an empty home; subsequent runs reuse only this experiment's
home. OS file caches are NOT flushed, and this is not Electron/UI startup.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=4)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    for key, name in {
        "CYRENE_BASE_DIR": "home", "CYRENE_USER_DATA_DIR": "user",
        "CYRENE_CACHE_DIR": "cache", "CYRENE_TEMP_DIR": "temp",
    }.items():
        env[key] = str(root / name)
    env["CYRENE_INSTALL_RESOURCES_DIR"] = str(repo)
    env["PYTHONPATH"] = str(repo / "src")
    env["PYTHONUNBUFFERED"] = "1"
    token = secrets.token_urlsafe(48)
    env["CYRENE_AUTH_TOKEN"] = token
    report = {"platform": platform.platform(), "python": sys.version,
              "measurement": "fresh process to authenticated HTTP health; no UI or model inference",
              "runs": []}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for index in range(args.runs):
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        row = {"index": index, "data": "empty" if index == 0 else "reused"}
        started = time.perf_counter()
        with (root / f"run-{index}.log").open("w") as log:
            proc = subprocess.Popen([sys.executable, "-m", "cyrene", "--port", str(port)],
                                    cwd=root, env=env, stdout=log, stderr=log,
                                    start_new_session=True)
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health",
                                                  headers={"X-Cyrene-Token": token})
                while time.perf_counter() - started < 180:
                    if proc.poll() is not None:
                        row["error"] = f"exit {proc.returncode}"
                        break
                    try:
                        with opener.open(request, timeout=0.4) as response:
                            body = json.load(response)
                            if response.status == 200 and body.get("status") == "ok":
                                row["health_seconds"] = time.perf_counter() - started
                                break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.05)
                else:
                    row["error"] = "health timeout"
                row["elapsed_seconds"] = time.perf_counter() - started
            finally:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                        row["forced_shutdown"] = True
        report["runs"].append(row)
        (root / "results.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(row), flush=True)
        if "error" in row:
            break


if __name__ == "__main__":
    main()
