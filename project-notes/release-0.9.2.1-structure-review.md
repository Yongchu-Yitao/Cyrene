# 0.9.2.1 release structure review

This release integrates the current user Hook actions, plugin updates and inline
views, first-run setup, Settings redesign, home weather, and task recovery changes.
Only the per-scope increases listed below are accepted. Global thresholds, checker
implementations, and functional assertions retain their existing requirements.

Reviewed responsibilities:

- Hook actions validate typed messages, contexts and decisions, persist deferred
  actions, restore questions and history, and deliver pre-tool explanations before
  invoking tools. Batch review retains per-tool failure and permission handling.
- Plugin updates validate source identity, stage a complete replacement, preserve
  activation, verify lifecycle state, and recover old source after errors or restart.
  Inline views bind results to the recorded plugin execution and restore instances.
- First-run setup coordinates saved progress, verified model credentials, appearance,
  desktop preferences and personality. Settings coordinates navigation, connection
  drafts, ordered saves, errors, extension controls and release-specific notes.
- Home cards retain authored ordering, disclosures, weather observations, reduced
  motion and hidden-page behavior. Weather artwork separates rendering from data.
- Task delivery retains durable admission, provider retry deadlines and terminal
  answers. Local embedding migration matches generated duplicates conservatively.

Release preparation avoids broad refactors of these operation boundaries. Each
exception records the measured scope and metric, and permits no additional growth.
Functional and release gates must pass independently.

