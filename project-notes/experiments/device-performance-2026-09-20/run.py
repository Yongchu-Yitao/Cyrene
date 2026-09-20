"""Single-core vs same-core CPU contention. AB/BA ordering, three independent pairs."""
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CPU = max(os.sched_getaffinity(0))


def run_one(label, repeat):
    hogs=[]
    try:
        if label == "contended":
            for _ in range(3):
                hogs.append(subprocess.Popen([sys.executable, "-c",
                    f"import os; os.sched_setaffinity(0, {{{CPU}}}); print('ready', flush=True)\nwhile True: pass"],
                    stdout=subprocess.PIPE, text=True))
            for process in hogs:
                assert process.stdout.readline().strip() == "ready"
        outputs={}
        for name, command in (("backend", [sys.executable, str(HERE/"backend.py")]),
                              ("frontend", ["node", str(HERE/"frontend.mjs")])):
            # Affinity applies to the worker and its subsequently created threads.
            proc=subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=120,
                                preexec_fn=lambda: os.sched_setaffinity(0, {CPU}))
            (HERE/f"{repeat}-{label}-{name}.stderr.log").write_text(proc.stderr)
            if proc.returncode:
                raise RuntimeError(f"{name} failed: {proc.returncode}: {proc.stderr[-4000:]}")
            outputs.update(json.loads(proc.stdout))
        return {"mode":label,"repeat":repeat,"results":outputs}
    finally:
        for process in hogs:
            process.terminate()
        for process in hogs:
            process.wait(timeout=5)


if __name__ == "__main__":
    tracked = ["src/cyrene/core/session.py", "src/cyrene/core/context/store.py",
               "src/cyrene/core/context/projection.py", "src/cyrene/core/context/compaction.py",
               "src/cyrene/core/observability.py", "src/cyrene/workbench/chat/run_timeline.py",
               "src/cyrene/workbench/webui/frontend/features/chat/runtime-timeline.jsx"]
    hashes=lambda: {p:hashlib.sha256((REPO/p).read_bytes()).hexdigest() for p in tracked}
    report={"platform":platform.platform(),"python":platform.python_version(),"cpu":CPU,
            "node":subprocess.check_output(["node","--version"],text=True).strip(),
            "design":"one pinned worker; baseline zero vs three same-core busy Python processes; AB/BA; no network",
            "source_before":hashes(),"trials":[]}
    for repeat in range(3):
        order=("baseline","contended") if repeat % 2 == 0 else ("contended","baseline")
        for label in order:
            print(f"Running pair {repeat+1}/3: {label}",flush=True)
            trial=run_one(label,repeat)
            report["trials"].append(trial)
            (HERE/"results.json").write_text(json.dumps(report,indent=2)+"\n")
            print(json.dumps({k:round(v["wall_ms"],2) for k,v in trial["results"].items()}),flush=True)
    report["source_after"]=hashes()
    report["source_unchanged"]=report["source_before"]==report["source_after"]
    (HERE/"results.json").write_text(json.dumps(report,indent=2)+"\n")
