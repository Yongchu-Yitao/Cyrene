# 0.9.0.1 release structure review

The graph feature in c6886745 and a97ad41b introduced the following measured
structure growth without updating the ratcheted baselines. This release records
an explicit, scoped review of that existing implementation. Every accepted value
was compared with a97ad41b and is no greater than its existing value. Global
limits, check scripts, behavioral assertions, and platform smoke gates are unchanged.

Reviewed responsibilities:

- Context path selection retains committed replies across historical retries.
- Projection and task restoration apply branch-local revisions and exclude draft checkpoints.
- Session mounts and Workbench records preserve branch drafts and revision state.
- Graph assembly includes history, context groups, task documents, and named tool steps.
- Materialization validates boundaries, pending requests, complete tool groups,
  source revisions, and operation identities before copying. It retains source
  history, rebases attachments, checkpoints drafts, records lineage, and cleans
  up failed targets. Its large operation body remains a maintenance concern;
  splitting its transaction during release preparation would expand regression risk.
- Conversation forks preserve selected boundaries, task state, and attachment staging.
- Graph actions and inspection retain their capability guards, unsaved-edit
  protection, model-input inspection, and unavailable-statistic presentation.

These exact per-scope exceptions recognize the existing feature; they do not
permit further growth. Functional regression coverage and all CI/release gates
must still pass independently before the release is tagged.

| Scope | Metric | Previous limit | Reviewed value |
| --- | --- | ---: | ---: |
| `src/cyrene/core/context/paths.py::select_context_leaf` | decisions | 20 | 27 |
| `src/cyrene/core/context/projection.py::project_model_messages` | lines | 165 | 196 |
| `src/cyrene/core/context/projection.py::project_model_messages` | decisions | 58 | 74 |
| `src/cyrene/core/context/store.py` | module_lines | 1086 | 1088 |
| `src/cyrene/core/context/tasks.py::fork_task_state` | decisions | 25 | 28 |
| `src/cyrene/core/session.py::AgentSession` | methods | 123 | 124 |
| `src/cyrene/core/session.py::AgentSession.__init__` | lines | 254 | 255 |
| `src/cyrene/workbench/chat/chat_application.py` | module_lines | 1578 | 1579 |
| `src/cyrene/workbench/chat/chat_application.py::public_chat_light` | lines | 113 | 114 |
| `src/cyrene/workbench/chat/context_graph_service.py::ContextGraphService.graph` | decisions | 20 | 49 |
| `src/cyrene/workbench/chat/context_graph_service.py::ContextGraphService._materialize` | lines | 99 | 212 |
| `src/cyrene/workbench/chat/context_graph_service.py::ContextGraphService._materialize` | decisions | 20 | 139 |
| `src/cyrene/workbench/chat/conversation_context_service.py` | module_lines | 2068 | 2071 |
| `src/cyrene/workbench/chat/conversation_context_service.py::AgentContextRepository._read_tree` | lines | 195 | 197 |
| `src/cyrene/workbench/core_adapter/bridge.py` | module_lines | 1116 | 1123 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py` | module_lines | 1000 | 1008 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py::ConversationRuntime.fork_context` | lines | 105 | 124 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py::ConversationRuntime.fork_context` | decisions | 20 | 34 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py::ConversationRuntime.send.operate` | decisions | 24 | 27 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py` | module_lines | 1611 | 1612 |
| `src/cyrene/workbench/webui/frontend/features/chat/context-graph.jsx::GraphNodeActions` | decisions | 20 | 33 |
| `src/cyrene/workbench/webui/frontend/features/chat/context-graph.jsx::GraphInspector` | decisions | 20 | 38 |
| `src/cyrene/workbench/webui/frontend/features/chat/context-indicator.jsx::WbcContextTechnicalDetails` | decisions | 29 | 37 |
