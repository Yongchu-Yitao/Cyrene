"""Sequential benchmarks, independent process deadlines; preserve all failures."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
def sources():
    return {str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (REPO/'src/cyrene').rglob('*') if p.suffix in ('.py','.jsx','.mjs')}

report={"source_before":sources(),"runs":[]}
for name in ('chat','search','event_bus','knowledge_search','knowledge_write','database_init','scheduled_tasks','file_hashing','terminal'):
    print('START',name,flush=True)
    start=time.monotonic()
    with (HERE/(name+'.log')).open('w') as log:
        process=subprocess.Popen([sys.executable,str(HERE/'suite_worker.py'),name],cwd=REPO,
                                 stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            result=process.wait(timeout=100)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait()
            result='timeout'
    record={'scenario':name,'exit_code':result,'wall_s':round(time.monotonic()-start,2)}
    report['runs'].append(record)
    (HERE/'suite-status.json').write_text(json.dumps(report,indent=2)+'\n')
    print(record,flush=True)
report['source_after']=sources()
report['changed_sources']=[p for p,h in report['source_before'].items() if report['source_after'].get(p)!=h]
(HERE/'suite-status.json').write_text(json.dumps(report,indent=2)+'\n')