| Scope | Metric | Previous limit | Reviewed value |
| --- | --- | ---: | ---: |
| `src/cyrene/core/hook/actions.py::validate_action` | decisions | 20 | 34 |
| `src/cyrene/core/hook/actions.py::validate_action` | nesting | 5 | 6 |
| `src/cyrene/core/hook/registry.py` | module_lines | 1689 | 1705 |
| `src/cyrene/core/hook/registry.py::HookSet._run_pre_tool` | lines | 129 | 134 |
| `src/cyrene/core/hook/registry.py::HookSet._run_pre_tool` | decisions | 20 | 22 |
| `src/cyrene/core/hook/registry.py::HookSet._run_pre_tool_batch` | lines | 169 | 171 |
| `src/cyrene/core/hook/registry.py::HookSet._run_pre_tool_batch` | decisions | 36 | 37 |
| `src/cyrene/core/hook/registry.py::HookSet._worker_main` | lines | 117 | 118 |
| `src/cyrene/core/plugin/runtime.py` | module_lines | 1000 | 1016 |
| `src/cyrene/core/plugin/runtime.py::PluginRuntime.review_batch` | lines | 214 | 245 |
| `src/cyrene/core/plugin/runtime.py::PluginRuntime.review_batch` | decisions | 24 | 36 |
| `src/cyrene/core/plugin/runtime.py::PluginRuntime.review_batch` | nesting | 5 | 6 |
| `src/cyrene/core/plugin/session_plugins.py::SessionPlugins.reconcile_plugins` | decisions | 23 | 25 |
| `src/cyrene/core/session.py` | module_lines | 4919 | 5073 |
| `src/cyrene/core/session.py::AgentSession` | methods | 124 | 130 |
| `src/cyrene/core/session.py::AgentSession` | fields | 47 | 48 |
| `src/cyrene/core/session.py::AgentSession.__init__` | lines | 255 | 256 |
| `src/cyrene/core/session.py::AgentSession._pending_question_from_results` | decisions | 39 | 41 |
| `src/cyrene/core/session.py::AgentSession._advance` | lines | 248 | 252 |
| `src/cyrene/core/session.py::AgentSession._advance` | decisions | 59 | 60 |
| `src/cyrene/core/session.py::AgentSession._continue_tools` | lines | 193 | 199 |
| `src/cyrene/model/error_details.py::classify_model_error` | decisions | 39 | 41 |
| `src/cyrene/plugins/application.py::PluginApplicationHost` | methods | 32 | 33 |
| `src/cyrene/plugins/builtin/cyrene_cli/config_agent.py::configure_user_hook` | lines | 115 | 120 |
| `src/cyrene/plugins/builtin/cyrene_cli/hooks.py::CliHookService._dispatch` | decisions | 20 | 35 |
| `src/cyrene/plugins/builtin/cyrene_model/configuration_validation.py::_merge_legacy_local_embedding` | decisions | 20 | 26 |
| `src/cyrene/plugins/builtin/cyrene_plugin_development/tools.py` | module_lines | 1444 | 1496 |
| `src/cyrene/plugins/builtin/cyrene_plugin_development/tools.py::scaffold` | lines | 102 | 108 |
| `src/cyrene/plugins/builtin/cyrene_plugin_development/tools.py::scaffold` | decisions | 27 | 29 |
| `src/cyrene/plugins/builtin/session_management/store.py::Store` | methods | 28 | 29 |
| `src/cyrene/plugins/update.py::update_source` | decisions | 20 | 24 |
| `src/cyrene/workbench/chat/run_timeline.py::RunTimeline.apply` | decisions | 81 | 82 |
| `src/cyrene/workbench/core_adapter/checkpoint.py::context_checkpoint_from_nodes` | decisions | 20 | 21 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py::ConversationRuntime._open_bridge` | lines | 151 | 173 |
| `src/cyrene/workbench/core_adapter/conversation_runtime.py::ConversationRuntime._open_bridge` | decisions | 49 | 50 |
| `src/cyrene/workbench/http/plugins.py::register_plugin_routes` | lines | 265 | 297 |
| `src/cyrene/workbench/webui/frontend/entry/bootstrap.jsx::WorkbenchRoot` | lines | 155 | 163 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx::CardBlock` | decisions | 20 | 21 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-home-layout.jsx::AssistantHomeLayout` | decisions | 20 | 26 |
| `src/cyrene/workbench/webui/frontend/features/chat/card-weather.jsx::CardWeather` | decisions | 20 | 25 |
| `src/cyrene/workbench/webui/frontend/features/chat/conversation.jsx` | module_lines | 2592 | 2595 |
| `src/cyrene/workbench/webui/frontend/features/chat/conversation.jsx::wbcRenderHistoryMessage` | decisions | 131 | 134 |
| `src/cyrene/workbench/webui/frontend/features/chat/home-weather-painter.mjs::createWeatherPainter` | lines | 99 | 155 |
| `src/cyrene/workbench/webui/frontend/features/chat/home-weather-painter.mjs::createWeatherPainter.cloud` | decisions | 20 | 21 |
| `src/cyrene/workbench/webui/frontend/features/chat/home-weather-painter.mjs::createWeatherPainter.wind` | decisions | 20 | 25 |
| `src/cyrene/workbench/webui/frontend/features/settings/about.jsx::UpdateSection` | decisions | 99 | 123 |
| `src/cyrene/workbench/webui/frontend/features/settings/about.jsx::UpdateSection` | hooks | 16 | 17 |
| `src/cyrene/workbench/webui/frontend/features/settings/custom-plugins.jsx::usePluginRegistryMutations` | lines | 99 | 112 |
| `src/cyrene/workbench/webui/frontend/features/settings/custom-plugins.jsx::RemoveUserContribution` | decisions | 20 | 35 |
| `src/cyrene/workbench/webui/frontend/features/settings/custom-plugins.jsx::RegistryTile` | decisions | 20 | 30 |
| `src/cyrene/workbench/webui/frontend/features/settings/general.jsx::GeneralPanel` | lines | 286 | 291 |
| `src/cyrene/workbench/webui/frontend/features/settings/plugin-center-cli-hooks.jsx::CliHooksPanel` | lines | 169 | 198 |
| `src/cyrene/workbench/webui/frontend/features/settings/plugin-center-cli-hooks.jsx::CliHooksPanel` | decisions | 46 | 65 |
| `src/cyrene/workbench/webui/frontend/features/shell/module-presentation.jsx::useWorkbenchModulePresentation` | decisions | 53 | 55 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx` | module_lines | 2222 | 2240 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::ServicesOverview.row` | decisions | 20 | 25 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>.ServicesPage` | lines | 739 | 767 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>.ServicesPage` | decisions | 57 | 92 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>.ServicesPage` | hooks | 38 | 42 |
| `src/cyrene/workbench/webui/frontend/settings-overlay.jsx::SettingsPage` | hooks | 51 | 52 |
| `src/cyrene/workbench/webui/frontend/shared/i18n/catalog-en.jsx` | module_lines | 4351 | 4391 |
| `src/cyrene/workbench/webui/frontend/shared/i18n/catalog-zh.jsx` | module_lines | 4351 | 4391 |
| `src/cyrene/workbench/webui/frontend/shared/i18n/extension-translations.jsx` | module_lines | 1234 | 1301 |
| `src/cyrene/workbench/webui/frontend/workbench-profile.jsx::<anonymous>.WorkbenchProfilePage` | decisions | 93 | 94 |
| `src/cyrene/workbench/webui/frontend/workbench-welcome.jsx::OnboardingFlow` | lines | 99 | 185 |
| `src/cyrene/workbench/webui/frontend/workbench-welcome.jsx::OnboardingFlow` | decisions | 20 | 96 |
| `src/cyrene/workbench/webui/frontend/workbench-welcome.jsx::OnboardingFlow` | hooks | 15 | 19 |
| `src/cyrene/workbench/webui/frontend/workbench.jsx::WorkbenchApp` | lines | 460 | 461 |
| `src/cyrene/workbench/webui/frontend/workbench.jsx::WorkbenchApp` | decisions | 41 | 42 |
| `src/cyrene/workbench/webui/frontend/workbench.jsx::WorkbenchApp` | hooks | 42 | 44 |

