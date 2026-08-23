import { workbenchServices } from "../../shared/runtime/services.jsx"

const {
  useState: useWbcState,
  useEffect: useWbcEffect,
  useLayoutEffect: useWbcLayoutEffect,
  useMemo: useWbcMemo,
  useRef: useWbcRef,
  useCallback: useWbcCallback,
} = React

function wbcT(key, fallback, params) {
  var i18n = workbenchServices.i18n()
  if (typeof i18n.t === "function") {
    var value = i18n.t(key, params, fallback)
    if (value && value !== key) return value
  }
  if (params && fallback) {
    Object.keys(params).forEach(function (name) {
      fallback = fallback.split("{" + name + "}").join(String(params[name]))
    })
  }
  return fallback || key
}

export {
  useWbcCallback,
  useWbcEffect,
  useWbcLayoutEffect,
  useWbcMemo,
  useWbcRef,
  useWbcState,
  wbcT,
}
