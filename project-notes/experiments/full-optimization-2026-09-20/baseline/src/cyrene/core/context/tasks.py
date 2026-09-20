"""Mutable task documents. The execution tree remains the durable event source.

The root owns one current document per ID, not document revisions. All mutations
(including model-assisted rewrites) hold the same loop-neutral session gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from dataclasses import is_dataclass, replace as dataclass_replace
from pathlib import Path

STATE_KEY = "_task_contexts"
SHARED_ID = "shared"
TOOLS = frozenset({"load_context", "unload_context", "append_context", "replace_context"})
PROMPT = """Manage task contexts proactively and silently; do not ask the user to manage
IDs or call a tool merely to announce task ownership. A context follows a user
goal or a distinct workstream within that goal, not a message, topic keyword,
tool, or file. Leave context-management call prose empty and put checkpoints in arguments. Unless asked, omit internal
operations, IDs and state from replies and send_message; report only task
progress or actionable blockers.

- CONTINUE: use the initial context without unloading or a startup checkpoint;
  keep tightly coupled steps, explanations, progress questions, clarifications,
  corrections, tests and reports together. Do not switch merely to save, finish,
  wait, or because a message, file or topic changed.
- NEW: proactively separate goals or substantial subtasks with independently
  resumable evidence, decisions and progress, even within one request (e.g.
  frontend, backend or a security audit sharing an API contract). This includes
  text-only work and work after a context demonstration. A previous load is not
  a new switch. Unload the active context before starting;
  if none is active, new task work creates one automatically.
- RESUME: match both goal and subtask to the catalog. If inactive, unload the
  current context, if any, then load the exact matching ID; match the work,
  not recency or project name. If already active,
  continue. Never use an unrelated placeholder or create a duplicate.
Honor explicit workstream boundaries, but do not split every checklist item or
brief lookup; split for capacity only when warned. Reports alone do not start new
goals; resume the implementation context when returning from research or reporting.
A switch pauses a workstream, not the overall request: continue remaining work
without asking the user to restart it. Keep common goals and contracts in shared,
local progress in its context; verify source evidence when integrating findings.
Remembering dialogue or rereading files
does not restore a context.

Use the always-visible task_context_catalog (IDs, last unload summaries, active
status) and successful receipts as current state, never invented IDs or earlier
narratives. Do not claim a missing active context or stale catalog without
evidence; an earlier unload does not prove that no task is active now.
The current request remains available while switching; other inactive-task
messages do not. Each context management call must be the only call in its response. Wait for
success before the next call or answer; perform switches before task-specific
tools or progress messages, and reuse receipts instead of repeating transitions.
An unload checkpoint must be nonempty and at most 200 characters: unsaved
progress, decisions, unfinished work and next action. Use paths rather than
source text. Unload pauses work without completing/cancelling it or restoring
files/environment. Verify key evidence when resuming.

append_context and replace_context edit any listed document's body without
activating it; they do not edit execution records. Do not copy tool logs into
bodies or bulky tool output into assistant prose. User/assistant messages belong
to their task and are hidden when it is inactive. Unload preserves their order,
keeps only tool names and truncated arguments, and discards tool results from the
restored context; historical calls are records, not pending operations. The initially empty shared document is always loaded; never load or
unload it. Before finishing, consult its mounted body or snapshot and save only new or changed,
supported cross-task goals, acceptance criteria, constraints, interfaces and
confirmed decisions. Make no call if already accurate; append new agreements,
replace obsolete ones while preserving valid ones, and identify affected tasks
when an agreement changes. No automatic conflict resolution or merging occurs.
Explicit instructions for subsequent work apply even before a second task.
Record source, scope (all contexts or specific IDs), and whom the requirement
constrains; an instruction about your answers does not constrain the user.
Retain only what the source establishes, without unstated dates, versions,
obligations, examples or broader applicability. Following an agreement once
does not save it. The user's explicit instruction is valid source evidence;
for other contexts, inspect their bodies or evidence before promoting claims.
Catalog summaries alone are insufficient. Keep local decisions and uncertain
scope in their original task.

