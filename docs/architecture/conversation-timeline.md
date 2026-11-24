# Conversation timeline

The Workbench run owns the public timeline (`chat/run_timeline.py`). Stream
observations update identified records. Each published event carries the
changed records, a run-scoped revision and the run status. Persisted messages
use those same records; ContextTree remains the Agent's execution/context
checkpoint, not a second algorithm for arranging the public transcript.

## Identity and boundaries

- Model stream events carry a source identity shared with their completed
  assistant node. Automatic model retry attempts have distinct stream identities.
- A message starts with its own ID. Deltas and final snapshots update that ID.
  Starting another message cannot overwrite the preceding message. Identical
  text is not an identity or a reason to deduplicate two messages.
- Activities have stable IDs. Reasoning and tools from the same source may share
  a card. Prose/guidance closes membership of preceding cards. Tools already in
  those cards can finish later and update their original owner. Reasoning streams
  likewise retain a separate owner from the current card accepting new work.
  Guidance does not clear the active reply/reasoning identity (including streams
  without a source ID); subsequent deltas finish above the guidance in place.
- Source event IDs deduplicate repeated delivery. Timeline revisions prevent old
  snapshots from overwriting newer client state.
- Completed records are checkpointed. Finalization adds metadata to the existing
  final message. It does not regenerate activity cards or bind a new render ID.
- Retained-event gaps are served with the current full timeline snapshot.

## Presentation

Main, split and quick-chat surfaces use the same transcript projection. Activity
cards stay in the same order with the same membership after save or reconnect.
Three or more consecutive activity cards form a group; a visible message breaks
that group. Blank started activities retain identity after completion.

The collapsed active group names the currently running tool(s) or reasoning.
The settled group shows `Processed {duration}`. Duration is the interval from
its earliest activity start to its latest activity end, including elapsed gaps,
not the sum of parallel tools or the duration of the whole turn. Failed tool
records remain visible and do not prevent the group from settling.

Disclosure choices are keyed by record/group identity and stored locally. They
survive new events, completion, remount and refresh. A newly formed group inherits
an expanded child so grouping never hides what the user was reading. Collapsing
a group does not erase the individual children's disclosure choices.

`Continue working` is a transient run indicator, not a stored record or activity.
It appears only while the run is active with no currently streaming prose or
running activity. The same projection update removes it when work starts.
Waiting, reconnecting and terminal states do not show this indicator.

Old persisted messages remain readable without inventing precise missing timing
or changing their IDs. Legacy stream handlers exist for pre-timeline transports;
current timeline events bypass their independent transcript reducers.

## Streaming transport and replay

Timeline wire version 2 preserves the existing record identities and presentation
model. Record creation, structural changes and completion carry full `messages`.
Text growth carries `updates`: each operation names a record, its `baseRevision`,
text fields to `append`, metadata to `set`, and fields to `unset`. The receiver
assembles the record locally and rejects operations without their exact base.
Repeated/stale timeline revisions are ignored. Version 1 full-record logs remain
readable. `snapshot: true` replaces the entire run projection.

The renderer still receives the complete current message, retaining its 48 ms
buffered cadence, stable Markdown blocks and tail fade. Network delta size no
longer grows with the accumulated reply or reasoning trace.

Streams own a cursor and a wake signal, rather than private event queues. All
clients read the shared replay history, limited to 6,000 events and an 8 MiB
serialized-size budget (ack plus the newest event are always retained, even if
one event exceeds that budget). A lagging cursor receives the current snapshot
and retained terminal events. Consumers never block the producer or accumulate
an unbounded private backlog.

Before SQLite evicts a replay prefix, it folds those operations into the ack's
baseline snapshot in the same transaction. `replay_base_seq` prevents retried
flushes from resurrecting already-compacted operations. Restart recovery loads
the baseline and replays the retained suffix, including unfinished partial text.

## Task contexts

