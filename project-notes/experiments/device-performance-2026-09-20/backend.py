"""Isolated production-code probes; no network or product modifications."""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import statistics
import tempfile
import time


def measure(fn, rounds=7):
    fn()
    samples = []
    for _ in range(rounds):
        wall, cpu = time.perf_counter(), time.process_time()
        fn()
        samples.append({"wall_ms": (time.perf_counter()-wall)*1000,
                        "cpu_ms": (time.process_time()-cpu)*1000})
    return {"wall_ms": statistics.median(x["wall_ms"] for x in samples),
            "cpu_ms": statistics.median(x["cpu_ms"] for x in samples), "samples": samples}


def main(root):
    from cyrene.core.context import ContextTreeStore
    from cyrene.core.context.projection import project_model_messages
    from cyrene.core.context.compaction import messages_token_estimate
    from cyrene.core.session import AgentSession
    from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
    from cyrene.workbench.chat.run_timeline import RunTimeline

    logging.disable(logging.CRITICAL)
    results = {}

    def calibration():
        value = 0
        for i in range(2_000_000):
            value = (value + i) & 0xFFFFFFFF
        return value
    results["calibration"] = measure(calibration, 3)

    for count in (100, 1000):
        store = ContextTreeStore.create(root/f"context-{count}.sqlite3", tree_id="probe", root_id="root",
                                       root_value={"role": "system", "content": "Fixed benchmark"})
        leaf = "root"
        for i in range(count):
            leaf = store.mount(leaf, {"role": "user" if i % 2 == 0 else "assistant",
                                    "content": "x"*2048}, node_id=f"n{i}").id
        expected_tokens = messages_token_estimate(project_model_messages(store.get_path(leaf)))
        def prepare():
            messages = project_model_messages(store.get_path(leaf))
            assert len(messages) == count+1
            assert messages_token_estimate(messages) == expected_tokens
        results[f"context_read_project_estimate_{count}"] = measure(prepare)
        store.close()

    for count in (100, 1000, 5000):
        timeline = RunTimeline("probe")
        for i in range(count):
            record = timeline._new("message", "2026-09-20T00:00:00+00:00", str(i))
            record.update(content="x"*2048, status="completed")
        timeline.reply_id = record["id"]
        seq = 0
        def complete():
            nonlocal seq
            seq += 1
            content = f"final {seq}"
            timeline.apply({"type": "message.completed", "messageId": str(count-1),
                            "text": content, "timestamp": "2026-09-20T00:00:01+00:00"})
            assert timeline.records[timeline.reply_id]["content"] == content
        results[f"timeline_complete_{count}"] = measure(complete, 15)

    class TimedSession(AgentSession):
        def _prepare_model_input(self, trigger):
            wall = time.perf_counter()
            result = super()._prepare_model_input(trigger)
            self.prepares.append((time.perf_counter()-wall)*1000)
            return result

    async def agent_case(delay):
        directory = root/f"agent-{delay}"
        directory.mkdir()
        registry = PluginRegistry()
        calls = 0
        tool_numbers = []
        streamed = []
        events = []
        async def model(arguments, context):
            nonlocal calls
            calls += 1
            if delay:
                await asyncio.sleep(delay)
            if calls <= 10:
                return {"content": "", "tool_calls": [{"id": f"call-{calls}", "name": "step",
                        "arguments": {"number": calls}}]}
            sink = context.services["model_stream"]
            await sink({"type": "reply_start"})
            for chunk in ("fixed ", "answer"):
                await sink({"type": "reply_delta", "delta": chunk})
                streamed.append(chunk)
            await sink({"type": "reply_done", "response": "fixed answer"})
            return {"content": "fixed answer", "tool_calls": []}
        async def step(arguments, context):
            tool_numbers.append(arguments["number"])
            return {"number": arguments["number"], "content": "x"*8192}
        registry.register_pack(PluginPack("probe", "probe", (
            Plugin("MiniMax", "fixed model", {"type": "object"}, model, kind="model"),
            Plugin("step", "fixed tool", {"type": "object", "properties": {"number": {"type": "integer"}},
                   "required": ["number"]}, step, metadata={"permission_review": False}),
        )), source="experiment")
        start = time.perf_counter()
        session = TimedSession(directory/"data", directory/"workspace", directory/"plugins", registry=registry,
                               load_plugins=False, inherit_application_scope=False,
                               event_listener=lambda event: events.append(event.type))
        setup_ms = (time.perf_counter()-start)*1000
        session.prepares = []
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            session.submit("Execute ten fixed steps.", run_id="probe-run")
            await asyncio.wait_for(session.drain(), 45)
            elapsed = (time.perf_counter()-wall)*1000
            cpu_ms = (time.process_time()-cpu)*1000
            output = session.final_output("probe-run")
            assert output["content"] == "fixed answer", output
            assert calls == 11 and tool_numbers == list(range(1, 11))
            assert "".join(streamed) == "fixed answer"
            assert events.count("assistant.stream.delta") == 2
            assert session.is_idle
            from collections import Counter
            return {"wall_ms": elapsed, "cpu_ms": cpu_ms, "setup_ms": setup_ms,
                    "prepare_total_ms": sum(session.prepares), "prepare_calls": len(session.prepares),
                    "scheduled_model_wait_ms": 11*delay*1000,
                    "model_calls": calls, "tool_calls": len(tool_numbers),
                    "answer_sha256": hashlib.sha256(output["content"].encode()).hexdigest(),
                    "event_counts": dict(Counter(events)), "checks_passed": True}
        finally:
            session.close()
    # Warm import/setup paths with an unmeasured independent session.
    asyncio.run(agent_case(0.001))
    for delay in (0.0, 0.1):
        results[f"agent_10_tools_wait_{int(delay*1000)}ms"] = asyncio.run(agent_case(delay))
    return results


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="cyrene-device-probe-") as directory:
        os.environ["CYRENE_BASE_DIR"] = directory
        print(json.dumps(main(Path(directory))))
