"""Measure avoidable formatting when the standard logger rejects a level."""
import json,logging,os,statistics,tempfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='cyrene-log-audit-') as tmp:
 os.environ['CYRENE_BASE_DIR']=tmp
 from cyrene.core.observability import log_operation,_default_log_level
 logger=logging.getLogger('audit-disabled');logger.propagate=False;logger.setLevel(logging.INFO)
 class Capture(logging.Handler):
  def __init__(self):super().__init__();self.rows=[]
  def emit(self,r):self.rows.append((r.levelno,r.getMessage()))
 handler=Capture();logger.addHandler(handler)
 def guarded(logger,component,action,*,level=None,exc_info=None,**fields):
  effective=_default_log_level(fields) if level is None else level
  if not logger.isEnabledFor(effective):return
  return log_operation(logger,component,action,level=effective,exc_info=exc_info,**fields)
 samples={k:[] for k in ['current','guarded']}
 for repeat in range(5):
  for name,fn in ([('current',log_operation),('guarded',guarded)] if repeat%2==0 else [('guarded',guarded),('current',log_operation)]):
   start=time.perf_counter()
   for i in range(10000):fn(logger,'context.store','get_node',phase='completed',tree_id='tree',node_id=str(i),count=12)
   samples[name].append((time.perf_counter()-start)*1000)
 assert not handler.rows
 # Enabled messages and warning/error outcomes must remain byte-identical.
 outputs=[]
 for fn in [log_operation,guarded]:
  handler.rows=[]
  for minimum in [logging.DEBUG,logging.INFO,logging.ERROR]:
   logger.setLevel(minimum)
   for phase in ['started','completed','blocked','cancelled','failed']:
    fn(logger,'probe','event',phase=phase,node_id='n',password='hidden',value={'x':1})
   for level in [logging.DEBUG,logging.WARNING,logging.ERROR]:fn(logger,'probe','event',level=level,phase='completed')
  outputs.append(handler.rows[:])
 assert outputs[0]==outputs[1]
 result={'calls_per_sample':10000,'disabled_debug_samples_ms':samples,'medians_ms':{k:statistics.median(v) for k,v in samples.items()},'enabled_output_equal':True,'enabled_records_per_variant':len(outputs[0]),'scope':'standard logging.Logger and ordinary structured fields only; experimental wrapper, not production'}
 (HERE/'logging-results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
