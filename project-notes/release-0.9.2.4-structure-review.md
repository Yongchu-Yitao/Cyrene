# 0.9.2.4 structural review

This release includes the submitted card fullscreen and stop/restart behavior.
No runtime behavior is changed to satisfy validation. CardEmbed owns its running
and stopped rendering plus the page-level controls. WbcAssistantCard retains the
ordinary-card fullscreen control and passes group eligibility to embedded pages.
The new control component owns the fullscreen and stop buttons, translated labels,
capability and eligibility checks, and stopped-state disabling. The fullscreen
controller remains separate from rendering and preloads with the card interface.

Only the measured decision entries below are reviewed. Existing global limits,
checkers, unrelated scoped budgets, Python budgets and plugin boundaries remain
unchanged.

| Scope | Metric | Previous budget | Reviewed |
| --- | --- | --- | --- |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-card-visuals.jsx::CardEmbed` | decisions | 20 | 23 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx::WbcAssistantCard` | decisions | 35 | 38 |
| `src/cyrene/workbench/webui/frontend/features/chat/card-fullscreen-button.jsx::CardFullscreenButton` | decisions | 20 | 27 |
