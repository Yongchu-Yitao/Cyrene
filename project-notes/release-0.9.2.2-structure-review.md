# 0.9.2.2 release structure review

This release integrates reply preferences, private assistant follow-ups, cloud
speech, mid-run attachments, home result viewing and Settings navigation changes.
Only the exact per-scope growth listed below is accepted. Global thresholds and
functional assertions retain their existing requirements.

Reviewed responsibilities:

- Follow-up reporting validates current-run evidence and defers private delivery
  until the original assistant conversation can resume. Shared card model selection
  and managed-task delivery preserve existing ownership and runtime behavior.
- Speech settings validate local and cloud engine selection, credentials, voice
  identities and model availability. Recorders preserve the originating conversation.
- Mid-run attachments are resolved against managed storage or library records and
  remain paired with the request across retries and context transitions.
- Settings coordinate navigation, loading states, model tests, credential errors,
  speech downloads and preference saves. Newly inserted callbacks have explicit
  names so existing anonymous scopes retain their measured identities.
- Home results resolve the original artifact or card, and pane rendering retains
  its existing close and confirmation behavior.

| Scope | Metric | Previous limit | Reviewed value |
| --- | --- | ---: | ---: |
| `src/cyrene/plugins/builtin/cyrene_proactive/revisit.py::report_from` | decisions | 20 | 21 |
| `src/cyrene/plugins/builtin/cyrene_voice/engine.py::update_settings` | decisions | 28 | 36 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py::_SendOperation` | fields | 29 | 30 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py::_SendOperation._load_chat` | lines | 106 | 120 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py::_SendOperation._load_chat` | decisions | 34 | 39 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_stream_routes.py::register_run_stream_routes` | lines | 128 | 138 |
| `src/cyrene/workbench/http/workbench/chat_routes/send_input.py::append_user_message` | decisions | 26 | 28 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer-voice.jsx::useWbcComposerVoice` | lines | 119 | 129 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer.jsx` | module_lines | 1588 | 1592 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer.jsx::WbcComposer` | lines | 1382 | 1386 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer.jsx::WbcComposer` | decisions | 519 | 525 |
| `src/cyrene/workbench/webui/frontend/features/chat/composer.jsx::WbcComposer.submit` | decisions | 20 | 24 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage.renderPaneCard` | decisions | 106 | 107 |
| `src/cyrene/workbench/webui/frontend/features/chat/page.jsx::WorkbenchChatPage.renderPaneCard` | nesting | 11 | 12 |
| `src/cyrene/workbench/webui/frontend/features/chat/split-pane.jsx::WbcSideAgentTab` | lines | 242 | 243 |
| `src/cyrene/workbench/webui/frontend/features/settings/agents.jsx::AgentsPanel` | decisions | 20 | 23 |
| `src/cyrene/workbench/webui/frontend/features/settings/capabilities.jsx::CapabilitiesPanel` | decisions | 114 | 151 |
| `src/cyrene/workbench/webui/frontend/features/settings/media.jsx` | module_lines | 1034 | 1035 |
| `src/cyrene/workbench/webui/frontend/features/settings/voice-controls.jsx::VoiceMethods.<anonymous>#2` | decisions | 20 | 22 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx` | module_lines | 2240 | 2241 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::renderModelDetailPane` | nesting | 5 | 6 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>` | lines | 1977 | 1991 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>.ModelIdCombobox` | decisions | 46 | 48 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>.ServicesPage` | lines | 767 | 797 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>.ServicesPage` | decisions | 92 | 114 |
| `src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx::<anonymous>.ServicesPage` | hooks | 42 | 48 |
| `src/cyrene/workbench/webui/frontend/settings-overlay.jsx::SettingsPage` | lines | 712 | 722 |
| `src/cyrene/workbench/webui/frontend/settings-overlay.jsx::SettingsPage` | hooks | 52 | 54 |
| `src/cyrene/workbench/webui/frontend/shared/i18n/catalog-en.jsx` | module_lines | 4405 | 4455 |
| `src/cyrene/workbench/webui/frontend/shared/i18n/catalog-zh.jsx` | module_lines | 4405 | 4455 |

The follow-up plugin reuses the card preparation model selector and the managed
assistant wake queue. These exact dependencies keep selection and durable delivery
in their existing owners. Other boundary growth remains checked.

- `src/cyrene/plugins/builtin/cyrene_proactive/revisit.py::cyrene.plugins.builtin.cyrene_cards.home_preparation::home_model_identity`
- `src/cyrene/plugins/builtin/cyrene_proactive/revisit.py::cyrene.plugins.builtin.session_management.followup::long_enabled`
- `src/cyrene/plugins/builtin/cyrene_proactive/revisit.py::cyrene.plugins.builtin.session_management.followup::schedule`
