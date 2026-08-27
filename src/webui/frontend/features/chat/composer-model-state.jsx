import { workbenchServices } from "../../shared/runtime/services.jsx"
import {
  WorkbenchChatModel,
  useWbcEffect,
  useWbcState,
  wbcReasoningEffortForModel,
} from "../../workbench-chat.jsx"

function useWbcComposerAgentCatalog(enabled) {
  var [options, setOptions] = useWbcState([])
  var [loaded, setLoaded] = useWbcState(false)

  useWbcEffect(function () {
    if (!enabled || loaded || !WorkbenchChatModel.listAgents) return
    var cancelled = false
    WorkbenchChatModel.listAgents().then(function (list) {
      if (cancelled) return
      setOptions(Array.isArray(list) ? list : [])
      setLoaded(true)
    }).catch(function () {
      if (!cancelled) setLoaded(true)
    })
    return function () { cancelled = true }
  }, [loaded, enabled])

  useWbcEffect(function () {
    function refresh() {
      if (enabled) setLoaded(false)
    }
    window.addEventListener("cyrene:agents-changed", refresh)
    return function () { window.removeEventListener("cyrene:agents-changed", refresh) }
  }, [enabled])

  return { loaded: loaded, options: options }
}

function useWbcComposerAgentConfig(chatId, chat, managed) {
  var [options, setOptions] = useWbcState([])
  var [values, setValues] = useWbcState({})
  var [loading, setLoading] = useWbcState(false)

  useWbcEffect(function () {
    if (!managed || !chatId || !WorkbenchChatModel.getAgentConfigOptions) {
      setOptions([])
      setValues({})
      setLoading(false)
      return undefined
    }
    var cancelled = false
    setLoading(true)
    WorkbenchChatModel.getAgentConfigOptions(chatId).then(function (payload) {
      if (cancelled) return
      setOptions(Array.isArray(payload.configOptions) ? payload.configOptions : [])
      setValues(payload.values && typeof payload.values === "object" ? payload.values : {})
      setLoading(false)
    }).catch(function () {
      if (cancelled) return
      setOptions([])
      setLoading(false)
    })
    return function () { cancelled = true }
  }, [chatId, managed])

  useWbcEffect(function () {
    if (!chat || !managed) return
    if (Array.isArray(chat.agentConfigOptions) && chat.agentConfigOptions.length) setOptions(chat.agentConfigOptions)
    if (chat.agentConfigValues && typeof chat.agentConfigValues === "object") setValues(chat.agentConfigValues)
  }, [chat && chat.agentConfigOptions, chat && chat.agentConfigValues, managed])

  return {
    loading: loading,
    options: options,
    setOptions: setOptions,
    setValues: setValues,
    values: values,
  }
}

function useWbcComposerConfiguredModels(chatId, chat, managed) {
  var [models, setModels] = useWbcState([])
  var [selectedId, setSelectedId] = useWbcState("")
  var [reasoningEffort, setReasoningEffort] = useWbcState(function () {
    return String(chat && chat.reasoningEffort || "").trim().toLowerCase()
  })

  useWbcEffect(function () {
    if (managed) return undefined
    var cancelled = false
    function loadConfiguredModels() {
      return workbenchServices.api().json("/api/settings/model-config", { toast: false }).then(function (payload) {
        var options = Array.isArray(payload.selectable_models) ? payload.selectable_models : []
        var needsCodexCatalog = options.some(function (item) { return String(item.provider || "") === "codex_oauth" })
        var catalogRequest = needsCodexCatalog
          ? workbenchServices.api().json("/api/settings/openai-oauth", { toast: false }).catch(function () { return {} })
          : Promise.resolve({})
        return catalogRequest.then(function (catalog) {
          if (cancelled) return
          var codexModels = Array.isArray(catalog.models) ? catalog.models : []
          options = options.map(function (item) {
            if (String(item.provider || "") !== "codex_oauth") return item
            var match = codexModels.find(function (entry) {
              var id = String(entry.model || entry.id || entry.slug || "").trim()
              return id === String(item.model || "").trim()
            })
            return match ? Object.assign({}, item, {
              supportedReasoningEfforts: match.supportedReasoningEfforts || match.supported_reasoning_efforts || [],
              defaultReasoningEffort: match.defaultReasoningEffort || match.default_reasoning_effort || "",
            }) : item
          })
          setModels(options)
          var chatSelection = String(chat && (chat.modelSelectionId || chat.model) || "").trim()
          var selected = options.find(function (item) {
            return chatSelection && [String(item.id || ""), String(item.model || ""), String(item.name || "")].indexOf(chatSelection) >= 0
          }) || options.find(function (item) {
            return String(item.id || "") === String(payload.active || "")
          }) || options[0]
          if (selected) {
            setSelectedId(String(selected.id || selected.model || ""))
            setReasoningEffort(wbcReasoningEffortForModel(selected, chat && chat.reasoningEffort))
          } else {
            setSelectedId("")
            setReasoningEffort("")
          }
        })
      }).catch(function () {
        if (!cancelled) setModels([])
      })
    }
    function onChanged() { loadConfiguredModels() }
    loadConfiguredModels()
    window.addEventListener("cyrene:model-configuration-changed", onChanged)
    window.addEventListener("cyrene:plugins-changed", onChanged)
    return function () {
      cancelled = true
      window.removeEventListener("cyrene:model-configuration-changed", onChanged)
      window.removeEventListener("cyrene:plugins-changed", onChanged)
    }
  }, [chatId, managed])

  return {
    models: models,
    reasoningEffort: reasoningEffort,
    selectedId: selectedId,
    setReasoningEffort: setReasoningEffort,
    setSelectedId: setSelectedId,
  }
}

export {
  useWbcComposerAgentCatalog,
  useWbcComposerAgentConfig,
  useWbcComposerConfiguredModels,
}
