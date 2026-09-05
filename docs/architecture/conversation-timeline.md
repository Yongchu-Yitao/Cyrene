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
