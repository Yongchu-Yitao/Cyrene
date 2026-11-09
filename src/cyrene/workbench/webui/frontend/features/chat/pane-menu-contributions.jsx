import { pluginLocalizedField } from "../../platform/plugins.jsx"

export function paneMenuContributions(pluginPaneState) {
  return (Array.isArray(pluginPaneState.paneMenu) ? pluginPaneState.paneMenu : []).filter(Boolean).map(function (contribution) {
    var disabled = paneMenuDisabled(contribution, pluginPaneState);
    var availableKey = String(contribution && contribution.available_values_state_key || "");
    var availableValues = availableKey && Array.isArray(pluginPaneState[availableKey])
      ? pluginPaneState[availableKey].map(String) : null;
    return Object.assign({}, contribution, {
      disabled: disabled,
      label: pluginLocalizedField(contribution, "label") || String(contribution && contribution.id || "Settings"),
      fields: (Array.isArray(contribution && contribution.fields) ? contribution.fields : []).map(function (field) {
        return paneMenuField(field, pluginPaneState);
      }),
      options: (Array.isArray(contribution && contribution.options) ? contribution.options : []).filter(function (option) {
        return !availableValues || availableValues.indexOf(String(option && option.value || "")) >= 0;
      }).map(function (option) {
        return Object.assign({}, option, { label: pluginLocalizedField(option, "label") || String(option && option.value || "") });
      }),
    });
  });
}

function paneMenuField(field, pluginPaneState) {
  var rawValue = pluginPaneState[String(field && field.state_key || "")];
  var valueLabel = (Array.isArray(field && field.value_labels) ? field.value_labels : []).find(function (candidate) {
    return String(candidate && candidate.value || "") === String(rawValue == null ? "" : rawValue);
  });
  var valueSuffix = pluginLocalizedField(field, "suffix");
  return Object.assign({}, field, {
    label: pluginLocalizedField(field, "label") || String(field && field.state_key || ""),
    tone: String(valueLabel && valueLabel.tone || ""),
    value: paneMenuValue(field, rawValue, valueLabel, valueSuffix),
  });
}

function paneMenuDisabled(contribution, pluginPaneState) {
  var requiredState = String(contribution && contribution.requires_state || "");
  var sessionKey = String(contribution.session_key || "session_id");
  var disabled = Boolean(
    (requiredState && !pluginPaneState[requiredState])
    || (contribution.requires_session === true && !pluginPaneState[sessionKey])
  );
  return disabled;
}

function paneMenuValue(field, rawValue, valueLabel, valueSuffix) {
  return valueLabel
    ? pluginLocalizedField(valueLabel, "label")
    : String(rawValue == null || rawValue === ""
      ? pluginLocalizedField(field, "empty_label") || "—"
      : rawValue) + (rawValue == null || rawValue === "" ? "" : valueSuffix);
}
