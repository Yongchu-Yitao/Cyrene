"""Early CLI entry point; offline inspection never boots the host."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cyrene doctor", description="Diagnose Cyrene without loading editable Plugins.")
    parser.add_argument("--offline", action="store_true", help="Inspect local files without starting the daemon (default)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--project", default="")
    parser.add_argument("--chat", default="")
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument("--plugin-dir", type=Path)
    parser.add_argument("--lang", choices=("zh", "en"), default="zh")
    args = parser.parse_args(argv)
    from cyrene.platform.paths import resolve_app_paths
    from .service import DoctorService
    paths = resolve_app_paths()
    base = args.base_dir or paths.runtime_base
    plugins = args.plugin_dir or Path(os.environ.get("CYRENE_PLUGIN_IMPL_DIR") or paths.user_data / "plugin_impl")
    service = DoctorService(data=base / "data", database=base / "store" / "cyrene.runtime.database", plugins=plugins)
    report = asyncio.run(service.diagnose({"project_id": args.project, "chat_id": args.chat}, language=args.lang, persist=False))
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for item in report["findings"]:
            print(f'[{item["status"]}] {item["summary"][args.lang]}')
            if item["status"] in {"failed", "unknown"}:
                print("  " + item["direction"][args.lang])
                if item["evidence"]:
                    print("  " + json.dumps(item["evidence"], ensure_ascii=False))
    return 1 if any(item["status"] == "failed" for item in report["findings"]) else 0
