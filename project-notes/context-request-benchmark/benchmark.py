"""Measure local AgentSession preparation against the same synthetic chat tree.

Run seed once and then measure each implementation in its own Python process.
Select the implementation with PYTHONPATH. The fixture stores synthetic text only.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
from cyrene.core.session import AgentSession


def registry(model):
    async def probe(arguments, context):
        return {"text": "checked"}

    plugins = (
        Plugin("MiniMax", "model", {"type": "object"}, model, kind="model"),
        Plugin(
            "Probe",
            "Read one fixture item",
            {"type": "object", "properties": {}},
            probe,
            metadata={"read_only": True},
        ),
    )
    result = PluginRegistry()
    result.register_pack(PluginPack("fixture", "fixture", plugins), source="fixture")
    return result


def seed(directory):
    fixture = directory / "fixture"
    fixture.mkdir(parents=True, exist_ok=False)
    plugins = directory / "plugins"
    plugins.mkdir(exist_ok=True)

    async def unused_model(arguments, context):
        raise AssertionError("Fixture setup must not call a model")

    session = AgentSession(fixture, directory, plugins, registry=registry(unused_model))
    database = session.store.tree_database_path(session.tree.id)
    session.close()
    connection = sqlite3.connect(database)
    try:
        populate(connection)
    finally:
        connection.close()


def populate(db):
    turns, tool_results, contexts = 2200, 5360, 200

    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    root = json.loads(
        db.execute(
            "SELECT value_json FROM context_nodes WHERE node_id='root'"
        ).fetchone()[0]
    )
    state = root["_task_contexts"]
    rows = []
    parent = "root"

    def append(value, owner):
        nonlocal parent
        node_id = f"{len(rows) + 1:05x}"
        rows.append(
            (node_id, parent, encode(value), 1, len(rows) + 2, "20000101", "20000101")
        )
        state["documents"][owner]["body"].append(
            {"id": node_id, "parent_id": parent, "value": value}
        )
        parent = node_id
        return node_id

    for turn in range(turns):
        owner = "active" if turn == turns - 1 else f"ctx{turn % (contexts - 1)}"
        document = state["documents"].setdefault(
            owner,
            {
                "title": owner,
                "summary": "",
                "archived": owner != "active",
                "covered": [],
                "body": [],
                "user_nodes": [],
            },
        )
        run_id = f"r{turn}"
        user_id = append(
            {"role": "user", "content": f"Question {turn}", "run_id": run_id}, owner
        )
        document["user_nodes"].append(user_id)
        for i in range(
            (turn + 1) * tool_results // turns - turn * tool_results // turns
        ):
            call_id = f"t{turn}.{i}"
            append(
                {
                    "role": "assistant",
                    "task_context_id": owner,
                    "run_id": run_id,
                    "content": "",
                    "tool_calls": [{"id": call_id, "name": "Probe", "arguments": {}}],
                },
                owner,
            )
            append(
                {
                    "role": "tool_results",
                    "task_context_id": owner,
                    "run_id": run_id,
                    "results": [
                        {
                            "call_id": call_id,
                            "name": "Probe",
                            "success": True,
                            "value": {"text": "x" * 640},
                        }
                    ],
                },
                owner,
            )
        append(
            {
                "role": "assistant",
                "task_context_id": owner,
                "run_id": run_id,
                "content": f"Answer {turn}",
                "answer_complete": True,
                "session_end_complete": True,
            },
            owner,
        )

    state["active"] = "active"
    with db:
        db.execute(
            "UPDATE context_nodes SET created_at='19000101', updated_at='19000101', value_json=? WHERE node_id='root'",
            (encode(root),),
        )
        db.executemany("INSERT INTO context_nodes VALUES (?,?,?,?,?,?,?)", rows)
        db.execute(
            "UPDATE context_tree_metadata SET committed_leaf_id=?, committed_run_id=?",
            (parent, run_id),
        )
    print(
        json.dumps(
            {
                "users": turns,
                "tool_results": tool_results,
                "contexts": contexts,
                "nodes": len(rows) + 1,
            }
        )
    )


def measure(directory):
    calls, completions, requests = [], [], []

    async def model(arguments, context):
        calls.append(time.perf_counter())
        requests.append(
            hashlib.sha256(
                json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        )
        if len(calls) == 1:
            return {
                "content": "",
                "tool_calls": [
                    {"id": "call-fixture", "name": "Probe", "arguments": {}}
                ],
            }
        return {"content": "Done", "tool_calls": []}

    def event(item):
        if item.type == "tool.completed":
            completions.append(time.perf_counter())

    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        sample = Path(temporary) / "sample"
        shutil.copytree(directory / "fixture", sample)
        plugins = registry(model)
        started = time.perf_counter()
        session = AgentSession(
            sample,
            directory,
            directory / "plugins",
            registry=plugins,
            event_listener=event,
            plugin_services={"model_context_limit": lambda *args, **kwargs: 128000},
        )
        opened = time.perf_counter()
        try:
            # Mirror each release's send-path preflight.
            if "include_history" in inspect.signature(session.snapshot).parameters:
                session.snapshot(include_history=False)
            else:
                session.snapshot()
            session.submit("Check one item", node_id="bench-user", run_id="bench")
            asyncio.run(session.drain())
            assert len(calls) == 2 and len(completions) == 1
            print(
                json.dumps(
                    {
                        "open": opened - started,
                        "first": calls[0] - started,
                        "next": calls[1] - completions[0],
                        "both": requests,
                    }
                )
            )
        finally:
            session.close()


def measure_path(directory):
    """Retained Python allocations and deep-branch reads on the same 10x tree."""
    from contextlib import nullcontext
    import gc
    import tracemalloc
    from cyrene.core.context import ContextStoreRouter, graph_reads

    tree_id = "agent-session"
    scope = getattr(graph_reads, "model_read_scope", lambda *_: nullcontext())
    read = graph_reads.model_path
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        database = Path(temporary) / "context"
        shutil.copytree(directory / "fixture" / "context", database)
        with ContextStoreRouter(database) as router:
            tree = router.get_tree(tree_id)
            deep, _ = router.committed_state(tree_id)
            with router._lease(tree_id) as store:
                # Isolate storage reads from asynchronous delivery of fixture hooks.
                other = store.mount(tree.root_id, {"role": "user", "content": "other branch"})
                gc.collect()
                tracemalloc.start()
                before = tracemalloc.get_traced_memory()[0]
                with scope(router, tree_id):
                    read(router, tree_id, deep)
                gc.collect()
                retained = tracemalloc.get_traced_memory()[0] - before
                tracemalloc.stop()

                def count_read(statement):
                    nonlocal reads
                    if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                        reads += 1

                timings = []
                reads = 0
                for sample in range(6):
                    with scope(router, tree_id):
                        read(router, tree_id, other.id)
                        new = store.mount(deep, {"role": "context", "content": "new leaf"})
                        if sample == 0:
                            store._connection.set_trace_callback(count_read)
                        start = time.perf_counter()
                        nodes = read(router, tree_id, new.id)
                        elapsed = time.perf_counter() - start
                        if sample == 0:
                            store._connection.set_trace_callback(None)
                            assert list(nodes) == router.get_path(tree_id, new.id)
                        else:
                            timings.append(elapsed)
                        del nodes
    print(json.dumps({"retained_python_bytes": retained, "branch_sql_reads": reads,
                      "branch_seconds": timings}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed", "measure", "path"])
    parser.add_argument(
        "directory", type=Path, help="Isolated directory for the synthetic benchmark"
    )
    args = parser.parse_args()
    {"seed": seed, "measure": measure, "path": measure_path}[args.action](args.directory.resolve())