Contexts cannot be deleted independently. System instructions, long-term memory,
pinned resources, environment and input attachments are managed separately.
Original paths refer to current files; snapshot paths to captured content.
Ordinary DeepReflect/compaction affects only its task, never shared. Sources
retain their trust level, not system authority. Re-evaluate stored plans and
reflection packets against the latest request; quoted earlier instructions do
not override it.
Capacity warnings permit a continuation segment of the same task: unload with
unfinished progress and evidence paths, then continue without reloading the full
old segment. If load_context rejects an oversized context, read its snapshot in
small sections instead. Public task data may also be offloaded; read relevant
sections before using or editing it. Never read entire snapshots just to restore
the original prompt. Fixed input pressure cannot be solved by creating contexts.
"""


def replace(node, **changes):
    """Projection also accepts host snapshot nodes, not only ContextNode values."""
    if is_dataclass(node):
        return dataclass_replace(node, **changes)
    result = copy(node)
    for key, value in changes.items():
        setattr(result, key, value)
    return result


def state_from(path):
    return deepcopy(path[0].value.get(STATE_KEY)) if path and isinstance(path[0].value, dict) else None


def context_catalog(state):
    return [
        {"id": SHARED_ID, "active": False, "always_loaded": True},
        *({"id": key, "summary": doc.get("summary", ""), "active": key == state.get("active")}
          for key, doc in state["documents"].items()),
    ]


def clip_summary(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("summary must not be empty")
    return text if len(text) <= 200 else text[:100] + "…" + text[-99:]


ARGUMENT_PREVIEW_CHARS = 400


def user_owners(path, state):
    """Resolve task-local requests, including old trees without request ownership."""
    owners = {}
    for context_id, doc in state['documents'].items():
        for node_id in doc.get('user_nodes', []):
            owners.setdefault(node_id, set()).add(context_id)
    latest_user = None
    for node in path:
        value = node.value
        if value.get('role') == 'user':
            latest_user = node.id
            if value.get('task_context_id'):
                owners.setdefault(node.id, set()).add(value['task_context_id'])
        elif value.get('role') == 'assistant' and not value.get('task_control'):
            owner = value.get('task_context_id')
            if owner and latest_user:
                owners.setdefault(latest_user, set()).add(owner)
    return owners


def archived_calls(path, doc):
    archived = set(doc.get('unloaded_nodes', []))
    # Previously unloaded contexts used result references. Their calls now obey
    # the same lossy restore rule without rewriting the durable execution log.
    references = set(doc.get('reference_nodes', []))
    archived.update(n.parent_id for n in path if n.id in references)
    return archived


def historical_assistant(value):
    """A completed call without results is evidence, never a protocol tool call."""
    content = str(value.get('content') or '')
    calls = []
    for call in value.get('tool_calls', []):
        arguments = json.dumps(call.get('arguments', {}), ensure_ascii=False, default=str)
        preview = arguments[:ARGUMENT_PREVIEW_CHARS]
        if len(arguments) > ARGUMENT_PREVIEW_CHARS:
            preview = preview[:-1] + '…'
        calls.append({'name': call.get('name', ''), 'arguments_preview': preview})
    if calls:
        content += ('\n\n' if content else '') + '[Historical tool calls; completed records, results discarded]\n' + json.dumps(calls, ensure_ascii=False)
    return {'role': 'assistant', 'content': content}


def saved_messages(messages):
    return [historical_assistant(m) if m.get('role') == 'assistant' else deepcopy(m)
            for m in messages if m.get('role') != 'tool']


def task_nodes(path, state, context_id):
    doc = state['documents'][context_id]
    covered = set(doc.get('covered', []))
    owners = user_owners(path, state)
    # Keep the currently executing request exact outside compaction.
    current_user = next((n.id for n in reversed(path) if n.value.get('role') == 'user'), None)
    return [n for n in path[1:] if n.id not in covered and (
        (n.value.get('role') == 'user' and n.id != current_user and context_id in owners.get(n.id, set()))
        or (n.value.get('task_context_id') == context_id and n.value.get('role') in {'assistant', 'tool_results'}))]


def task_messages(path, state, context_id, *, live=False, observation_services=()):
    from .projection import project_model_messages
    doc = state['documents'][context_id]
    nodes = task_nodes(path, state, context_id)
    archived = archived_calls(path, doc)
    selected = []
    included_calls = {}
    for node in nodes:
        value = deepcopy(node.value)
        role = value.get('role')
        if role == 'assistant':
            if node.id in archived:
                value = historical_assistant(value)
            else:
                included_calls[node.id] = {c.get('id') for c in value.get('tool_calls', [])}
        elif role == 'tool_results':
            value['results'] = [r for r in value.get('results', [])
                                if r.get('call_id') in included_calls.get(node.parent_id, set())]
            if not value['results']:
                continue
        selected.append(replace(node, value=value))
    messages = []
    if doc.get('body'):
        messages.append({'role': 'user', 'content': '[Task context data]\n' + doc['body']})
    messages.extend(deepcopy(doc.get('messages', [])))
    root = replace(path[0], value={'role': 'system', 'content': ''})
    projected = project_model_messages([root, *selected], observation_services=observation_services)
    messages.extend(m for m in projected if m.get('role') != 'system' or m.get('content'))
    return messages


def project_tasks(path, state, *, observation_services=()):
    """Keep the original chronological projection during an activation.

    Only task ownership, explicit rewrites and a reload remove material. In
    particular, a new model call never evicts a prior observation on its own.
    """
    from .projection import project_model_messages
    active = state.get("active")
    doc = state["documents"].get(active, {})
    covered = set(doc.get("covered", []))
    archived = archived_calls(path, doc)
    owners = user_owners(path, state)
    current_user = next((n.id for n in reversed(path) if n.value.get("role") == "user"), None)
    # Keep the whole current request's management handshake, including failures
    # before a switch. Otherwise the model can repeat an already completed
    # multi-step transition when the successful load hides its earlier steps.
    control_start = max((i for i, n in enumerate(path) if n.value.get("role") == "user"), default=0)
    selected = []
    included_calls = {}
    for index, node in enumerate(path):
        value = deepcopy(node.value)
        value.pop(STATE_KEY, None)
        role = value.get("role")
        if role == "context" and str(value.get("context_kind", "")).startswith("task_capacity."):
            if value["context_kind"] != f"task_capacity.{active}":
                continue
        if role == "context_reflection" and not value.get("task_context_id"):
            for record in value.get("public_nodes", []):
                original = deepcopy(record.get("value", {}))
                if active not in owners.get(record.get("id"), set()) and original.get("task_context_id") != active:
                    continue
                if original.get("role") in {"user", "assistant"}:
                    original.pop("tool_calls", None)
                    original["content"] = original.get("metadata", {}).get("public_user_message", original.get("content", ""))
                    selected.append(replace(node, id=record.get("id", node.id), value=original))
            continue
        if role in {"context_compaction", "context_reflection"}:
            continue
        if role == 'user':
            if node.id != current_user and (active not in owners.get(node.id, set()) or node.id in covered):
                continue
        elif role == "assistant":
            transition = bool(value.get('tool_calls')) and all(
                c.get('name') in {'load_context', 'unload_context'} for c in value['tool_calls'])
            handshake = bool(value.get('task_control') and index >= control_start and node.id not in covered
                             and (transition or (value.get('task_context_id') == active and node.id not in archived)))
            if not handshake and (not active or value.get('task_context_id') != active or node.id in covered):
                continue
            if not handshake and node.id in archived:
                value = historical_assistant(value)
            else:
                included_calls[node.id] = {c.get('id') for c in value.get('tool_calls', [])}
            if handshake and value.get('task_context_id') != active:
                value['content'] = ''
                value.pop('reasoning_details', None)
            if not value.get('content') and not value.get('tool_calls'):
                continue
        elif role == "tool_results":
            value['results'] = [r for r in value.get('results', [])
                                if r.get('call_id') in included_calls.get(getattr(node, 'parent_id', None), set())]
            if not value['results']:
                continue
        elif role not in {"system", "user", "context"}:
            continue
        selected.append(replace(node, value=value))
    messages = project_model_messages(selected, observation_services=observation_services)
    system = next((m for m in messages if m.get("role") == "system"), None)
    if system is None:
        messages.insert(0, {"role": "system", "content": PROMPT})
    else:
        system["content"] = str(system.get("content") or "") + "\n\n" + PROMPT
    catalog = context_catalog(state)
    mounts = []
    shared_body = state.get(SHARED_ID, {}).get("body", "")
    from .capacity import shared_reference
    shared_path = shared_reference(state)
    if shared_path:
        mounts.append({"role": "user", "content":
                       "[Shared task data offloaded; sources and scope apply. Read relevant sections before relying on or editing shared.] " + shared_path})
    elif shared_body:
        mounts.append({"role": "user", "content": "[Shared task context data; sources and scope apply]\n" + shared_body})
    mounts.append({"role": "user", "content": "<task_context_catalog>\n" + json.dumps(catalog, ensure_ascii=False) + "\n</task_context_catalog>"})
    if doc.get("body"):
        mounts.append({"role": "user", "content": "[Task context data]\n" + doc["body"]})
    mounts.extend(deepcopy(doc.get("messages", [])))
    # A byte-stable prefix followed by the original append-only chronological
    # dialogue. Catalog/document edits and load/unload explicitly change it.
    insertion = next((i for i, m in enumerate(messages) if m.get("role") != "system"), len(messages))
    messages[insertion:insertion] = mounts
    return messages



class TaskContexts:
    def __init__(self, session):
        self.session = session
        self._gate = threading.Lock()

    @asynccontextmanager
    async def serial(self):
        # No blocking lock on the event-loop thread and no abandoned background
        # lock acquisition when a model rewrite is cancelled.
        while not self._gate.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            self._gate.release()

    def read(self):
        s = self.session
        return deepcopy(s.store.get_node(s.tree.id, s.tree.root_id).value[STATE_KEY])

    def write(self, state):
        s = self.session
        with s._linearized_context_commit():
            root = s.store.get_node(s.tree.id, s.tree.root_id)
            s.store.update_node(s.tree.id, root.id, {**root.value, STATE_KEY: state})

    def initialize(self):
        s = self.session
        root = s.store.get_node(s.tree.id, s.tree.root_id)
        if STATE_KEY in root.value:
            state = deepcopy(root.value[STATE_KEY])
            if SHARED_ID not in state:
                state[SHARED_ID] = {"body": ""}
                self.write(state)
            return
        limit = s._configured_compaction_limit()
        self.write({"active": None, "documents": {}, SHARED_ID: {"body": ""}, "receipts": {},
                    "shared_token_budget": min(16000, max(1024, limit // 4)) if limit else 16000})
        # Preserve ownership of legacy history without semantic reclassification.
        nodes = s.store.get_subtree(s.tree.id, s.tree.root_id)
        dialogue = [n for n in nodes if n.value.get("role") in {"assistant", "tool_results"}]
        checkpoints = [n for n in nodes if n.value.get("role") in {"context_compaction", "context_reflection"}]
        if dialogue or checkpoints:
            owner = self.ensure("legacy")
            for n in dialogue:
                s.store.update_node(s.tree.id, n.id, {**n.value, "task_context_id": owner})
            if checkpoints:
                last = max(checkpoints, key=lambda n: n.created_at)
                state = self.read()
                doc = state["documents"][owner]
                doc["messages"] = [{**m, "role": "user"} if m.get("compacted_block") else m
                                   for m in last.value.get("messages", [])
                                   if m.get("compacted_block") or m.get("role") != "system"]
                doc["covered"] = [n.id for n in s.store.get_path(s.tree.id, last.id)]
                self.write(state)

    def recover_compaction(self):
        state = self.read()
        pending = state.get("pending_compaction")
        if not pending:
            return None
        s = self.session
        from .errors import NodeNotFoundError
        try:
            node = s.store.get_node(s.tree.id, pending["node_id"])
        except NodeNotFoundError:
            node = s.store.mount(s.tree.id, pending["parent_id"], pending["value"], node_id=pending["node_id"])
        state.pop("pending_compaction", None)
        self.write(state)
        return node

    def ensure(self, key):
        state = self.read()
        if not state["active"]:
            context_id = "ctx_" + hashlib.sha256(str(key).encode()).hexdigest()[:16]
            state["documents"].setdefault(context_id, {"body": "", "summary": "", "messages": [], "covered": []})
            state["active"] = context_id
            self.write(state)
        path = self.session.store.get_path(self.session.tree.id, self.session._leaf_id)
        latest_user = next((n.id for n in reversed(path) if n.value.get('role') == 'user'), None)
        users = state['documents'][state['active']].get('user_nodes', [])
        if latest_user and latest_user not in users:
            state['documents'][state['active']]['user_nodes'] = [*users, latest_user]
            self.write(state)
        return state["active"]

    async def execute(self, name, args, receipt):
        async with self.serial():
            state = self.read()
            if receipt in state["receipts"]:
                return state["receipts"][receipt]
            target = args.get("context_id")
            if name == "unload_context":
                summary = clip_summary(args["summary"])
                target = state["active"]
                if not target:
                    raise ValueError("No active context to unload")
                state["documents"][target]["summary"] = summary
                s = self.session
                path = s.store.get_path(s.tree.id, s._leaf_id)
                doc = state['documents'][target]
                doc['unloaded_nodes'] = list(dict.fromkeys([
                    *doc.get('unloaded_nodes', []),
                    *(n.id for n in path if n.value.get('task_context_id') == target
                      and n.value.get('role') in {'assistant', 'tool_results'}),
                ]))
                doc['messages'] = saved_messages(doc.get('messages', []))
                state["active"] = None
            else:
                if target == SHARED_ID:
                    if name != "append_context" and name != "replace_context":
                        raise ValueError("shared is always loaded; only append_context and replace_context can edit it")
                    doc = state[SHARED_ID]
                else:
                    if target not in state["documents"]:
                        raise ValueError("Unknown context_id; use the context catalog")
                    doc = state["documents"][target]
                if name == "load_context":
                    if state["active"] and state["active"] != target:
                        raise ValueError("Unload the active context with a summary first")
                    # Tool results are intentionally absent from restored tasks;
                    # their old observation files are not load dependencies.
                    from .capacity import check_load
                    check_load(self, state, target)
                    state["active"] = target
                elif name == "append_context":
                    doc["body"] += ("\n\n" if doc["body"] else "") + args["content"]
                elif name == "replace_context":
                    doc["body"] = args["content"]
                else:
                    raise ValueError("Unknown context operation")
            result = {"context_id": target, "active_context_id": state["active"], "saved": True}
            state["receipts"][receipt] = result
            self.write(state)
            return result

    def reference(self, result, call):
        """Store exact observations under the tree-owned artifact directory."""
        if result.get("name") in TOOLS:
            return
        value = result.get("value")
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        name = str(result.get("name", ""))
        if len(encoded) <= 2000 and name != "Read":
            return
        s = self.session
        directory = s.store.artifact_directory(s.tree.id)
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        path = directory / (digest + ".json")
        if not path.exists():
            temp = path.with_suffix(".tmp")
            temp.write_bytes(encoded.encode("utf-8"))
            temp.replace(path)
        ref = {"snapshot_path": str(path), "sha256": digest, "preview": encoded[:300]}
        args = (call or {}).get("arguments", {})
        if args.get("path"):
            source = Path(args["path"]).expanduser()
            ref["source_path"] = str(source if source.is_absolute() else s.workspace / source)
            ref["start_line"] = args.get("start_line")
            ref["end_line"] = args.get("end_line")
        if args.get("url"):
            ref["source_url"] = args["url"]
        result["task_reference"] = ref


def fork_task_state(path):
    """Reconstruct task state at a fork boundary from successful tool events.

    Copying the live root would import decisions made *after* the selected turn.
    Replaying retained events needs no document revisions. Compaction is omitted:
    the fork can project its retained raw execution records and compact them anew.
    """
    original = state_from(path)
    if original is None:
        return None
    state = {"active": None, "documents": {}, SHARED_ID: {"body": ""}, "receipts": {},
             "shared_token_budget": original.get("shared_token_budget", 16000)}
    by_id = {n.id: n for n in path}
    for node in path[1:]:
        value = node.value
        owner = value.get("task_context_id")
        if owner and owner != SHARED_ID:
            state["documents"].setdefault(owner, {"body": "", "summary": "", "messages": [], "covered": []})
        if value.get("role") == "assistant" and owner:
            state["active"] = owner
        if value.get("role") != "tool_results":
            continue
        parent = by_id.get(node.parent_id)
        calls = {c.get("id"): c for c in parent.value.get("tool_calls", [])} if parent else {}
        for result in value.get("results", []):
            call = calls.get(result.get("call_id"), {})
            name = call.get("name")
            if name not in TOOLS or not result.get("success"):
                continue
            args = call.get("arguments", {})
            target = args.get("context_id")
            if name == "unload_context":
                target = owner or state["active"]
                if target in state["documents"]:
                    state["documents"][target]["summary"] = clip_summary(args["summary"])
                    state["documents"][target]["unloaded_nodes"] = [
                        n.id for n in path[:path.index(node) + 1]
                        if n.value.get('task_context_id') == target
                        and n.value.get('role') in {'assistant', 'tool_results'}]
                state["active"] = None
            elif name == "load_context":
                if target in state["documents"]:
                    state["active"] = target
            else:
                doc = state[SHARED_ID] if target == SHARED_ID else state["documents"].get(target)
                if doc is not None:
                    content = str(args.get("content", ""))
                    doc["body"] = ((doc["body"] + "\n\n") if doc["body"] else "") + content if name == "append_context" else content
    return state
