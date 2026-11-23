# 0.9.2.3 structural review

The backup interface adds six translation entries to each existing language
catalog for local backup actions, export scope, and restore confirmation.
Both catalogs retain their existing ownership and now contain 4461 lines
(previously 4455). Only those two module-line entries are updated; global
limits and all other scoped budgets remain unchanged.

Home-result path matching is extracted into a pure helper without changing
matching order, path normalization, ambiguity handling, or task fallback.
This keeps the resolver within its existing decision budget.

Python structure and plugin dependency boundaries retain their existing budgets.

## Latest-content review — 2026-11-23

The release additionally includes in-pane card fullscreen, malformed model-action
recovery, task-owned persona isolation, task-notification memory guidance, and
live-workstream capacity reminders. Existing runtime behavior is retained as
submitted. No runtime changes are made to satisfy tests or structural checks.

The correction payload preserves the invalid-name marker and requests a complete
new batch before any tools execute. The router recognizes that exact response
failure so it returns to session correction rather than replaying unchanged
history against another model. The send route retains memory guidance specifically
for task notifications without enabling notification memory writes or archives.
The card host adds its fullscreen hook/control, while the embed receiver ignores
inline-height feedback during fullscreen. These branches stay with their existing
owners; the standalone fullscreen lifecycle lives in its new controller module.
Only the measured scoped entries below are updated. Global limits, checkers,
plugin boundaries, and all unrelated scoped budgets remain unchanged.

| Scope | Metric | Previous | Reviewed |
| --- | --- | --- | --- |
| `src/cyrene/core/session.py` | module_lines | 5073 | 5081 |
| `src/cyrene/core/session.py::_tool_call_correction_payload` | decisions | 30 | 33 |
| `src/cyrene/plugins/model_router.py::route_model_call` | lines | 207 | 214 |
| `src/cyrene/plugins/model_router.py::route_model_call` | decisions | 34 | 37 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py` | module_lines | 1617 | 1620 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-card-visuals.jsx::CardEmbed.<anonymous>.receive` | decisions | 24 | 25 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx::WbcAssistantCard` | decisions | 33 | 35 |
