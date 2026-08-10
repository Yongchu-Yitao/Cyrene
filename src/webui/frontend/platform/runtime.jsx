// Cyrene Workbench platform registry.
//
// The UI ships as ordered non-module scripts for compatibility with the
// current React/Electron build. Cross-script services must be registered here
// instead of creating new ad-hoc window globals.
(function (root) {
  "use strict";

  var existing = root.CyreneUI;
  if (existing && existing.version !== 1) {
    throw new Error("Unsupported CyreneUI platform registry version");
  }

  var services = existing && existing.services
    ? existing.services
    : Object.create(null);

  function register(name, service) {
    var key = String(name || "").trim();
    if (!key) throw new Error("CyreneUI service name is required");
    if (services[key] && services[key] !== service) {
      throw new Error("CyreneUI service already registered: " + key);
    }
    services[key] = service;
    return service;
  }

  function requireService(name) {
    var key = String(name || "").trim();
    if (!services[key]) {
      throw new Error("CyreneUI service is not registered: " + key);
    }
    return services[key];
  }

  root.CyreneUI = {
    version: 1,
    services: services,
    register: register,
    require: requireService,
  };
})(window);
