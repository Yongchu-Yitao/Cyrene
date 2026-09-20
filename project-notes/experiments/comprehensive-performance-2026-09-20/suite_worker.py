"""Run one existing performance scenario in an isolated process."""
import asyncio
import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent

async def main(root, name):
    if name in ("chat", "search"):
        from cyrene.observability.performance_suite import write_report
        _, _, report = await write_report(HERE/name, groups=(name,), repeats=3)
    else:
        from cyrene.observability import feature_performance_benchmark as feature
        scenario = next(s for s in feature.SCENARIOS if s.__name__ == "_benchmark_"+name)
        samples = []
        for repeat in range(3):
            print(f"{name} repeat {repeat+1}", flush=True)
            samples.append(await feature._run_parallel_rounds(scenario, feature.DEFAULT_CONFIG, root/str(repeat)))
        report = {"scenario": name, "samples": samples,
                  "quality": {"preserved": all(s["quality"]["preserved"] for s in samples)}}
    (HERE/(name+".json")).write_text(json.dumps(report, indent=2)+"\n")
    print("quality", report["quality"], flush=True)

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="cyrene-comprehensive-") as temporary:
        os.environ["CYRENE_BASE_DIR"] = temporary
        faulthandler.dump_traceback_later(80, repeat=False)
        asyncio.run(main(Path(temporary), sys.argv[1]))
        faulthandler.cancel_dump_traceback_later()
