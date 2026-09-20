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
    original=_TerminalPersistenceWriter._run_main
    def profiled(self):
        profiler=cProfile.Profile();profiler.enable()
        try:return original(self)
        finally:
            profiler.disable();profiler.dump_stats(str(HERE/'terminal-worker.prof'))
            output=io.StringIO();pstats.Stats(profiler,stream=output).strip_dirs().sort_stats('tottime').print_stats(45);(HERE/'terminal-worker-profile.txt').write_text(output.getvalue())
    _TerminalPersistenceWriter._run_main=profiled
    folder=root/'profiled';folder.mkdir();diagnostic=await _benchmark_terminal(DEFAULT_CONFIG,folder);assert diagnostic['quality']['preserved']
    (HERE/'terminal-results.json').write_text(json.dumps({'unprofiled':results,'profiled_diagnostic_only':diagnostic,'worker_metrics':metrics},indent=2)+'\n')
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='cyrene-terminal-audit-') as d:
        os.environ['CYRENE_BASE_DIR']=d;asyncio.run(main(Path(d)))
