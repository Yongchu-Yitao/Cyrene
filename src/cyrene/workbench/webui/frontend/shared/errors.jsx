import { workbenchServices } from "./runtime/services.jsx"

function wbErrorText(err) {
  try {
    var api = workbenchServices.api();
    if (api && typeof api.errorText === "function") return api.errorText(err);
  } catch (e) {}
  return String((err && err.message) || err || "");
}


export { wbErrorText }
