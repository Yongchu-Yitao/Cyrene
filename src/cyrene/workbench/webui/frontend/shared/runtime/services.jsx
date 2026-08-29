// The platform registry is the legacy composition root. Feature modules import
// this typed adapter instead of reaching through `window` themselves, keeping
// dependency discovery and future constructor/prop injection in one place.
function resolveWorkbenchService(name) {
  var platform = window.CyreneUI;
  if (!platform || typeof platform.require !== "function") {
    throw new Error("CyreneUI service registry is not ready: " + name);
  }
  return platform.require(name);
}

var workbenchServices = Object.freeze({
  api: function () { return resolveWorkbenchService("api"); },
  browser: function () { return resolveWorkbenchService("browser"); },
  browserOverlays: function () { return resolveWorkbenchService("browser-overlays"); },
  codeHighlight: function () { return resolveWorkbenchService("codeHighlight"); },
  chat: function () { return resolveWorkbenchService("chat"); },
  create: function () { return resolveWorkbenchService("create"); },
  data: function () { return resolveWorkbenchService("data"); },
  diff: function () { return resolveWorkbenchService("diff"); },
  events: function () { return resolveWorkbenchService("events"); },
  feedback: function () { return resolveWorkbenchService("feedback"); },
  format: function () { return resolveWorkbenchService("format"); },
  i18n: function () { return resolveWorkbenchService("i18n"); },
  library: function () { return resolveWorkbenchService("library"); },
  markdown: function () { return resolveWorkbenchService("markdown"); },
  memory: function () { return resolveWorkbenchService("memory"); },
  model: function () { return resolveWorkbenchService("model"); },
  modelSettings: function () { return resolveWorkbenchService("model-settings"); },
  navigation: function () { return resolveWorkbenchService("navigation"); },
  pdf: function () { return resolveWorkbenchService("pdf"); },
  plugins: function () { return resolveWorkbenchService("plugins"); },
  profile: function () { return resolveWorkbenchService("profile"); },
  readiness: function () { return resolveWorkbenchService("readiness"); },
  resources: function () { return resolveWorkbenchService("resources"); },
  schedule: function () { return resolveWorkbenchService("schedule"); },
  search: function () { return resolveWorkbenchService("search"); },
  settings: function () { return resolveWorkbenchService("settings"); },
  settingsIndex: function () { return resolveWorkbenchService("settings-index"); },
  shell: function () { return resolveWorkbenchService("shell"); },
  shortcuts: function () { return resolveWorkbenchService("shortcuts"); },
  terminal: function () { return resolveWorkbenchService("terminal"); },
  tour: function () { return resolveWorkbenchService("tour"); },
  tourHost: function () { return resolveWorkbenchService("tour-host"); },
  uiSurface: function () { return resolveWorkbenchService("uiSurface"); },
  welcome: function () { return resolveWorkbenchService("welcome"); },
});

export { resolveWorkbenchService, workbenchServices }
