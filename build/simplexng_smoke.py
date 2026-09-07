"""Exercise the real SimpleXNG child and JSON API using its offline demo engine."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import yaml


def run_smoke(executable: Path | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="cyrene-search-smoke-") as directory:
        root = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        settings = {
            "use_default_settings": {"engines": {"keep_only": []}},
            "server": {"secret_key": secrets.token_hex(16), "limiter": False},
            "search": {"formats": ["html", "json"]},
            "engines": [{
                "name": "cyrene smoke", "engine": "demo_offline",
                "shortcut": "smoke", "disabled": False,
            }],
        }
        settings_path = root / "settings.yml"
        settings_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
        command = (
            [str(executable.resolve()), "--launch-simplexng"]
            if executable else [sys.executable, "-m", "cyrene.simplexng_child"]
        )
        command += ["--settings", str(settings_path), "-p", str(port), "-H", "127.0.0.1"]
        env = dict(os.environ, SEARXNG_SETTINGS_PATH=str(settings_path),
                   CYRENE_SIMPLEXNG_PARENT_PID=str(os.getpid()), XDG_CACHE_HOME=str(root / "cache"))
        opener = build_opener(ProxyHandler({}))
        log_path = root / "child.log"
        env["CYRENE_SIMPLEXNG_LOG_PATH"] = str(log_path)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"SimpleXNG exited: {process.returncode}")
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("SimpleXNG startup timed out")
                    time.sleep(0.2)
                # Cross a watchdog tick before testing search. A package import
                # check cannot catch the Windows parent-termination regression.
                time.sleep(1.2)
                with opener.open(
                    f"http://127.0.0.1:{port}/search?q=cyrene&format=json", timeout=10
                ) as response:
                    payload = json.load(response)
                if payload.get("query") != "cyrene" or payload.get("number_of_results", 0) <= 0:
                    raise RuntimeError(f"Offline JSON search returned no results: {payload!r}")
                with opener.open(
                    f"http://127.0.0.1:{port}/search?q=2%2B2&format=json", timeout=20
                ) as response:
                    arithmetic = json.load(response)
                if not any("2+2 = 4" in str(answer) for answer in arithmetic.get("answers", [])):
                    raise RuntimeError(f"Calculator answer missing: {arithmetic.get('answers')!r}")
                if process.poll() is not None:
                    raise RuntimeError("SimpleXNG exited during search")
                print("CYRENE_SIMPLEXNG_SMOKE=ok", flush=True)
            except Exception:
                print(log_path.read_text(encoding="utf-8", errors="replace")[-12000:], file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, help="Frozen Cyrene executable; omit for source")
    run_smoke(parser.parse_args().executable)
