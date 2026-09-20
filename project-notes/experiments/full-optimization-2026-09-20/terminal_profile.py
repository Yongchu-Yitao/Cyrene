"""Profile actual terminal worker; separate profiled timings from normal samples."""
import asyncio,cProfile,io,json,logging,os,pstats,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
async def main(root):
    from cyrene.observability.feature_performance_benchmark import _benchmark_terminal,DEFAULT_CONFIG
    from cyrene.plugins.builtin.cyrene_code.terminal.manager import _TerminalPersistenceWriter,TerminalManager
    logging.disable(logging.CRITICAL)
    metrics=[];close=TerminalManager.close_store
    def close_capture(self):
        if self._persistence_writer:metrics.append(self._persistence_writer.metrics())
        return close(self)
    TerminalManager.close_store=close_capture
    results=[]
    for i in range(3):
        folder=root/str(i);folder.mkdir();result=await _benchmark_terminal(DEFAULT_CONFIG,folder);assert result['quality']['preserved'];results.append(result)
    (HERE/'terminal-results.json').write_text(json.dumps({'unprofiled':results,'worker_metrics':metrics},indent=2)+'\n')
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='cyrene-terminal-audit-') as d:
        os.environ['CYRENE_BASE_DIR']=d;asyncio.run(main(Path(d)))
