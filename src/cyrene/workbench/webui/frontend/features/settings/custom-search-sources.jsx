import { useStateSt, Toggle } from "./shared.jsx"

const EMPTY_SOURCE = {
  name: "", type: "json", method: "GET", search_url: "", request_body: "",
  results_path: "results", title_path: "title", url_path: "url", content_path: "content",
  url_prefix: "", auth_header: "", auth_value: "", content_type: "application/json",
};

function CustomSourceForm(p) {
  var [draft, setDraft] = useStateSt({ ...EMPTY_SOURCE, ...p.source, auth_value: "" });
  function field(key, label, placeholder, options) {
    return React.createElement("label", { className: "wb-custom-source-field", key: key },
      React.createElement("span", null, p.t("settings.customSource." + label)),
      React.createElement(options?.multiline ? "textarea" : "input", {
        className: "wb-input", value: draft[key], placeholder: placeholder,
        type: options?.secret ? "password" : "text", autoComplete: "off",
        required: options?.required, rows: options?.multiline ? 3 : undefined,
        onChange: function (event) { setDraft({ ...draft, [key]: event.target.value }); },
      }));
  }
  function select(key, label, choices) {
    return React.createElement("label", { className: "wb-custom-source-field" },
      React.createElement("span", null, p.t("settings.customSource." + label)),
      React.createElement("select", {
        className: "wb-select", value: draft[key],
        onChange: function (event) {
          var value = event.target.value;
          var next = { ...draft, [key]: value };
          if (key === "type") Object.assign(next, value === "json"
            ? { results_path: "results", title_path: "title", url_path: "url", content_path: "content" }
            : { results_path: "//article", title_path: ".//h2", url_path: ".//a/@href", content_path: ".//p" });
          setDraft(next);
        },
      }, choices.map(function (choice) { return React.createElement("option", { value: choice[0], key: choice[0] }, choice[1]); })));
  }
  var json = draft.type === "json";
  return React.createElement("form", {
    className: "wb-custom-source-form",
    onSubmit: function (event) { event.preventDefault(); p.onSave(draft); },
  },
    React.createElement("div", { className: "wb-custom-source-grid" },
      field("name", "name", p.t("settings.customSource.namePlaceholder"), { required: true }),
      select("type", "type", [["json", "JSON API"], ["html", "HTML / XPath"]])),
    field("search_url", "url", "https://example.com/search?q={query}", { required: true }),
    React.createElement("small", { className: "wb-custom-source-hint" }, p.t("settings.customSource.urlHint")),
    React.createElement("div", { className: "wb-custom-source-grid" },
      select("method", "method", [["GET", "GET"], ["POST", "POST"]]),
      draft.method === "POST" && select("content_type", "contentType", [["application/json", "JSON"], ["application/x-www-form-urlencoded", "Form URL encoded"]])),
    draft.method === "POST" && field("request_body", "body", '{{"q": "{query}"}}', { multiline: true }),
    React.createElement("div", { className: "wb-custom-source-section" }, p.t("settings.customSource.mapping")),
    React.createElement("small", { className: "wb-custom-source-hint" }, p.t("settings.customSource." + (json ? "jsonHint" : "htmlHint"))),
    React.createElement("div", { className: "wb-custom-source-grid" },
      field("results_path", "results", json ? "data/results" : "//article"),
      field("title_path", "title", json ? "title" : ".//h2", { required: true }),
      field("url_path", "link", json ? "url" : ".//a/@href", { required: true }),
      field("content_path", "snippet", json ? "description" : ".//p")),
    json && field("url_prefix", "prefix", "https://example.com"),
    React.createElement("details", { className: "wb-custom-source-auth" },
      React.createElement("summary", null, p.t("settings.customSource.auth")),
      React.createElement("div", { className: "wb-custom-source-grid" },
        field("auth_header", "header", "Authorization / X-API-Key"),
        field("auth_value", "secret", draft.auth_configured ? p.t("settings.searchConfigured") : "Bearer …", { secret: true })),
      draft.auth_configured && React.createElement("label", { className: "wb-custom-source-clear" },
        React.createElement("input", { type: "checkbox", checked: !!draft.clear_auth, onChange: function (event) { setDraft({ ...draft, clear_auth: event.target.checked, auth_value: "" }); } }),
        p.t("settings.clearStoredKey"))),
    React.createElement("div", { className: "wb-custom-source-actions" },
      React.createElement("button", { type: "button", className: "wb-btn muted", onClick: p.onCancel }, p.t("settings.customSource.cancel")),
      React.createElement("button", { type: "submit", className: "wb-btn" }, p.t("settings.customSource.save"))));
}

function CustomSearchSources(p) {
  var [editing, setEditing] = useStateSt(null);
  var sources = p.provider.custom_sources || [];
  function commit(next, engines) {
    p.updateProvider("simplexng", { custom_sources: next, engines: engines });
  }
  return React.createElement("section", { className: "wb-custom-sources" },
    React.createElement("div", { className: "wb-custom-source-toolbar" },
      React.createElement("span", null, p.t("settings.customSource.heading")),
      p.provider.custom_sources_supported !== false && !editing && React.createElement("button", {
        type: "button", className: "wb-btn muted", onClick: function () { setEditing({ ...EMPTY_SOURCE, id: "custom-" + crypto.randomUUID().replaceAll("-", "") }); },
      }, "+ " + p.t("settings.customSource.add"))),
    React.createElement("small", { className: "wb-custom-source-hint" }, p.t("settings.customSource." + (p.provider.custom_sources_supported === false ? "external" : "hint"))),
    p.saving && React.createElement("small", { className: "wb-custom-source-hint", role: "status" }, p.t("settings.customSource.saving")),
    p.config?.runtime_warning && React.createElement("small", { className: "wb-search-save-error", role: "alert" }, p.t("settings.customSource.restartFailed")),
    sources.map(function (source) {
      var on = p.provider.engines == null || p.provider.engines.includes(source.id);
      return React.createElement("div", { className: "wb-custom-source-row", key: source.id },
        React.createElement("div", { className: "wb-custom-source-info" },
          React.createElement("span", null, source.name),
          React.createElement("small", null, source.type === "json" ? "JSON API" : "HTML / XPath")),
        React.createElement("div", { className: "wb-custom-source-actions" },
          React.createElement("button", { type: "button", className: "wb-btn muted", onClick: function () { setEditing(source); } }, p.t("settings.customSource.edit")),
          React.createElement("button", { type: "button", className: "wb-btn muted", onClick: function () {
            commit(sources.filter(function (row) { return row.id !== source.id; }), p.provider.engines == null ? null : p.provider.engines.filter(function (id) { return id !== source.id; }));
            if (editing?.id === source.id) setEditing(null);
          } }, p.t("settings.customSource.remove")),
          Toggle(on, function () {
            var enabled = p.enabledEngines;
            p.updateProvider("simplexng", { engines: on ? enabled.filter(function (id) { return id !== source.id; }) : enabled.concat(source.id) });
          }, !p.catalogReady && p.provider.engines == null, source.name)));
    }),
    editing && React.createElement(CustomSourceForm, {
      key: editing.id, source: editing, t: p.t,
      onCancel: function () { setEditing(null); },
      onSave: function (source) {
        var exists = sources.some(function (row) { return row.id === source.id; });
        var next = exists ? sources.map(function (row) { return row.id === source.id ? source : row; }) : sources.concat(source);
        var engines = p.provider.engines;
        commit(next, engines == null ? null : exists ? engines : engines.concat(source.id));
        setEditing(null);
      },
    }));
}

export { CustomSearchSources };