## Additional changes reviewed on 2026-11-20

- Conversation cards bind persistence and submissions to their owning chat.
  Version snapshots and group membership validate owner and revision before use.
- Inline projection reconciles Markdown references, per-turn versions, schedule
  receipts and knowledge sources. Group navigation preserves sizing and focus.
- Pin changes preserve unsaved-content checks and update the owning chat layout.
  Bound inputs separate persisted task fields from device-local presentation state.
- Knowledge searches retain workspace and attachment provenance. Schedule receipts
  retain their original scope checks and report changes after repository success.
- PDF selection adjusts worker text runs while retaining source extraction and
  rendering, and fit-width observers release subscriptions on preview disposal.

The following exact per-scope increases are accepted for these additions. Global
limits and behavior assertions remain subject to their existing checks.

| Scope | Metric | Previous limit | Reviewed value |
| --- | --- | ---: | ---: |
| `src/cyrene/plugins/builtin/cyrene_cards/application.py::setup_application` | lines | 99 | 107 |
| `src/cyrene/plugins/builtin/cyrene_knowledge/search_library.py::handler` | decisions | 23 | 26 |
| `src/cyrene/plugins/builtin/cyrene_knowledge/service.py` | module_lines | 1215 | 1219 |
| `src/cyrene/plugins/builtin/cyrene_knowledge/store.py` | module_lines | 2005 | 2007 |
| `src/cyrene/plugins/builtin/cyrene_schedule/tools.py::edit_schedule` | decisions | 27 | 32 |
| `src/cyrene/workbench/webui/build-jsx.mjs::build` | lines | 186 | 189 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-card-bridge.mjs::installCardBridge.onBoundInput` | decisions | 20 | 26 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs::cardReferences.<anonymous>` | decisions | 20 | 32 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-card-visuals.jsx::CardEmbed.<anonymous>.receive` | decisions | 20 | 24 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx::CardField` | decisions | 20 | 25 |
| `src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx::WbcAssistantCard` | decisions | 20 | 33 |
| `src/cyrene/workbench/webui/frontend/features/chat/conversation-card-group.jsx::ConversationCardGroup` | lines | 99 | 142 |
| `src/cyrene/workbench/webui/frontend/features/chat/conversation-card-group.jsx::ConversationCardGroup` | decisions | 20 | 35 |
| `src/cyrene/workbench/webui/frontend/features/chat/conversation-card-group.jsx::ConversationCardGroup` | hooks | 15 | 20 |
| `src/cyrene/workbench/webui/frontend/features/chat/conversation.jsx` | module_lines | 2595 | 2598 |
| `src/cyrene/workbench/webui/frontend/features/chat/conversation.jsx::useWbcConversationProjection` | decisions | 31 | 35 |
| `src/cyrene/workbench/webui/frontend/features/chat/markdown-card-references.mjs::groupDirective` | decisions | 20 | 26 |
| `src/cyrene/workbench/webui/frontend/features/chat/markdown-card-references.mjs::markdownCardParts` | decisions | 20 | 28 |
| `src/cyrene/workbench/webui/frontend/features/chat/messages.jsx` | module_lines | 1861 | 1869 |
| `src/cyrene/workbench/webui/frontend/features/chat/messages.jsx::WbcAssistantMessage` | lines | 137 | 143 |
| `src/cyrene/workbench/webui/frontend/features/chat/messages.jsx::WbcAssistantMessage` | decisions | 67 | 70 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx` | module_lines | 3124 | 3151 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage` | lines | 3051 | 3078 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage` | hooks | 132 | 135 |
| `src/cyrene/workbench/webui/frontend/shared/i18n/catalog-en.jsx` | module_lines | 4391 | 4405 |
| `src/cyrene/workbench/webui/frontend/shared/i18n/catalog-zh.jsx` | module_lines | 4391 | 4405 |
| `src/cyrene/workbench/webui/frontend/shared/pdf/bridge.jsx::<anonymous>.pdfjsInstallSelectionSanitizer.sanitizePage` | decisions | 31 | 32 |

### Final card-panel state review

The latest pin panel change preserves the user's underlying panel preference and
suppresses the conversation panel only while pins occupy its space. Removing the
last pin restores that preference. Reviewed exact increments follow.

| Scope | Metric | Previous limit | Reviewed value |
| --- | --- | ---: | ---: |
| `src/cyrene/plugins/builtin/cyrene_cards/application.py::setup_application` | lines | 107 | 108 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage` | decisions | 219 | 220 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage.renderPaneCard` | decisions | 102 | 106 |
