"""Runtime settings store — persists user preferences that can be changed via Web UI.

Delegates to the encrypted config_store for all read/write operations.
"""

from cyrene.platform import config_store as _store

# Re-export for callers that import directly from settings_store
get = _store.get_setting
set_ = _store.set_setting
get_all = _store.get_all_settings
reset_all = _store.reset_all
get_revision = _store.get_settings_revision
update_atomic = _store.update_settings_atomic
update_settings_and_env_atomic = _store.update_settings_and_env_atomic
is_plugin_enabled = _store.is_plugin_enabled
get_enabled_plugins = _store.get_enabled_plugins
save_enabled_plugins = _store.save_enabled_plugins
is_plugin_pack_enabled = _store.is_plugin_pack_enabled
get_enabled_plugin_packs = _store.get_enabled_plugin_packs
save_enabled_plugin_packs = _store.save_enabled_plugin_packs
get_write_permission_mode = _store.get_write_permission_mode
set_write_permission_mode = _store.set_write_permission_mode
