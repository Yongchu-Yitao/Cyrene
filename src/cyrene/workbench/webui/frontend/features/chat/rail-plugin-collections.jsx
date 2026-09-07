import { useWbcEffect, useWbcState, wbcErrorText } from "../../workbench-chat.jsx"
import { PluginFrontendService } from "../../platform/plugins.jsx"

export function pluginToolKey(tool) {
  return String(tool && tool.pack_id || "") + ":" + String(tool && (tool.id || tool.view) || "");
}

// Own collection data, loading/errors and refresh subscriptions together.
export function useRailPluginCollections(projectId, pluginTools, projectToolView) {
  var [pluginCollections, setPluginCollections] = useWbcState({});
  var [pluginCollectionLoading, setPluginCollectionLoading] = useWbcState({});
  var [pluginCollectionErrors, setPluginCollectionErrors] = useWbcState({});
  function loadPluginCollection(tool) {
    var packId = String(tool && tool.pack_id || "");
    var method = String(tool && tool.items_method || "");
    var key = pluginToolKey(tool);
    if (!packId || !method || pluginCollectionLoading[key]) return Promise.resolve();
    setPluginCollectionLoading(function (current) { return Object.assign({}, current, { [key]: true }); });
    setPluginCollectionErrors(function (current) { return Object.assign({}, current, { [key]: "" }); });
    return PluginFrontendService.call(packId, method, {}, projectId).then(function (result) {
      setPluginCollections(function (current) {
        return Object.assign({}, current, { [key]: Array.isArray(result && result.cards) ? result.cards : [] });
      });
    }).catch(function (error) {
      setPluginCollectionErrors(function (current) {
        return Object.assign({}, current, { [key]: wbcErrorText(error) });
      });
    }).finally(function () {
      setPluginCollectionLoading(function (current) { return Object.assign({}, current, { [key]: false }); });
    });
  }
  useWbcEffect(function () {
    var events = window.CyreneUI && window.CyreneUI.events;
    if (!events || typeof events.subscribe !== "function") return undefined;
    return events.subscribe(function (event) {
      if (!event || ["remote_desktop_cards_changed", "remote_desktop_session_changed"].indexOf(String(event.type || "")) < 0) return;
      pluginTools.filter(function (tool) { return String(tool && tool.presentation || "") === "collection"; }).forEach(loadPluginCollection);
    });
  }, [pluginTools, projectId]);
  useWbcEffect(function () {
    pluginTools.filter(function (tool) {
      return String(tool && tool.presentation || "") === "collection" && projectToolView === "plugin:" + pluginToolKey(tool);
    }).forEach(loadPluginCollection);
  }, [pluginTools, projectId, projectToolView]);
  return { pluginCollections, pluginCollectionLoading, pluginCollectionErrors, loadPluginCollection };
}
