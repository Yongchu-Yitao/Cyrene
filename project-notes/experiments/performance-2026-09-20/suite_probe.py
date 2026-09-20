"""Bounded, isolated suite runner with shutdown diagnostics (no real model calls)."""
import asyncio
import faulthandler
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


async def main():
    from cyrene.observability.performance_suite import write_report
    group = sys.argv[1] if len(sys.argv) > 1 else "chat"
    result = await write_report(ROOT / (group + "-isolated"), groups=(group,), repeats=3)
    print("report:", result[0], "quality:", result[2]["quality"], flush=True)

    def pending():
        tasks = [{"name": t.get_name(), "coroutine": str(t.get_coro()),
                  "cancelling": t.cancelling(),
                  "stack": [f"{f.f_code.co_filename}:{f.f_lineno}:{f.f_code.co_name}" for f in t.get_stack()]}
                 for t in asyncio.all_tasks() if not t.done()]
        (ROOT / (group + "-shutdown.json")).write_text(json.dumps(tasks, indent=2) + "\n")
        print("pending shutdown tasks:", json.dumps(tasks), flush=True)
    asyncio.get_running_loop().call_later(2, pending)


with tempfile.TemporaryDirectory(prefix="cyrene-performance-isolated-") as directory:
    os.environ["CYRENE_BASE_DIR"] = directory
    # Dump stacks and exit nonzero if benchmark or shutdown does not finish.
    faulthandler.dump_traceback_later(45, exit=True)
    asyncio.run(main())
    faulthandler.cancel_dump_traceback_later()
