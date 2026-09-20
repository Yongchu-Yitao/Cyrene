import json,sqlite3,tempfile,time,statistics
from pathlib import Path
from equivalence import baseline,HERE
from cyrene.core.context.schema import ensure_tree_schema
old=baseline('src/cyrene/core/context/schema.py','cyrene.core.context')
def measure(fn):
    samples=[]
    for i in range(15):
        start=time.perf_counter();fn();samples.append((time.perf_counter()-start)*1000)
    return {'median_ms':statistics.median(samples),'samples_ms':samples}
with tempfile.TemporaryDirectory() as d:
    c=sqlite3.connect(Path(d)/'db');c.row_factory=sqlite3.Row;old.ensure_tree_schema(c)
    c.execute("INSERT INTO context_nodes(node_id,parent_id,value_json,self_token_count,path_token_count,created_at,updated_at) VALUES ('root',NULL,'{}',1,1,'2026','2026')")
    c.executemany("INSERT INTO context_nodes(node_id,parent_id,value_json,self_token_count,path_token_count,created_at,updated_at) VALUES (?,'root',?,1,2,?,?)",[(str(i),json.dumps({'content':'x'*512}),'2026','2026') for i in range(100000)]);c.commit()
    query='SELECT 1 FROM context_nodes WHERE self_token_count IS NULL OR path_token_count IS NULL LIMIT 1'
    report={'baseline':measure(lambda:(old.ensure_tree_schema(c),c.execute(query).fetchone())),'baseline_plan':[tuple(r) for r in c.execute('explain query plan '+query)]}
    ensure_tree_schema(c)
    report.update(candidate=measure(lambda:(ensure_tree_schema(c),c.execute(query).fetchone())),candidate_plan=[tuple(r) for r in c.execute('explain query plan '+query)]);c.close()
    (HERE/'schema-bench.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
