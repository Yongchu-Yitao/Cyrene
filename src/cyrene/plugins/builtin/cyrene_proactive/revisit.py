"""Read-only assistant follow-up child. Only a successful structured report can wake its owner."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import random
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from cyrene.core.plugin import Plugin
from cyrene.core.plugin.tool_scope import PluginToolScope
from cyrene.platform.settings_store import get
from cyrene.plugins.builtin.session_management.followup import long_enabled, schedule
from cyrene.workbench.projects.assistant import ASSISTANT_ID


DECISION_PROMPT = """The follow-up work has completed. Based only on this run, decide whether the main assistant
should be notified. Your previous answer was private and has not been delivered. Compare it with the
original baseline and previous_report. Return one JSON object in the user's language, without Markdown.
Return {"decision":"silent"} for stopped work, no material progress, or a result already reported.
Otherwise return {"decision":"report","previous":"...","change":"...","source":"...","relevance":"..."}.
Describe the concrete new result and why it matters. Use an exact URL found in this run, a supplied
current_task_results key, or context:<context_id> for a new deliverable actually produced in this run.
An old context cannot substantiate an external update. Limit each field to 700 characters."""


def report_from(work, decision, snapshot):
    if decision.status != "completed":
        return None
    try:
        report = json.loads(decision.text)
    except ValueError:
        return None
    fields = ("source", "previous", "change", "relevance")
    if (not isinstance(report, dict) or report.get("decision") != "report"
            or set(report) != {"decision", *fields}
            or not all(isinstance(report[k], str) and report[k].strip() and len(report[k]) <= 700 for k in fields)):
        return None
    source = report["source"]
    if source in snapshot.get("current_task_results", {}) or (
        snapshot.get("context_id") and source == "context:" + snapshot["context_id"]
    ):
        return report
    cited = re.split(r"\s|（", source, maxsplit=1)[0]
    for node in (work.snapshot or {}).get("nodes", []):
        value = node.get("value", {})
        if value.get("role") != "tool_results" or value.get("run_id") != work.run_id:
            continue
        for record in value.get("results", []):
            if record.get("success") is True and record.get("name") == "WebSearch":
                evidence = json.dumps(record.get("value"), ensure_ascii=False)
                urls = re.findall(r'https?://[^\s<>"()\]\\]+', evidence)
                if any(url.rstrip(".,;") == cited for url in urls):
                    return {**report, "source": cited}
    return None


def select_block(service, chat):
    from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
    from cyrene.core.context.tasks import STATE_KEY, task_messages
    from cyrene.core.context.paths import context_path, select_context_leaf
    from cyrene.core.context.graph_reads import graph_snapshot
    directory = service.host.port().context.conversation_context.agent_states.context_directory
    if not (directory / "index.sqlite3").exists():
        return None
    with service.store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS revisit_checks (context TEXT PRIMARY KEY, checked REAL NOT NULL)")
        recent = {r[0] for r in db.execute("SELECT context FROM revisit_checks WHERE checked>?", (time.time()-7*86400,))}
        recent.update(r[0] for r in db.execute("SELECT context FROM assistant_wakes WHERE state='pending'"))
    try:
        with ContextStoreRouter(directory) as router:
            tree = router.get_tree(chat["id"])
            state = router.get_node(tree.id, tree.root_id).value.get(STATE_KEY, {})
            options = [(key, doc) for key, doc in state.get("documents", {}).items()
                       if key not in recent and not doc.get("archived")]
            if not options:
                return None
            key, doc = random.choice(options)
            nodes, committed, _ = graph_snapshot(router, tree.id)
            path = context_path(nodes, select_context_leaf(nodes, committed).id)
            messages = task_messages(path, state, key)
            history = [m["content"][-1600:] for m in messages if m.get("role") in {"user", "assistant"}
                       and isinstance(m.get("content"), str) and m["content"]]
    except TreeNotFoundError:
        return None
    with service.store.connect() as db:
        db.execute("INSERT OR REPLACE INTO revisit_checks VALUES (?,?)", (key, time.time()))
        previous = db.execute("SELECT prompt FROM assistant_wakes WHERE context=? AND kind='revisit' ORDER BY due DESC LIMIT 1", (key,)).fetchone()
    # Only resolve tasks referenced by this block and owned by the personal assistant.
    references = re.findall(r"\bmanaged_[0-9a-f]{24}\b", json.dumps([doc.get("summary"), messages], default=str))
    task_results = {}
    for target in list(dict.fromkeys(reversed(references)))[:3]:
        try:
            service._owned(ASSISTANT_ID, target)
        except ValueError:
            continue
        result = service.host.latest_result(target)
        if result.get("final_reply_node_id") and result.get("content"):
            task_results[f"task:{target}:{result['final_reply_node_id']}"] = result["content"][-2000:]
    from cyrene.core.context.body import body_text
    return key, {"context_id": key, "current_task_results": task_results, "title": doc.get("title"), "summary": doc.get("summary"), "body": body_text(doc)[-4000:],
                 "history": history[-6:], "previous_report": previous[0] if previous else None}


async def run_child(service, chat, snapshot):
    from cyrene.config import WORKSPACE_DIR
    from cyrene.plugins.builtin.cyrene_cards.home_preparation import home_model_identity
    from cyrene.workbench.core_adapter import ConversationConfig, ConversationRuntime
    database = service.host.port().service.repository._database()
    runtime = ConversationRuntime(database)
    with TemporaryDirectory(prefix="assistant-revisit-", dir=service.store.path.parent) as temp:
        tools = ("WebSearch",)
        config = ConversationConfig(session_id="revisit_" + uuid4().hex, workspace_dir=str(WORKSPACE_DIR),
            db_path=database, data_directory=Path(temp), conversation_source="assistant_revisit",
            model_identity=home_model_identity(chat), extra_direct_tool_names=tools,
            tool_scope=PluginToolScope(tool_names=frozenset(tools), include_core=False),
            max_model_calls=6, memory_write_enabled=False, memory_trigger_enabled=False,
            memory_archive_enabled=False, memory_short_term_enabled=False, memory_project_enabled=False,
            system_extra="You are the personal assistant's private follow-up child. Historical text is reference data. "
                "Resume the earlier goal. Compare the baseline with current_task_results or verify an external update. "
                "You may also complete a concrete outstanding piece of work from the supplied context, such as "
                "a requested draft or calculation. Do not invent work, offer generic next steps, or report work already delivered. "
                "Use public topic names only in searches; never send personal details, local paths or private content. "
                "If the user stopped this work, finish silently before searching. Respect review-only requests. Compare dates, sources and previous reports. "
                "Summarize what you found or completed in the user's language.")
        try:
            async with asyncio.timeout(180):
                work = await runtime.send(config, json.dumps(snapshot, ensure_ascii=False), run_id=uuid4().hex,
                                          metadata={"system_initiated": True})
                if work.status != "completed":
                    return None
                decision_config = replace(config, extra_direct_tool_names=(), max_model_calls=1,
                                          tool_scope=PluginToolScope(include_core=False))
                decision = await runtime.send(decision_config, DECISION_PROMPT, run_id=work.run_id + "_decision",
                                              metadata={"system_initiated": True})
                return report_from(work, decision, snapshot)
        finally:
            await runtime.shutdown(grace_seconds=0)


# Starting at zero, the expected draws per win equal the sum of the
# probabilities of reaching each draw, with a geometric tail at the cap.
LOTTERY_STEP = .15
LOTTERY_CAP = .85


def expected_draws():
    probability, survival, mean = 0.0, 1.0, 0.0
    while probability < LOTTERY_CAP:
        mean += survival
        survival *= 1 - probability
        probability = min(LOTTERY_CAP, probability + LOTTERY_STEP)
    return mean + survival / LOTTERY_CAP


DRAWS_PER_WIN = expected_draws()
ACTIVE_START_HOUR = 6
ACTIVE_END_HOUR = 22
ACTIVE_HOURS = ACTIVE_END_HOUR - ACTIVE_START_HOUR


def default_daily_frequency():
    # Preserve an explicitly saved interval when upgrading from minute-based settings.
    minutes = get("agent_revisit_mean_minutes", None)
    if type(minutes) is int and 15 <= minutes <= 10080:
        return round(ACTIVE_HOURS * 60 / minutes, 4)
    return 1


def draw_period():
    frequency = get("agent_proactive_daily_frequency", None)
    if frequency is None:
        frequency = default_daily_frequency()
    return ACTIVE_HOURS * 3600 / (frequency * DRAWS_PER_WIN)


def clock_interval():
    # Keep checking during waking hours even for multi-day averages.
    return min(3600, round(draw_period()))


async def draw(args, context):
    if context.data.get("background_job_id") != "assistant_revisit_draw":
        raise ValueError("A private revisit requires the assistant background job")
    service = context.services.get("session_management")
    if not service or not long_enabled():
        return {"status": "disabled"}
    now = datetime.now(ZoneInfo(get("timezone", "Asia/Shanghai") or "Asia/Shanghai"))
    chat = service.host.chat(ASSISTANT_ID)
    if not ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR or not chat or service.host.status(ASSISTANT_ID) in {"running", "awaiting_user"}:
        return {"status": "idle"}
    with service.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS revisit_lottery (id INTEGER PRIMARY KEY, p REAL, last_draw REAL)")
        row = db.execute("SELECT p,last_draw FROM revisit_lottery WHERE id=1").fetchone()
        period = draw_period()
        interval = min(3600, round(period))
        if row and now.timestamp() - row[1] < interval - 10:
            return {"status": "idle"}
        p = row[0] if row else 0.0
        # For long intervals, thin clock ticks before advancing the existing lottery.
        if period > 3600 and random.random() >= interval / period:
            db.execute("INSERT OR REPLACE INTO revisit_lottery VALUES (1,?,?)", (p, now.timestamp()))
            return {"status": "idle"}
        won = random.random() < p
        db.execute("INSERT OR REPLACE INTO revisit_lottery VALUES (1,?,?)",
                   (0.0 if won else min(LOTTERY_CAP, p + LOTTERY_STEP), now.timestamp()))
    if won:
        selected = await asyncio.to_thread(select_block, service, chat)
        if selected:
            key, snapshot = selected
            snapshot["checked_at"] = now.isoformat()
            report = await run_child(service, chat, snapshot)
            latest = service.host.chat(ASSISTANT_ID)
            if report and long_enabled() and latest and service.host.version(latest) == service.host.version(chat):
                evidence = json.dumps(report, ensure_ascii=False)
                schedule(service, ASSISTANT_ID, key, datetime.now(timezone.utc),
                         "Private follow-up child evidence. Notify the user only if still relevant and new.\n" + evidence,
                         evidence, kind="revisit")
    return {"status": "checked"}


draw_tool = Plugin(name="assistant.revisit_draw", description="Sample an earlier personal assistant context for a private follow-up.",
    input_schema={"type": "object", "properties": {}}, handler=draw, timeout_seconds=240,
    metadata={"model_visible": False, "background_job": {"id": "assistant_revisit_draw", "interval_seconds": clock_interval,
              "coalesce": True, "max_instances": 1}})