Each session stores multiple contexts and activates at most one ordinary context.
`switch_context` atomically checkpoints the previous context and activates an
existing or new one. `load_context` requires the previous context to be unloaded.
The current request and its control receipts survive a switch; other inactive
context histories do not. `shared` is always available independently.

A context's `body` is an ordered list containing user messages, assistant replies,
tool calls/results, and explicit additions. It is no longer a string prepended to
an independently assembled history. Entries reuse execution-tree record values
with source IDs; compacted messages and explicit additions retain their message
roles. The original execution tree remains the audit trail, including records
that were replaced or compacted out of the editable body. Reconciliation of
committed records is idempotent and persisted with session progress.

`append_context` adds a structured text record at the end of the target body.
During a tool call the paired result is kept adjacent to the assistant call,
followed by the addition. Subsequent dialogue follows that addition. An append
never rewrites earlier messages. Editing an inactive context does not activate it.
`replace_context` explicitly replaces the model-visible body, retaining the audit
trail. DeepReflect and compaction replace only the selected task's body and mark
covered source records so they cannot reappear. These explicit rewrites, context
switches and lifecycle mount changes may invalidate provider caches.

Legacy string bodies and saved `messages` lists migrate into structured entries;
existing chronological execution records are reconciled by their source IDs.
Reload keeps the existing policy of converting old tool calls to bounded
historical records and omitting their tool results. Images retire at the same
transition/compaction boundaries. Answered clarifications retain their existing
one-transition handoff. Tool pairing, multimodal materialization and lifecycle
context projection reuse the existing model-message adapters.

The context inspector exports the structured body as JSON and accepts structured
edits (legacy plain-text replacement is still supported). Search, capacity
snapshots, fork replay and task exports use the same body representation. Forks
replay only successful operations up to their boundary, never later root state.

### Always-loaded shared task document

Every conversation has a fixed `shared` document, initially empty. Older task
states receive it on reopen without modifying their active task or documents.
`append_context(content, context_id="shared")` and
`replace_context(content, context_id="shared")` edit its body using the same
serialized, idempotent operations. It has no revisions, generated title or summary.
It is stored separately from ordinary task documents so it cannot become active,
receive tool execution ownership, or be rewritten by ordinary DeepReflect or
compaction. `load_context("shared")` returns an error: it is already always loaded.
Unload never removes its body. Deleting the conversation removes it with the tree.

The catalog marks shared as `always_loaded`. Its structured body contains shared agreements rather than task execution records.
Initial shared text precedes the catalog; subsequent appends retain their chronological
positions without rewriting that prefix. Explicit replacement and offloading can
change the prefix. It is not a system prompt,
long-term memory or pinned resource. The Agent is instructed to maintain only
necessary cross-task goals, constraints, interface contracts and verified decisions,
with source references and scope (all contexts or named IDs). Uncertain/local
claims remain in their task; catalog summaries alone are not evidence. Updating an
agreement must identify affected tasks. No automatic merging/conflict resolver or
additional tool is introduced.

### Recovery and fork boundaries

Tool/result membership is resolved against the owning assistant node, not a
conversation-global set of provider call IDs. Repeated IDs in another task cannot
select its results. Task compaction counts the full currently projected observation
as task input; snapshot references remain visible alongside live content so they
can survive distillation. A document with only a body can also be compacted.

A pending compaction marker is committed with the current document state and
completed on reopen if a crash interrupts marker publication. This is recovery
metadata, not a document revision. Artifact loading verifies saved checksums.

Forking reconstructs the selected prefix's mutable task/shared state from its
successful control events rather than copying the source's latest root (which may
contain future decisions). It retains raw execution records instead of inheriting
later compaction, copies tree-owned artifacts and rebases their references. Failed
fork setup removes the partial target. Subagent initial roots exclude parent task
state; children have independent task documents and shared bodies. The public
`initial_root_value` continues to describe the initial system root, without the
internal task-state envelope.
