# 0.9.2.0 release structure review

The work-device release adds remote task admission, recovery and cleanup,
assistant memory editing, and a pane-anchored assistant menu. The baseline
changes below record only measured per-scope growth in the release changes.
Global thresholds and functional regression checks retain their existing values.

- Remote execution handles shared-workspace grants, model callbacks, durable
  task results, disconnect recovery and safe peer cache cleanup. The execute
  entry point holds the operation dispatch and recovery branches together.
- The memory editor drafts changes against their original sources and checks
  for intervening changes before applying edits.
- The assistant menu coordinates the device list, settings, memory, activity,
  keyboard focus, and the draggable pane grip. Existing chat effects retain
  their original scope identities by naming the new subscriptions.
- Pane resize and drag preserve edge-close previews, pending confirmation,
  pointer cancellation, and transcript position. Browser tab pickers retain
  a fallback when site icons fail to load.
- Conversation and model routing adds work-device integration, project memory
  access and compact tool-result previews.

| Scope | Metric | Previous limit | Reviewed value |
| --- | --- | ---: | ---: |
| `electron/main.js` | module_lines | 6487 | 6490 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer.jsx` | module_lines | 1579 | 1588 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer.jsx::WbcComposer` | lines | 1375 | 1382 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer.jsx::WbcComposer` | hooks | 51 | 52 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx` | module_lines | 3114 | 3124 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage` | lines | 3042 | 3051 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage` | hooks | 131 | 132 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage.renderActiveConversationCard` | decisions | 25 | 27 |
| `src/cyrene/workbench/webui/frontend/features/chat/pane-card-drag-controller.jsx::wbcFinishPaneCardGhost` | decisions | 26 | 30 |
| `src/cyrene/workbench/webui/frontend/features/layout/pointer-resize.mjs::startPointerResize` | lines | 121 | 149 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-chat.jsx::AssistantChat` | lines | 122 | 125 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-device-list.jsx::AssistantDeviceList` | decisions | 20 | 35 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-device-list.jsx::AssistantDeviceList.<anonymous>#5` | decisions | 20 | 25 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-identity-menu.jsx::AssistantIdentityMenu` | lines | 99 | 142 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-identity-menu.jsx::AssistantIdentityMenu` | decisions | 20 | 59 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-identity-menu.jsx::AssistantIdentityMenu` | hooks | 15 | 30 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-page.jsx::PersonalAssistantPage` | decisions | 20 | 23 |
| `src/cyrene/workbench/webui/frontend/features/shell/assistant-page.jsx::PersonalAssistantPage` | hooks | 15 | 16 |
| `src/cyrene/workbench/webui/frontend/platform/data-store.jsx::bootstrapData` | decisions | 20 | 21 |
| `src/cyrene/plugins/builtin/cyrene_memory/memory_editor.py::_draft_memory` | decisions | 20 | 27 |
| `src/cyrene/plugins/builtin/cyrene_memory/project_memory.py` | module_lines | 1769 | 1772 |
| `src/cyrene/plugins/builtin/cyrene_remote/commands.py` | module_lines | 2897 | 2907 |
| `src/cyrene/plugins/builtin/cyrene_remote/control.py` | module_lines | 2689 | 2705 |
| `src/cyrene/plugins/builtin/cyrene_remote/control.py::RemoteControlStore` | methods | 38 | 39 |
| `src/cyrene/plugins/builtin/cyrene_remote/status.py::_download_remote_file` | decisions | 20 | 21 |
| `src/cyrene/plugins/builtin/cyrene_remote/task_execution.py::execute` | decisions | 20 | 52 |
| `src/cyrene/plugins/builtin/cyrene_remote/task_execution.py::release_cache` | decisions | 20 | 22 |
| `src/cyrene/plugins/builtin/session_management/remote_host.py::RemoteAwareHost.refresh_task` | decisions | 20 | 25 |
| `src/cyrene/plugins/builtin/session_management/store.py::Store` | methods | 25 | 28 |
| `src/cyrene/plugins/model_router.py::route_model_call` | lines | 204 | 207 |
| `src/cyrene/plugins/model_router.py::route_model_call` | decisions | 33 | 34 |
| `src/cyrene/workbench/chat/chat_application.py::public_chat_light` | lines | 114 | 116 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py::ConversationRuntime._open_bridge` | lines | 145 | 151 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py::ConversationRuntime._open_bridge` | decisions | 44 | 49 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py` | module_lines | 1616 | 1617 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py::_SendOperation._load_project_and_model` | lines | 99 | 103 |

Remote execution reuses the assistant task harness on peers, including peers
with the assistant module disabled. The controller uses the Remote plugin retry
and file-transfer routines for result delivery. These five dependencies preserve
that shared behavior while the boundary checker continues to reject other growth.

- `src/cyrene/plugins/builtin/cyrene_remote/task_execution.py::cyrene.plugins.builtin.session_management.host::KIND`
- `src/cyrene/plugins/builtin/cyrene_remote/task_execution.py::cyrene.plugins.builtin.session_management.host::OWNER`
- `src/cyrene/plugins/builtin/cyrene_remote/task_execution.py::cyrene.plugins.builtin.session_management.host::WorkbenchHost`
- `src/cyrene/plugins/builtin/session_management/remote_host.py::cyrene.plugins.builtin.cyrene_remote.status::_download_remote_file`
- `src/cyrene/plugins/builtin/session_management/remote_host.py::cyrene.plugins.builtin.cyrene_remote.task_execution::retry_task_request`
