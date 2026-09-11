"""Actual shared AgentSession with deterministic model transport.

Exercises Write -> Read -> final answer -> close/reopen without network. This
isolates orchestration/persistence from provider latency, not model quality.
Only a fresh temporary workspace is used. Full application plugins are absent.
"""
# Import timing intentionally starts before the remaining imports.
# ruff: noqa: E402
import time
START = time.perf_counter()
import asyncio
import json
from pathlib import Path
import platform
import tempfile

from cyrene.core import AgentSession
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry

IMPORTED = time.perf_counter()


def main():
    calls = []
    async def model(arguments, _context):
        calls.append(arguments)
        if len(calls) == 1:
            return {'content': '', 'tool_calls': [{'id': 'write-proof', 'name': 'Write',
                'arguments': {'path': 'proof.txt', 'content': 'research-proof'}}]}
        if len(calls) == 2:
            return {'content': '', 'tool_calls': [{'id': 'read-proof', 'name': 'Read',
                'arguments': {'path': 'proof.txt'}}]}
        return {'content': 'research-complete', 'tool_calls': []}
    with tempfile.TemporaryDirectory(prefix='cyrene-core-research-') as directory:
        root = Path(directory)
        plugins = root / 'plugins'
        plugins.mkdir()
        start = time.perf_counter()
        registry = PluginRegistry()
        registry.register_pack(PluginPack('model', 'research model', (
            Plugin('MiniMax', 'deterministic research transport', {'type': 'object'}, model, kind='model'),
        )), source='research')
        def session():
            return AgentSession(root / 'data', root / 'workspace', plugins,
                                registry=registry, load_plugins=False, max_model_calls=8,
                                extra_direct_tool_names=('Write', 'Read'))
        agent = session()
        ready = time.perf_counter()
        agent.submit('Write and read proof.txt', run_id='research-run')
        asyncio.run(agent.drain())
        completed = time.perf_counter()
        output = agent.final_output('research-run')
        assert output and output.get('content') == 'research-complete', output
        assert (root / 'workspace' / 'proof.txt').read_text() == 'research-proof'
        assert len(calls) == 3, len(calls)
        # Confirm the real Read tool result reached the next model invocation.
        assert 'research-proof' in json.dumps(calls[-1]['messages'])
        agent.close()
        restored = session()
        assert restored.final_output('research-run')['content'] == 'research-complete'
        restored.close()
        print(json.dumps({'platform': platform.platform(), 'core_import_ms': (IMPORTED - START) * 1000,
            'registry_and_session_ms': (ready - start) * 1000,
            'three_model_turns_two_tools_ms': (completed - ready) * 1000,
            'restore_and_close_ms': (time.perf_counter() - completed) * 1000,
            'total_ms': (time.perf_counter() - START) * 1000,
            'model_calls': len(calls), 'file_verified': True, 'restored_final_verified': True,
            'scope': 'shared microkernel + real file tools; deterministic model; no full application plugins'}), flush=True)


if __name__ == '__main__':
    main()
