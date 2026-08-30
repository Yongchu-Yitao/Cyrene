import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_AGENT_CHAT_FLOW_EVENT, WBC_BUILTIN_AGENT_ID, WBC_BUILTIN_AGENT_INSTALLATION, WBC_COMMANDS, WBC_COMMAND_ICONS, WBC_ICONS, WBC_MODES, WbcVoice, WorkbenchChatModel, useWbcEffect, useWbcRef, useWbcState, wbcAgentAvailability, wbcAgentChatFlowSnapshot, wbcAgentDisplayName, wbcAttachmentTypeLabel, wbcCapabilityEnabled, wbcCapabilityStatus, wbcChatAgent, wbcComposerAgentRow, wbcComposerSlashCommands, wbcCreateComposerVoiceFeedback, wbcCurrentModel, wbcDefaultAgentBinding, wbcErrorText, wbcFriendlyModelName, wbcHasAgentCapabilitySnapshot, wbcIsBuiltinAgent, wbcLocalizedModelDescription, wbcModeMeta, wbcNormalizePermissionMode, wbcPublishChatModelChanged, wbcReasoningEffortForModel, wbcStartVoiceRecorder, wbcSupportedReasoningEfforts, wbcT, wbcTranscribeVoiceBlob, wbcWorkspaceDisplayName } from "../../workbench-chat.jsx"
import { WBC_DRAFT_SAVE_DELAY_MS, WBC_NATIVE_FIELD_SIZING, wbcLoadAttachments, wbcLoadDraft, wbcLoadWorkspaceOverride, wbcSaveAttachments, wbcSaveDraft, wbcSaveWorkspaceOverride, wbcSyncLegacyComposerHeight, wbcWorkspaceContextKey } from "./messages.jsx"
import { WbcFileVisual, wbcCommandMeta } from "./file-resources.jsx"
import { useWbcComposerAttachments } from "./composer-attachments.jsx"
import { useWbcComposerAgentFlow } from "./composer-flow.jsx"
import { useWbcComposerAgentCatalog, useWbcComposerAgentConfig, useWbcComposerConfiguredModels } from "./composer-model-state.jsx"
import { useWbcComposerVoice } from "./composer-voice.jsx"
import { WbcComposerContextIndicator } from "./context-indicator.jsx"

var WBC_CONTEXT_ACTIVATION_KEYS = ["mcpServers", "skills", "pluginPacks"];

function wbcAuthoredContextTranslation(item, field) {
  var translations = item && item.i18n && typeof item.i18n === "object" ? item.i18n : {};
  var language = "en";
  try { language = workbenchServices.i18n().getLang() || "en"; } catch (error) {}
  var localized = translations[language] || translations[String(language).split("-")[0]] || {};
  return String(localized && localized[field] || "");
}

function wbcNormalizeContextActivations(value) {
  var source = value && typeof value === "object" ? value : {};
  var result = {};
  WBC_CONTEXT_ACTIVATION_KEYS.forEach(function (key) {
    result[key] = Array.from(new Set((Array.isArray(source[key]) ? source[key] : []).map(function (item) {
      return String(item || "").trim();
    }).filter(Boolean)));
  });
  return result;
}

function wbcContextCatalogItems(catalog, key) {
  return catalog && typeof catalog === "object" && Array.isArray(catalog[key])
    ? catalog[key] : [];
}

function wbcAvailableContextIds(items) {
  return new Set((items || []).filter(function (item) {
    return item && item.available === true;
  }).map(function (item) { return String(item.id || ""); }).filter(Boolean));
}

function wbcParseSlashCommandText(text, commands) {
  var source = String(text || "").trim();
  if (source.indexOf("/") !== 0) return null;
  var match = source.match(/^\/([^\s]+)(?:\s+([\s\S]*))?$/);
  if (!match) return null;
  var id = match[1];
  var command = (commands || []).find(function (item) { return item.id === id; });
  return command ? { command: command, message: String(match[2] || "").trim() } : null;
}

function wbcCommandOptionId(commandId) {
  return "wbc-command-option-" + String(commandId || "").replace(/[^a-zA-Z0-9_-]/g, "-");
}

function wbcTranslateSlashCommand(declared) {
  var builtin = wbcCommandMeta(declared.id);
  if (builtin) return { ...builtin, ...declared, label: builtin.label, desc: builtin.desc, group: declared.group || "workflow" };
  var label = String(declared.label || declared.id || "");
  var desc = String(declared.description || "");
  if (declared.group === "pluginPack" && declared.activation) {
    label = wbcAuthoredContextTranslation(declared, "name")
      || wbcT("toolName." + declared.activation.id, label);
    desc = wbcAuthoredContextTranslation(declared, "description")
      || wbcT("pluginPackDesc." + declared.activation.id, desc);
  } else {
    label = wbcAuthoredContextTranslation(declared, "title") || label;
    desc = wbcAuthoredContextTranslation(declared, "description") || desc;
  }
  return { ...declared, label: label, desc: desc };
}

// Workbench chat feature module with explicit ESM dependencies.
function WbcComposer({ chat, project, runtime, running, onSend, onGuidance, onInterrupt, draftNamespace, autoFocus, clearOnSend, error, errorKind, compact, placeholder, runningPlaceholder, topOverlay, draftAgent, onDraftAgentChange, onSwitchAgent, onOpenAgentDetail }) {
  var model = WorkbenchChatModel;
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var pluginModules = Array.isArray(dataStore.state.pluginModules)
    ? dataStore.state.pluginModules : [];
  var composerContextAvailable = pluginModules.indexOf("composer_context") >= 0;
  var soulMarkerAvailable = composerContextAvailable && pluginModules.indexOf("soul") >= 0;
  var agentsAvailable = pluginModules.indexOf("agents") >= 0;
  var remoteMarkerAvailable = composerContextAvailable && pluginModules.indexOf("remote") >= 0;
  var mcpMarkerAvailable = composerContextAvailable && pluginModules.indexOf("mcp") >= 0;
  var skillsMarkerAvailable = composerContextAvailable && pluginModules.indexOf("skills") >= 0;
  var chatId = chat ? chat.id : "";
  var awaitingAnswer = !!(chat && chat.pendingQuestion && chat.pendingQuestion.id);
  var projectId = (project && project.id) || "";
  var projectWorkspacePath = (project && project.workspacePath) || "";
  // Surface-scoped storage prefix (empty for the main chat). The quick-chat
  // window passes one so its draft/attachments never overwrite the main
  // window's for the same chat id.
  var draftNs = draftNamespace || "";
  var agentPickerEnabled = agentsAvailable && typeof onDraftAgentChange === "function";
  var shouldClearOnSend = clearOnSend !== false;
  var workspaceContextKey = wbcWorkspaceContextKey(chatId, projectId);
  var [draft, setDraft] = useWbcState(function () { return wbcLoadDraft(chatId, draftNs); });
  var [mode, setMode] = useWbcState(function () {
    return wbcNormalizePermissionMode(chat && chat.permissionMode, "auto");
  });
  var [command, setCommand] = useWbcState("");
  var [slashCommandCatalog, setSlashCommandCatalog] = useWbcState([]);
  var [slashCommandCatalogLoaded, setSlashCommandCatalogLoaded] = useWbcState(false);
  var [slashCommandCatalogLoading, setSlashCommandCatalogLoading] = useWbcState(false);
  var [slashActiveIndex, setSlashActiveIndex] = useWbcState(0);
  var [slashDismissedDraft, setSlashDismissedDraft] = useWbcState("");
  var [toolsOpen, setToolsOpen] = useWbcState(false);
  var [modelOpen, setModelOpen] = useWbcState(false);
  var [modelPanel, setModelPanel] = useWbcState("root");
  var agentCatalog = useWbcComposerAgentCatalog(agentPickerEnabled);
  var agentOptions = agentCatalog.options;
  var agentOptionsLoaded = agentCatalog.loaded;
  var [contextState, setContextState] = useWbcState(null);
  var [contextCatalogLoading, setContextCatalogLoading] = useWbcState(false);
  var [contextCatalogLoaded, setContextCatalogLoaded] = useWbcState(false);
  var [contextStateRevision, setContextStateRevision] = useWbcState(0);
  var contextCatalogPayload = contextState && contextState.catalog && typeof contextState.catalog === "object"
    ? contextState.catalog : null;
  var contextOptions = contextState && contextState.options && typeof contextState.options === "object"
    ? contextState.options : null;
  var contextResponseAvailable = composerContextAvailable && !!contextCatalogPayload && !!contextOptions;
  var soulAvailable = soulMarkerAvailable && contextResponseAvailable
    && contextOptions.soul && contextOptions.soul.available === true;
  var workspaceAvailable = contextResponseAvailable
    && contextOptions.workspace && contextOptions.workspace.available === true;
  var remoteAvailable = remoteMarkerAvailable && contextResponseAvailable
    && contextOptions.remoteDevices && contextOptions.remoteDevices.available === true;
  var mcpAvailable = mcpMarkerAvailable && contextResponseAvailable
    && Array.isArray(contextCatalogPayload.mcpServers);
  var skillsAvailable = skillsMarkerAvailable && contextResponseAvailable
    && Array.isArray(contextCatalogPayload.skills);
  var pluginPacksAvailable = contextResponseAvailable
    && Array.isArray(contextCatalogPayload.pluginPacks);
  var contextCatalog = {
    mcpServers: mcpAvailable ? wbcContextCatalogItems(contextCatalogPayload, "mcpServers") : [],
    skills: skillsAvailable ? wbcContextCatalogItems(contextCatalogPayload, "skills") : [],
    pluginPacks: pluginPacksAvailable ? wbcContextCatalogItems(contextCatalogPayload, "pluginPacks") : [],
  };
  var remoteDevices = remoteAvailable
    ? wbcContextCatalogItems(contextCatalogPayload, "remoteDevices") : [];
  var [soulActive, setSoulActive] = useWbcState(function () {
    return chat && typeof chat.soulActive === "boolean" ? chat.soulActive : true;
  });
  var [workspaceActive, setWorkspaceActive] = useWbcState(function () {
    return chat && typeof chat.workspaceActive === "boolean" ? chat.workspaceActive : true;
  });
  var [workspaceOverride, setWorkspaceOverride] = useWbcState(function () {
    return String(chat && chat.workspaceOverride || "").trim()
      || wbcLoadWorkspaceOverride(workspaceContextKey, draftNs);
  });
  var [remoteDeviceIds, setRemoteDeviceIds] = useWbcState(function () {
    return chat && Array.isArray(chat.remoteDeviceIds) ? chat.remoteDeviceIds.slice() : [];
  });
  var [contextActivations, setContextActivations] = useWbcState(function () {
    return wbcNormalizeContextActivations(chat && chat.contextActivations);
  });
  var [contextCatalogPanel, setContextCatalogPanel] = useWbcState("");
  var taRef = useWbcRef(null);
  var composerBoxRef = useWbcRef(null);
  var sendButtonRef = useWbcRef(null);
  var toolsPickerRef = useWbcRef(null);
  var modelPickerRef = useWbcRef(null);
  var draftRef = useWbcRef(draft);
  var prevChatIdRef = useWbcRef(chatId);
  var workspaceOverrideRef = useWbcRef(workspaceOverride);
  var remoteDeviceIdsRef = useWbcRef(remoteDeviceIds);
  var contextActivationsRef = useWbcRef(contextActivations);
  var prevWorkspaceContextKeyRef = useWbcRef(workspaceContextKey);
  var draftSaveTimerRef = useWbcRef(0);
  var pendingDraftSaveRef = useWbcRef(null);
  // Last payload snapshot for optimistic clear with restore on error
  var lastSentRef = useWbcRef(null);
  var prevRunningRef = useWbcRef(running);
  var ComposerBrowserIcon = workbenchServices.browser().Icon;
  var agentFlow = useWbcComposerAgentFlow(chatId);
  // Effective Agent identity: an existing chat carries its locked binding; an
  // empty draft carries the composer's draft selection; otherwise the built-in
  // Cyrene Agent is the default.
  var boundAgent = wbcChatAgent(chat);
  var draftAgentIdentity = draftAgent && draftAgent.agent && typeof draftAgent.agent === "object"
    ? draftAgent.agent
    : draftAgent;
  var effectiveAgent = boundAgent || draftAgentIdentity || { installationId: WBC_BUILTIN_AGENT_INSTALLATION, agentId: WBC_BUILTIN_AGENT_ID, displayName: "Cyrene", builtin: true };
  var effectiveCatalogAgent = agentOptions.find(function (agent) {
    return String(agent && agent.installationId || "") === String(effectiveAgent.installationId || "");
  }) || null;
  var effectiveAgentName = wbcAgentDisplayName(effectiveCatalogAgent || effectiveAgent);
  var builtinContextCapabilities = wbcIsBuiltinAgent(effectiveAgent);
  // Probe/auth/settings changes refresh the installed catalog. Overlay that
  // current snapshot so an open composer is never gated by stale capabilities
  // captured when the chat was first created.
  var capabilitySource = (!wbcIsBuiltinAgent(effectiveAgent) && effectiveCatalogAgent)
    ? { capabilities: effectiveCatalogAgent.capabilities || {} }
    : chat;
  var agentBindingLocked = !!(chat && ((chat.agent && chat.agent.bindingLocked) || (chat.messages && chat.messages.length > 0)));
  function pickAgentBinding(binding) {
    var targetId = String(binding && binding.agent && binding.agent.installationId || WBC_BUILTIN_AGENT_INSTALLATION);
    if (targetId === String(effectiveAgent.installationId || WBC_BUILTIN_AGENT_INSTALLATION)) return;
    // Persisted empty chats are safely rebound in place by the backend. Chats
    // with messages ask before creating a new Agent-bound conversation.
    if (chat && chat.id && onSwitchAgent) onSwitchAgent(binding);
    else onDraftAgentChange(binding);
  }
  var chatCapabilitySnapshot = wbcHasAgentCapabilitySnapshot(capabilitySource);
  var capText = wbcCapabilityEnabled(capabilitySource, "input", "text", { strictUnknown: true });
  var capImage = wbcCapabilityEnabled(capabilitySource, "input", "image", { strictUnknown: true });
  var capFile = wbcCapabilityEnabled(capabilitySource, "input", "file", { strictUnknown: true });
  var capAudio = wbcCapabilityEnabled(capabilitySource, "input", "audio", { strictUnknown: true });
  var capSteer = wbcCapabilityEnabled(capabilitySource, "interaction", "steer", { strictUnknown: true });
  var capCancel = wbcCapabilityEnabled(capabilitySource, "interaction", "cancel", { strictUnknown: true });
  var capReasoningEffort = wbcCapabilityEnabled(capabilitySource, "model", "reasoningEffort");
  var capSwitchModel = wbcCapabilityEnabled(capabilitySource, "model", "switchDuringSession", { strictUnknown: true });
  var composerAttachments = useWbcComposerAttachments({
    awaitingAnswer: awaitingAnswer,
    canAttachFiles: capFile,
    canAttachImages: capImage,
    chatId: chatId,
    draftNamespace: draftNs,
    model: model,
    previousChatIdRef: prevChatIdRef,
    running: running,
    setDraft: setDraft,
  });
  var attachRef = composerAttachments.attachRef;
  var attachments = composerAttachments.attachments;
  var failedImagePreviews = composerAttachments.failedImagePreviews;
  var fileRef = composerAttachments.fileRef;
  var onFilePick = composerAttachments.onFilePick;
  var onPaste = composerAttachments.onPaste;
  var pickFiles = composerAttachments.pickFiles;
  var setAttachments = composerAttachments.setAttachments;
  var setFailedImagePreviews = composerAttachments.setFailedImagePreviews;
  var uploading = composerAttachments.uploading;
  var effectiveModelAccess = (effectiveCatalogAgent && effectiveCatalogAgent.modelAccess)
    || (draftAgent && draftAgent.modelAccess)
    || (chat && chat.modelAccess)
    || {};
  var agentManagedModels = effectiveModelAccess.mode === "agent_managed";
  var configuredModelState = useWbcComposerConfiguredModels(chatId, chat, agentManagedModels);
  var configuredModels = configuredModelState.models;
  var reasoningEffort = configuredModelState.reasoningEffort;
  var selectedModelId = configuredModelState.selectedId;
  var setReasoningEffort = configuredModelState.setReasoningEffort;
  var setSelectedModelId = configuredModelState.setSelectedId;
  var agentConfigState = useWbcComposerAgentConfig(chatId, chat, agentManagedModels);
  var agentConfigLoading = agentConfigState.loading;
  var agentConfigOptions = agentConfigState.options;
  var agentConfigValues = agentConfigState.values;
  var setAgentConfigOptions = agentConfigState.setOptions;
  var setAgentConfigValues = agentConfigState.setValues;
  var permissionCapability = chatCapabilitySnapshot
    ? wbcCapabilityStatus(capabilitySource, "interaction", "permission")
    : "supported";
  var permissionAgentDefined = chatCapabilitySnapshot
    && permissionCapability === "agent_defined";
  var permissionModeVisible = permissionCapability === "supported" || permissionAgentDefined;
  var commandSource = {
    ...(chat || {}),
    agent: effectiveAgent,
    capabilities: capabilitySource && capabilitySource.capabilities,
  };
  var agentSlashCommands = wbcComposerSlashCommands(commandSource);
  var slashCommandsCapabilityDriven = agentSlashCommands !== null;

  useWbcEffect(function () { draftRef.current = draft; });
  useWbcEffect(function () { workspaceOverrideRef.current = workspaceOverride; });
  useWbcEffect(function () { remoteDeviceIdsRef.current = remoteDeviceIds; });
  useWbcEffect(function () { contextActivationsRef.current = contextActivations; });

  useWbcEffect(function () {
    if (!composerContextAvailable) {
      setToolsOpen(false);
      setCommand("");
    }
  }, [composerContextAvailable]);

  useWbcEffect(function () {
    if (!soulAvailable) {
      setSoulActive(false);
      return;
    }
    setSoulActive(chat && typeof chat.soulActive === "boolean"
      ? chat.soulActive : contextOptions.soul.selected === true);
  }, [soulAvailable, chatId, contextState]);

  useWbcEffect(function () {
    var next = wbcNormalizeContextActivations(chat && chat.contextActivations);
    WBC_CONTEXT_ACTIVATION_KEYS.forEach(function (key) {
      var allowed = wbcAvailableContextIds(contextCatalog[key]);
      next[key] = next[key].filter(function (identity) { return allowed.has(identity); });
    });
    contextActivationsRef.current = next;
    setContextActivations(next);
    var allowedRemoteIds = wbcAvailableContextIds(remoteDevices);
    var requestedRemoteIds = chat && Array.isArray(chat.remoteDeviceIds)
      ? chat.remoteDeviceIds
      : contextOptions && contextOptions.remoteDevices && Array.isArray(contextOptions.remoteDevices.selectedIds)
        ? contextOptions.remoteDevices.selectedIds : [];
    var nextRemoteIds = remoteAvailable
      ? requestedRemoteIds.map(function (item) { return String(item || ""); })
          .filter(function (identity) { return allowedRemoteIds.has(identity); })
      : [];
    remoteDeviceIdsRef.current = nextRemoteIds;
    setRemoteDeviceIds(nextRemoteIds);
    if (!workspaceAvailable) {
      setWorkspaceActive(false);
      setWorkspaceOverride("");
    } else {
      setWorkspaceActive(chat && typeof chat.workspaceActive === "boolean"
        ? chat.workspaceActive : contextOptions.workspace.selected === true);
      setWorkspaceOverride(String(chat && chat.workspaceOverride || "").trim()
        || wbcLoadWorkspaceOverride(workspaceContextKey, draftNs));
    }
    if ((!mcpAvailable && !skillsAvailable && !pluginPacksAvailable) ||
        (contextCatalogPanel === "mcpServers" && !mcpAvailable) ||
        (contextCatalogPanel === "skills" && !skillsAvailable) ||
        (contextCatalogPanel === "pluginPacks" && !pluginPacksAvailable)) {
      setContextCatalogPanel("");
    }
  }, [chatId, contextState, workspaceAvailable, remoteAvailable, mcpAvailable, skillsAvailable, pluginPacksAvailable]);

  useWbcEffect(function () {
    if (builtinContextCapabilities) return;
    var empty = wbcNormalizeContextActivations(null);
    contextActivationsRef.current = empty;
    setContextActivations(empty);
    setContextCatalogPanel("");
  }, [builtinContextCapabilities]);

  useWbcEffect(function () {
    if (!builtinContextCapabilities || (draft.indexOf("/") !== 0 && !toolsOpen) || slashCommandCatalogLoaded || slashCommandCatalogLoading) return undefined;
    var cancelled = false;
    setSlashCommandCatalogLoading(true);
    workbenchServices.api().json(
      "/api/workbench/slash-commands?project_id=" + encodeURIComponent(projectId || ""),
      { toast: false }
    ).then(function (payload) {
      if (cancelled) return;
      var commands = Array.isArray(payload && payload.commands) ? payload.commands : [];
      setSlashCommandCatalog(commands);
      if (command && !commands.some(function (item) { return item && item.id === command; })) {
        setCommand("");
      }
      setSlashCommandCatalogLoaded(true);
    }).catch(function (err) {
      if (!cancelled) workbenchServices.api().toastError(err, wbcT("workbenchChat.slashCommandsLoadFailed", "Failed to load commands: "));
    }).finally(function () {
      if (!cancelled) setSlashCommandCatalogLoading(false);
    });
    return function () { cancelled = true; };
  }, [builtinContextCapabilities, draft.indexOf("/") === 0, toolsOpen, slashCommandCatalogLoaded, projectId]);

  useWbcEffect(function () {
    setSlashCommandCatalog([]);
    setSlashCommandCatalogLoaded(false);
  }, [projectId]);

  useWbcEffect(function () {
    function invalidateComposerContext() {
      setContextState(null);
      setContextCatalogLoaded(false);
      setContextCatalogPanel("");
      setContextStateRevision(function (current) { return current + 1; });
      setSlashCommandCatalog([]);
      setSlashCommandCatalogLoaded(false);
    }
    function onPlatformEvent(event) {
      if (event && event.type === "remote_devices_changed") invalidateComposerContext();
    }
    var unsubscribe;
    try { unsubscribe = workbenchServices.events().subscribe(onPlatformEvent); } catch (error) {}
    window.addEventListener("cyrene:plugins-changed", invalidateComposerContext);
    window.addEventListener("cyrene:remote-devices-changed", invalidateComposerContext);
    return function () {
      window.removeEventListener("cyrene:plugins-changed", invalidateComposerContext);
      window.removeEventListener("cyrene:remote-devices-changed", invalidateComposerContext);
      if (unsubscribe) unsubscribe();
    };
  }, []);

  useWbcEffect(function () {
    if (draft.indexOf("/") !== 0 && !toolsOpen && !command && slashCommandCatalogLoaded) {
      setSlashCommandCatalogLoaded(false);
    }
  }, [draft.indexOf("/") === 0, toolsOpen, command]);

  useWbcEffect(function () {
    if (!modelOpen) return undefined;
    var overlays;
    try { overlays = workbenchServices.browserOverlays(); } catch (e) {}
    if (!overlays || typeof overlays.adjust !== "function") return undefined;
    overlays.adjust(1);
    return function () { overlays.adjust(-1); };
  }, [modelOpen]);

  useWbcEffect(function () {
    if (prevChatIdRef.current !== chatId) return;
    if (draftSaveTimerRef.current) window.clearTimeout(draftSaveTimerRef.current);
    pendingDraftSaveRef.current = { id: chatId, text: draft, ns: draftNs };
    draftSaveTimerRef.current = window.setTimeout(flushPendingDraftSave, WBC_DRAFT_SAVE_DELAY_MS);
  }, [draft, chatId, draftNs]);

  useWbcEffect(function () {
    function flushHiddenDraft() {
      if (document.visibilityState === "hidden") flushPendingDraftSave();
    }
    window.addEventListener("pagehide", flushPendingDraftSave);
    document.addEventListener("visibilitychange", flushHiddenDraft);
    return function () {
      window.removeEventListener("pagehide", flushPendingDraftSave);
      document.removeEventListener("visibilitychange", flushHiddenDraft);
      flushPendingDraftSave();
    };
  }, []);

  useWbcEffect(function () {
    if (prevWorkspaceContextKeyRef.current === workspaceContextKey) {
      wbcSaveWorkspaceOverride(workspaceContextKey, workspaceOverride, draftNs);
    }
  }, [workspaceOverride]);

  useWbcEffect(function () {
    if (!WBC_NATIVE_FIELD_SIZING) {
      wbcSyncLegacyComposerHeight(taRef.current, draft, compact);
    }
  }, [draft, compact]);

  // Focus the textarea on mount when the host surface asks for it (the quick
  // chat window opens straight into typing).
  useWbcEffect(function () {
    if (autoFocus && taRef.current) {
      taRef.current.focus();
    }
  }, []);

  useWbcEffect(function () {
    if (!toolsOpen) return undefined;
    function closeToolsMenu(event) {
      if (toolsPickerRef.current && !toolsPickerRef.current.contains(event.target)) {
        setToolsOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeToolsMenu);
    return function () { document.removeEventListener("pointerdown", closeToolsMenu); };
  }, [toolsOpen]);

  useWbcEffect(function () {
    if (!modelOpen) return undefined;
    function closeModelPicker(event) {
      if (modelPickerRef.current && !modelPickerRef.current.contains(event.target)) {
        setModelOpen(false);
        setModelPanel("root");
      }
    }
    document.addEventListener("pointerdown", closeModelPicker);
    return function () { document.removeEventListener("pointerdown", closeModelPicker); };
  }, [modelOpen]);

  useWbcEffect(function () {
    var prev = prevChatIdRef.current;
    if (prev !== chatId) {
      flushPendingDraftSave();
      wbcSaveDraft(prev, draftRef.current, draftNs);
      wbcSaveAttachments(prev, attachRef.current, draftNs);
      setDraft(wbcLoadDraft(chatId, draftNs));
      setAttachments(wbcLoadAttachments(chatId, draftNs));
      setMode(wbcNormalizePermissionMode(chat && chat.permissionMode, "auto"));
      setSoulActive(soulAvailable && (chat && typeof chat.soulActive === "boolean"
        ? chat.soulActive : contextOptions.soul.selected === true));
      setWorkspaceActive(workspaceAvailable && (chat && typeof chat.workspaceActive === "boolean"
        ? chat.workspaceActive : contextOptions.workspace.selected === true));
      setReasoningEffort(String(chat && chat.reasoningEffort || "").trim().toLowerCase());
      var nextContextActivations = wbcNormalizeContextActivations(chat && chat.contextActivations);
      if (!mcpAvailable) nextContextActivations.mcpServers = [];
      if (!skillsAvailable) nextContextActivations.skills = [];
      if (!pluginPacksAvailable) nextContextActivations.pluginPacks = [];
      setContextActivations(nextContextActivations);
      contextActivationsRef.current = nextContextActivations;
      setContextCatalogPanel("");
      setFailedImagePreviews({});
      prevChatIdRef.current = chatId;
    }
      setCommand("");
      setToolsOpen(false);
      setModelOpen(false);
      setModelPanel("root");
  }, [chatId]);

  useWbcEffect(function () {
    var prevKey = prevWorkspaceContextKeyRef.current;
    if (prevKey === workspaceContextKey) return;
    var currentOverride = workspaceOverrideRef.current;
    if (workspaceAvailable) wbcSaveWorkspaceOverride(prevKey, currentOverride, draftNs);
    var nextOverride = workspaceAvailable ? (String(chat && chat.workspaceOverride || "").trim()
      || wbcLoadWorkspaceOverride(workspaceContextKey, draftNs)) : "";
    setWorkspaceOverride(nextOverride);
    workspaceOverrideRef.current = nextOverride;
    prevWorkspaceContextKeyRef.current = workspaceContextKey;
  }, [workspaceContextKey, workspaceAvailable]);

  // Track running→false transitions where an error occurred to restore the draft
  // that was optimistically cleared in submit() — only for the main chat surface
  // (shouldClearOnSend is true) and only for message-kind errors.
  useWbcEffect(function () {
    var wasRunning = prevRunningRef.current;
    prevRunningRef.current = running;
    if (wasRunning && !running && lastSentRef.current && shouldClearOnSend) {
      var isSendError = error && (errorKind === "message" || errorKind === "load");
      if (isSendError) {
        var saved = lastSentRef.current;
        setDraft(saved.message || "");
        setAttachments(saved.attachments || []);
        if (saved.command) setCommand(saved.command);
      }
      lastSentRef.current = null;
    }
  }, [running, error, errorKind]);

  useWbcEffect(function () {
    if (!composerContextAvailable) {
      setContextState(null);
      setContextCatalogLoaded(false);
      setContextCatalogLoading(false);
      return undefined;
    }
    var cancelled = false;
    var controller = new AbortController();
    setContextCatalogLoading(true);
    setContextCatalogLoaded(false);
    workbenchServices.api().json("/api/context/state", { toast: false, signal: controller.signal }).then(function (s) {
      if (cancelled) return;
      var valid = s && typeof s === "object"
        && s.catalog && typeof s.catalog === "object"
        && s.options && typeof s.options === "object";
      setContextState(valid ? s : null);
      setContextCatalogLoaded(true);
    }).catch(function (err) {
      if (cancelled || (err && err.name === "AbortError")) return;
      setContextState(null);
      setContextCatalogLoaded(true);
      workbenchServices.api().toastError(err, wbcT("workbenchChat.contextCapabilitiesLoadFailed", "Failed to load context capabilities: "));
    }).finally(function () {
      if (!cancelled) setContextCatalogLoading(false);
    });
    return function () { cancelled = true; controller.abort(); };
  }, [projectId, projectWorkspacePath, composerContextAvailable, contextStateRevision]);

  function flushPendingDraftSave() {
    if (draftSaveTimerRef.current) {
      window.clearTimeout(draftSaveTimerRef.current);
      draftSaveTimerRef.current = 0;
    }
    var pending = pendingDraftSaveRef.current;
    pendingDraftSaveRef.current = null;
    if (pending) wbcSaveDraft(pending.id, pending.text, pending.ns);
  }

  function persistCurrentDraft() {
    pendingDraftSaveRef.current = {
      id: chatId,
      text: String(draftRef.current || ""),
      ns: draftNs,
    };
    flushPendingDraftSave();
  }

  function submit(messageOverride) {
    if (awaitingAnswer) return;
    var text = String(typeof messageOverride === "string" ? messageOverride : draft).trim();
    if (running) {
      if (!text || !onGuidance) return;
      draftRef.current = "";
      setDraft("");
      persistCurrentDraft();
      onGuidance(text).catch(function () {
        draftRef.current = text;
        setDraft(text);
      });
      return;
    }
    var parsedSlash = !command ? wbcParseSlashCommandText(text, slashPool) : null;
    var submittedCommand = command || (parsedSlash && parsedSlash.command.id) || "";
    var submittedDescriptor = command
      ? slashPool.find(function (item) { return item.id === command; })
      : parsedSlash && parsedSlash.command;
    if (parsedSlash) text = parsedSlash.message;
    if (!text && attachments.length === 0 && !submittedCommand) return;
    var submittedContextActivations = wbcNormalizeContextActivations(contextActivationsRef.current);
    if (!mcpAvailable) submittedContextActivations.mcpServers = [];
    if (!skillsAvailable) submittedContextActivations.skills = [];
    if (!pluginPacksAvailable) submittedContextActivations.pluginPacks = [];
    if (submittedDescriptor && submittedDescriptor.activation) {
      var activationKind = String(submittedDescriptor.activation.kind || "");
      var activationId = String(submittedDescriptor.activation.id || "");
      var activationOwnerAvailable = (activationKind === "mcpServers" && mcpAvailable)
        || (activationKind === "skills" && skillsAvailable)
        || (activationKind === "pluginPacks" && pluginPacksAvailable);
      if (activationOwnerAvailable
          && wbcAvailableContextIds(contextCatalog[activationKind]).has(activationId)
          && submittedContextActivations[activationKind]
          && activationId
          && submittedContextActivations[activationKind].indexOf(activationId) < 0) {
        submittedContextActivations[activationKind] = submittedContextActivations[activationKind].concat([activationId]);
        contextActivationsRef.current = submittedContextActivations;
        setContextActivations(submittedContextActivations);
      }
    }
    var payload = {
      message: text,
      attachments: attachments,
      mode: mode,
      command: submittedCommand,
      model: agentManagedModels ? "" : selectedModelId,
      reasoningEffort: agentManagedModels ? "" : reasoningEffort,
    };
    if (soulAvailable) payload.soulActive = personaOn;
    if (workspaceAvailable) {
      payload.workspaceOverride = workspaceOverride;
      payload.workspaceActive = workspaceOn;
    }
    if (composerContextAvailable) {
      payload.remoteDeviceIds = remoteAvailable ? remoteDeviceIdsRef.current.slice() : [];
      payload.contextActivations = submittedContextActivations;
    }
    // Optimistically clear on send; restored in the running-transition effect
    // if the send fails (error). The quick-chat surface passes clearOnSend=false
    // and manages its own draft lifecycle.
    if (shouldClearOnSend) {
      lastSentRef.current = payload;
      draftRef.current = "";
      setDraft("");
      persistCurrentDraft();
      setAttachments([]);
      setCommand("");
    }
    onSend(payload);
  }

  var composerVoice = useWbcComposerVoice({
    awaitingAnswer: awaitingAnswer,
    chatId: chatId,
    draftRef: draftRef,
    setDraft: setDraft,
    setModelOpen: setModelOpen,
    setModelPanel: setModelPanel,
    setToolsOpen: setToolsOpen,
    submit: submit,
    textAreaRef: taRef,
  });
  var voicePhase = composerVoice.voicePhase;
  var voiceSnapshot = composerVoice.voiceSnapshot;
  var toggleVoiceInput = composerVoice.toggleVoiceInput;

  function onKeyDown(event) {
    if (slashDraftOpen && slashItems.length && !event.metaKey && !event.ctrlKey && !event.altKey) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        var direction = event.key === "ArrowDown" ? 1 : -1;
        setSlashActiveIndex(function (current) {
          return (current + direction + slashItems.length) % slashItems.length;
        });
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        if (event.nativeEvent && event.nativeEvent.isComposing) return;
        event.preventDefault();
        chooseSlashCommand(slashItems[Math.min(slashActiveIndex, slashItems.length - 1)]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setSlashDismissedDraft(draft);
        return;
      }
    }
    var sc = workbenchServices.shortcuts();
    if (sc && sc.matches(event, "composer-send")) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return; // IME guard
      event.preventDefault();
      submit();
      return;
    }
    if (sc && sc.matches(event, "composer-newline")) {
      // Allow the textarea's default Shift+Enter behavior (insert newline).
      return;
    }
    // Fallback when the shortcut module is unavailable: plain Enter sends,
    // Shift/Cmd/Ctrl+Enter inserts a newline.
    if (!sc && event.key === "Enter" && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return; // IME guard
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Escape") {
      setToolsOpen(false);
      setModelOpen(false);
      setModelPanel("root");
    }
  }

  function chooseSlashCommand(item) {
    if (!item) return;
    setCommand(item.id);
    setToolsOpen(false);
    draftRef.current = "";
    setDraft("");
    setSlashDismissedDraft("");
    if (taRef.current) taRef.current.focus();
  }

  var slashQuery = draft.indexOf("/") === 0 ? draft.slice(1).toLowerCase() : "";
  var translatedCommands = (slashCommandCatalog.length ? slashCommandCatalog : WBC_COMMANDS).map(wbcTranslateSlashCommand).filter(function (item) {
    return !!item.id && (item.group !== "pluginPack" || pluginPacksAvailable);
  });
  var translatedModes = WBC_MODES.map(function (m) { return wbcModeMeta(m.id); });
  // Slash commands come from the Agent's declared command list when a
  // capability snapshot exists; the built-in Agent keeps its native set.
  var slashPool = slashCommandsCapabilityDriven
    ? agentSlashCommands.map(function (declared) {
        var builtin = translatedCommands.find(function (item) { return item.id === declared.id; });
        return builtin || { id: declared.id, label: declared.label || declared.id, desc: declared.description || declared.inputHint || "", external: true };
      })
    : translatedCommands;
  var slashItems = slashPool.filter(function (c) {
    return !slashQuery || c.id.indexOf(slashQuery) !== -1 || c.label.toLowerCase().indexOf(slashQuery) !== -1;
  });
  function slashCommandIcon(item) {
    if (WBC_COMMAND_ICONS[item.id]) return WBC_COMMAND_ICONS[item.id];
    if (item.group === "skill") return WBC_ICONS.book;
    if (item.group === "mcp") return WBC_ICONS.layers;
    if (item.group === "pluginPack") return WBC_ICONS.tool;
    if (item.group === "customTool") return WBC_ICONS.code;
    if (item.group === "plugin") return WBC_ICONS.phase;
    return WBC_ICONS.slash;
  }
  var slashDraftOpen = draft.indexOf("/") === 0 && !/\s/.test(draft) && slashItems.length > 0 && !running && slashDismissedDraft !== draft;
  var showToolsMenu = (toolsOpen || slashDraftOpen) && !running && !awaitingAnswer;
  var activeCommand = command ? (slashPool.find(function (item) { return item.id === command; }) || wbcCommandMeta(command) || { id: command, label: command, desc: "" }) : null;
  var currentMode = wbcModeMeta(mode);
  var personaOn = soulAvailable && soulActive !== false;
  var workspaceOn = workspaceAvailable && workspaceActive !== false;
  var activeContextCapabilityCount = WBC_CONTEXT_ACTIVATION_KEYS.reduce(function (count, key) {
    return count + contextActivations[key].length;
  }, 0);
  var enabledContentCount = (personaOn ? 1 : 0) + (workspaceOn ? 1 : 0) + remoteDeviceIds.length + activeContextCapabilityCount;
  var contextCapabilityCategories = [
    mcpAvailable && { key: "mcpServers", label: wbcT("workbenchChat.contextMcp", "MCP servers"), icon: WBC_ICONS.layers, items: contextCatalog.mcpServers },
    skillsAvailable && { key: "skills", label: wbcT("workbenchChat.contextSkills", "Skills"), icon: WBC_ICONS.book, items: contextCatalog.skills },
    pluginPacksAvailable && { key: "pluginPacks", label: wbcT("workbenchChat.contextPluginPacks", "Plugin packs"), icon: WBC_ICONS.tool, items: contextCatalog.pluginPacks },
  ].filter(Boolean);

  useWbcEffect(function () {
    setSlashActiveIndex(function (current) {
      return slashItems.length ? Math.min(current, slashItems.length - 1) : 0;
    });
  }, [slashQuery, slashItems.length]);

  useWbcEffect(function () {
    if (!slashCommandsCapabilityDriven || !command) return;
    if (!slashPool.some(function (item) { return item.id === command; })) setCommand("");
  }, [slashCommandsCapabilityDriven, slashPool.map(function (item) { return item.id; }).join("|"), command]);
  // Follow the active project's workspace by default. A directory explicitly
  // chosen from the composer remains selected when the user switches projects.
  var wsDir = workspaceOverride || projectWorkspacePath || (contextState && contextState.workspace_dir) || "";
  var wsHistory = (contextState && Array.isArray(contextState.workspace_history)) ? contextState.workspace_history : [];
  var workspaceOptions = [];
  [wsDir, projectWorkspacePath].concat(wsHistory).forEach(function (path) {
    var normalized = String(path || "").trim();
    if (!normalized || workspaceOptions.some(function (item) { return item.path === normalized; })) return;
    workspaceOptions.push({ path: normalized, isDefault: normalized === projectWorkspacePath });
  });
  var selectedModel = configuredModels.find(function (item) {
    return String(item.id || item.model || "") === String(selectedModelId || "");
  });
  var agentModelConfig = agentConfigOptions.find(function (option) {
    return String(option.category || "") === "model";
  }) || agentConfigOptions.find(function (option) {
    return String(option.id || "").toLowerCase() === "model";
  });
  var agentModelValue = String(
    agentModelConfig && Object.prototype.hasOwnProperty.call(agentConfigValues, agentModelConfig.id)
      ? agentConfigValues[agentModelConfig.id]
      : agentModelConfig && agentModelConfig.currentValue || ""
  );
  var agentSelectedModel = agentModelConfig && (agentModelConfig.options || []).find(function (item) {
    return String(item.value || "") === agentModelValue;
  });
  var agentReasoningConfig = agentConfigOptions.find(function (option) {
    return String(option.category || "") === "thought_level";
  }) || agentConfigOptions.find(function (option) {
    return ["reasoning_effort", "reasoning-effort"].indexOf(String(option.id || "").toLowerCase()) >= 0;
  });
  var agentReasoningValue = String(
    agentReasoningConfig && Object.prototype.hasOwnProperty.call(agentConfigValues, agentReasoningConfig.id)
      ? agentConfigValues[agentReasoningConfig.id]
      : agentReasoningConfig && agentReasoningConfig.currentValue || ""
  );
  var modelLocked = agentManagedModels
    ? agentConfigLoading || !agentModelConfig || !(agentModelConfig.options || []).length
    : agentBindingLocked && !capSwitchModel;
  var modelName = wbcCurrentModel(chat, project, runtime, null);
  modelName = wbcFriendlyModelName(selectedModel, modelName);
  if (agentManagedModels) {
    modelName = agentSelectedModel && (agentSelectedModel.name || agentSelectedModel.value)
      || agentModelValue
      || (agentConfigLoading
        ? wbcT("workbenchChat.agentModelLoading", "Loading Agent models…")
        : wbcT("workbenchChat.agentModelUnavailable", "Agent did not provide model choices"));
  }
  var effectiveReasoningEffort = agentManagedModels ? agentReasoningValue : reasoningEffort;
  var effortLabel = effectiveReasoningEffort
    ? wbcT("settings.reasoningEffortValue." + effectiveReasoningEffort, effectiveReasoningEffort)
    : "";
  var modelButtonLabel = wbcT("workbenchChat.chooseModel", "Choose model")
    + ": " + modelName + (effortLabel ? " · " + effortLabel : "");
  var supportedReasoningEfforts = agentManagedModels
    ? (agentReasoningConfig && agentReasoningConfig.options || []).map(function (item) {
        return String(item.value || "");
      }).filter(Boolean)
    : wbcSupportedReasoningEfforts(selectedModel);

  function wbcTogglePersona() {
    if (!soulAvailable) return;
    var previous = personaOn;
    var next = !previous;
    setSoulActive(next);
    if (!chatId) return;
    model.updateChatPreferences(chatId, { soulActive: next }).then(function (nextChat) {
      if (chat && nextChat) Object.assign(chat, nextChat);
    }, function (err) {
        setSoulActive(previous);
        workbenchServices.api().toastError(err, wbcT("workbenchChat.personaFailed", "Failed to toggle persona: "));
      }).catch(function () {});
  }

  function wbcAddWorkspace(path) {
    if (!workspaceAvailable) return;
    var selectedPath = String(path || "").trim();
    var previousOverride = workspaceOverride;
    var previousActive = workspaceOn;
    setWorkspaceOverride(selectedPath && selectedPath !== projectWorkspacePath ? selectedPath : "");
    setWorkspaceActive(true);
    setContextState(function (prev) {
      if (!prev) return prev;
      var history = Array.isArray(prev.workspace_history) ? prev.workspace_history : [];
      if (selectedPath) {
        history = [selectedPath].concat(history.filter(function (item) { return item !== selectedPath; })).slice(0, 10);
      }
      return { ...prev, workspace_active: true, workspace_dir: selectedPath || prev.workspace_dir, workspace_history: history };
    });
    if (!chatId) return;
    model.updateChatPreferences(chatId, {
      workspaceActive: true,
      workspaceOverride: selectedPath && selectedPath !== projectWorkspacePath ? selectedPath : "",
    }).then(function (nextChat) {
      if (chat && nextChat) Object.assign(chat, nextChat);
    }, function (err) {
      setWorkspaceOverride(previousOverride);
      setWorkspaceActive(previousActive);
      workbenchServices.api().toastError(err, wbcT("workbenchChat.workspaceAddFailed", "Failed to add workspace: "));
    }).catch(function () {});
  }

  function wbcRemoveWorkspace() {
    if (!workspaceAvailable) return;
    var previous = workspaceOn;
    setWorkspaceActive(false);
    if (!chatId) return;
    model.updateChatPreferences(chatId, { workspaceActive: false })
      .then(function (nextChat) {
        if (chat && nextChat) Object.assign(chat, nextChat);
      }, function (err) {
        setWorkspaceActive(previous);
        workbenchServices.api().toastError(err, wbcT("workbenchChat.workspaceRemoveFailed", "Failed to remove workspace: "));
      }).catch(function () {});
  }

  function wbcPickWorkspace() {
    if (!workspaceAvailable) return;
    setToolsOpen(false);
    if (
      window.cyrene &&
      typeof window.cyrene.pickDirectory === "function"
    ) {
      window.cyrene.pickDirectory().then(function (data) {
        if (data && data.path) wbcAddWorkspace(data.path);
      }).catch(function (err) {
        workbenchServices.api().toastError(err, wbcT("workbenchChat.pickDirFailed", "Failed to open directory picker: "));
      });
      return;
    }
    workbenchServices.api().fetch("/api/context/pick-directory", { method: "POST" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (data) { if (data && data.path) wbcAddWorkspace(data.path); })
      .catch(function (err) {
        workbenchServices.api().toastError(err, wbcT("workbenchChat.pickDirFailed", "Failed to open directory picker: "));
      });
  }

  function wbcSaveRemoteContext(targetChatId, nextDeviceIds) {
    if (!remoteAvailable) {
      remoteDeviceIdsRef.current = [];
      setRemoteDeviceIds([]);
      return Promise.resolve();
    }
    var allowed = wbcAvailableContextIds(remoteDevices);
    var normalized = Array.from(new Set((nextDeviceIds || []).map(function (item) {
      return String(item || "");
    }).filter(function (identity) { return allowed.has(identity); })));
    remoteDeviceIdsRef.current = normalized;
    setRemoteDeviceIds(normalized);
    if (!targetChatId) {
      return Promise.resolve();
    }
    return model.updateChatPreferences(targetChatId, { remoteDeviceIds: normalized }).then(function (nextChat) {
      if (chat && chat.id === targetChatId && nextChat) Object.assign(chat, nextChat);
    }, function (err) {
      workbenchServices.api().toastError(err, wbcT("workbenchChat.remoteContextFailed", "Failed to update remote context: "));
      throw err;
    });
  }

  function wbcToggleRemoteDevice(deviceId) {
    var previousIds = remoteDeviceIds.slice();
    var selected = remoteDeviceIds.indexOf(deviceId) >= 0;
    var nextIds = selected
      ? remoteDeviceIds.filter(function (item) { return item !== deviceId; })
      : remoteDeviceIds.concat([deviceId]);
    wbcSaveRemoteContext(chatId, nextIds).catch(function () {
      remoteDeviceIdsRef.current = previousIds;
      setRemoteDeviceIds(previousIds);
    });
  }

  function wbcToggleContextActivation(kind, identity) {
    if (WBC_CONTEXT_ACTIVATION_KEYS.indexOf(kind) < 0 || !identity) return;
    if ((kind === "mcpServers" && !mcpAvailable)
        || (kind === "skills" && !skillsAvailable)
        || (kind === "pluginPacks" && !pluginPacksAvailable)) return;
    if (!wbcAvailableContextIds(contextCatalog[kind]).has(identity)) return;
    var previous = wbcNormalizeContextActivations(contextActivationsRef.current);
    var next = wbcNormalizeContextActivations(previous);
    var active = next[kind].indexOf(identity) >= 0;
    next[kind] = active
      ? next[kind].filter(function (item) { return item !== identity; })
      : next[kind].concat([identity]);
    contextActivationsRef.current = next;
    setContextActivations(next);
    if (!chatId) return;
    model.updateChatPreferences(chatId, { contextActivations: next }).then(function (nextChat) {
      if (chat && nextChat) Object.assign(chat, nextChat);
    }, function (err) {
      contextActivationsRef.current = previous;
      setContextActivations(previous);
      workbenchServices.api().toastError(err, wbcT("workbenchChat.contextCapabilitiesSaveFailed", "Failed to update context capabilities: "));
    }).catch(function () {});
  }
  // Steer is capability-driven: an Agent that cannot steer mid-run only offers
  // stop, so the running composer never fabricates a guidance send path.
  var canSteerWhileRunning = !running || capSteer;
  var hasRuntimeGuidance = running && !!draft.trim();
  if (!capSteer) hasRuntimeGuidance = false;
  var cancelUnsupported = running && !capCancel;
  // An Agent without interaction.cancel never shows a misleading Stop action;
  // the composer waits (read-only) until the Agent finishes (handoff §13).
  var waitingForAgent = running && !capCancel && !hasRuntimeGuidance;
  var showStopButton = running && !hasRuntimeGuidance && capCancel;
  var sendDisabled = awaitingAnswer || (running
    ? waitingForAgent
    : (!draft.trim() && attachments.length === 0 && !command) || (!!draft.trim() && !capText));
  // Self-management may prepare text in the current visible composer. Submit
  // is an explicit stable R2 action, so it is never inherited from the generic
  // DOM projection and always passes exact local-user delegation review.
  useWbcEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var unregister = [];
    unregister.push(uiSurface.register({
      node_id: "chat_composer_input",
      parent_id: "root",
      scope: "main",
      get_element: function () { return taRef.current; },
      get_highlight_element: function () { return composerBoxRef.current; },
      get_node: function () {
        if ((compact && running) || (chat && chat.pendingQuestion)) return null;
        var currentDraft = String(draftRef.current || "");
        return {
          role: "textbox",
          name: wbcT("workbenchChat.placeholder", "Message Cyrene..."),
          value_summary: currentDraft ? "Draft present" : "Empty draft",
          state: {
            session_id: String(chatId || ""),
            session_kind: "chat",
            draft_empty: !currentDraft,
            draft_length: currentDraft.length,
            running: running === true,
            submit_exposed: !sendDisabled,
          },
        };
      },
      actions: [{
        action_id: "set_value",
        kind: "set_value",
        risk: "R1",
        gesture_aliases: ["text_input"],
        input_schema: { value: "text<=20000" },
      }, {
        action_id: "clear_value",
        kind: "set_value",
        risk: "R1",
        gesture_aliases: ["semantic_clear"],
        input_schema: { expected_value: "text<=20000" },
      }],
      handlers: {
        set_value: function (input) {
          var currentDraft = String(draftRef.current || "");
          var nextDraft = String(input.value || "");
          if (currentDraft && currentDraft !== nextDraft) {
            throw new Error("composer draft is not empty");
          }
          draftRef.current = nextDraft;
          setDraft(nextDraft);
          return { draft_length: nextDraft.length, submitted: false };
        },
        clear_value: function (input) {
          var currentDraft = String(draftRef.current || "");
          if (currentDraft !== String(input.expected_value || "")) {
            throw new Error("composer draft changed");
          }
          draftRef.current = "";
          setDraft("");
          return { draft_length: 0, cleared: true, submitted: false };
        },
      },
    }));
    var submitMode = running && !hasRuntimeGuidance ? "interrupt" : (running ? "guidance" : "send");
    var submitRisk = submitMode === "interrupt" ? "R1" : "R2";
    var submitActionId = submitMode === "interrupt" ? "interrupt" : "submit";
    unregister.push(uiSurface.register({
      node_id: "chat_composer_submit",
      parent_id: "root",
      scope: "main",
      get_element: function () { return sendButtonRef.current; },
      get_node: function () {
        if ((compact && running) || (chat && chat.pendingQuestion)) return null;
        return {
          role: "button",
          name: submitMode === "interrupt"
            ? wbcT("workbenchChat.stop", "Stop")
            : submitMode === "guidance"
              ? wbcT("workbenchChat.sendGuidance", "Send guidance")
              : wbcT("workbenchChat.send", "Send"),
          state: {
            session_id: String(chatId || ""),
            session_kind: "chat",
            mode: submitMode,
            disabled: !!sendDisabled,
          },
        };
      },
      actions: sendDisabled ? [] : [{
        action_id: submitActionId,
        kind: "invoke",
        risk: submitRisk,
        gesture_aliases: ["press", "keyboard"],
        outcome: {
          effect: submitMode === "interrupt" ? "interrupts_current_run" : "submits_current_composer",
          target_scope: "chat",
          inspect_after: true,
        },
      }],
      handlers: {
        interrupt: function () {
          var button = sendButtonRef.current;
          if (!button || button.disabled) throw new Error("composer interrupt is unavailable");
          button.click();
        },
        submit: function () {
          var button = sendButtonRef.current;
          if (!button || button.disabled) throw new Error("composer submit is unavailable");
          button.click();
        },
      },
    }));
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [chatId, compact, running, chat && chat.pendingQuestion, hasRuntimeGuidance, sendDisabled]);

  return (
    <div className={"wbc-composer" + (compact ? " compact" : "")} data-tour="chat_composer">
      {activeCommand && (
        <div className="wbc-command-row">
          <span className="wbc-command-chip">
            {WBC_ICONS.slash}
            {activeCommand.label}
            <button type="button" disabled={awaitingAnswer} onClick={function () { setCommand(""); }} aria-label={wbcT("workbenchChat.removeCommand", "Remove command")}>{WBC_ICONS.x}</button>
          </span>
        </div>
      )}
      <div
        ref={composerBoxRef}
        className={"wbc-composer-box" + (agentFlow ? (" agent-flow agent-flow-" + agentFlow) : "")}
        data-agent-flow={agentFlow || undefined}
      >
        {topOverlay}
        {attachments.length > 0 && (
          <div className="wbc-attach-row">
            {attachments.map(function (file, i) {
              var isImg = file.kind === "image" || String(file.content_type || "").indexOf("image") === 0;
              var attachmentKey = String(file.id || file.url || i);
              var showImagePreview = isImg && file.url && !failedImagePreviews[attachmentKey];
              return (
                <div className={"wbc-attach-card" + (showImagePreview ? " image" : " file")} key={attachmentKey}>
                  {showImagePreview
                    ? <img src={file.url} alt="" onError={function () {
                        setFailedImagePreviews(function (prev) {
                          return Object.assign({}, prev, { [attachmentKey]: true });
                        });
                      }} />
                    : <>
                        <WbcFileVisual file={file} className="wbc-composer-file-visual" />
                        <span className="wbc-attach-file-meta">
                          <b title={file.name}>{file.name || "file"}</b>
                          <small>{wbcAttachmentTypeLabel(file)}</small>
                        </span>
                      </>}
                  <button type="button" className="wbc-attach-x" disabled={awaitingAnswer} onClick={function () {
                    setAttachments(attachments.filter(function (_f, idx) { return idx !== i; }));
                  }} aria-label={wbcT("workbenchChat.removeAttachment", "Remove attachment")}>{WBC_ICONS.x}</button>
                </div>
              );
            })}
          </div>
        )}
        <textarea
          ref={taRef}
          className="wbc-composer-textarea"
          aria-label={capText ? (running
            ? (runningPlaceholder || wbcT("workbenchChat.placeholderRunning", "Send guidance to the running agent..."))
            : (placeholder || wbcT("workbenchChat.placeholder", "Message Cyrene...")))
            : wbcT("workbenchChat.capability.noText", "This Agent does not support text input")}
          value={draft}
          rows={compact ? 1 : 2}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={slashDraftOpen}
          aria-controls={slashDraftOpen ? "wbc-command-listbox" : undefined}
          aria-activedescendant={slashDraftOpen && slashItems[slashActiveIndex] ? wbcCommandOptionId(slashItems[slashActiveIndex].id) : undefined}
          disabled={awaitingAnswer || !capText || (running && !capSteer) || cancelUnsupported}
          onChange={function (e) {
            draftRef.current = e.target.value;
            setDraft(e.target.value);
            setSlashDismissedDraft("");
            setSlashActiveIndex(0);
          }}
          onBlur={persistCurrentDraft}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={!capText
            ? wbcT("workbenchChat.capability.noText", "This Agent does not support text input")
            : cancelUnsupported
              ? wbcT("workbenchChat.capability.waitForAgent", "Waiting for the Agent to finish…")
              : running
                ? (capSteer ? (runningPlaceholder || wbcT("workbenchChat.placeholderRunning", "Send guidance to the running agent...")) : wbcT("workbenchChat.capability.waitForAgent", "Waiting for the Agent to finish…"))
                : (placeholder || wbcT("workbenchChat.placeholder", "Message Cyrene..."))}
        />
        <div className="wbc-composer-actions">
          <input ref={fileRef} type="file" multiple accept={!capFile && capImage ? "image/*" : undefined} style={{ display: "none" }} onChange={onFilePick} />
          {(capFile || capImage) && (
            <button type="button" data-tour="chat_attach" className="wbc-composer-icon" title={uploading ? wbcT("workbenchChat.uploading", "Uploading...") : wbcT("workbenchChat.addAttachment", "Add attachment")} disabled={uploading || running || awaitingAnswer} onClick={pickFiles}>
              {uploading ? <span className="wb-spinner small" /> : WBC_ICONS.attach}
            </button>
          )}
          {!compact && (
            <span className="wbc-pop-anchor wbc-tools-anchor" ref={toolsPickerRef}>
              {composerContextAvailable ? <button
                type="button"
                data-tour="chat_tools"
                className={"wbc-composer-icon wbc-tools-trigger" + (enabledContentCount > 0 ? " has-content" : "") + (showToolsMenu ? " active" : "")}
                title={wbcT("workbenchChat.toolsCount", "Tools · {count} enabled", { count: enabledContentCount })}
                aria-label={wbcT("workbenchChat.toolsCount", "Tools · {count} enabled", { count: enabledContentCount })}
                aria-haspopup="menu"
                aria-expanded={showToolsMenu}
                disabled={running || awaitingAnswer}
                onClick={function () {
                  setToolsOpen(!toolsOpen);
                  setModelOpen(false);
                }}
              >
                <span className="wbc-tools-trigger-icon" aria-hidden="true">{WBC_ICONS.layers}</span>
                {enabledContentCount > 0 ? <span className="wbc-tools-trigger-count" aria-hidden="true">{enabledContentCount}</span> : null}
              </button> : null}
              {showToolsMenu && (
                <div className="wbc-popmenu wbc-tools-menu" role={toolsOpen ? "menu" : "presentation"}>
                  {toolsOpen && <>
                  <section
                    className="wbc-tools-section"
                    aria-label={wbcT("workbenchChat.contentItems", "Content")}
                  >
                    <div className="wbc-tools-section-title">
                      {wbcT("workbenchChat.contentItems", "Content")}
                    </div>
                    <div className="wbc-tools-content-list">
                      {soulAvailable ? <button type="button" className={"wbc-tools-enabled-row" + (personaOn ? " active" : "")} role="menuitemcheckbox" aria-checked={personaOn} onClick={wbcTogglePersona}>
                        <span className="wbc-tools-row-icon">{WBC_ICONS.spark}</span>
                        <span className="wbc-tools-row-copy">
                          <span>{wbcT("workbenchChat.persona", "Persona")}</span>
                          <small>{wbcT("workbenchChat.personaDescription", "Cyrene persona settings")}</small>
                        </span>
                        {personaOn ? <span className="wbc-tools-row-check">{WBC_ICONS.check}</span> : null}
                      </button> : null}
                      {workspaceAvailable && workspaceOptions.map(function (option) {
                        var selected = workspaceOn && option.path === wsDir;
                        return (
                          <button key={option.path} type="button" className={"wbc-tools-enabled-row" + (selected ? " active" : "")} role="menuitemcheckbox" aria-checked={selected} onClick={function () {
                            if (selected) wbcRemoveWorkspace();
                            else wbcAddWorkspace(option.path);
                          }}>
                            <span className="wbc-tools-row-icon">{WBC_ICONS.folder}</span>
                            <span className="wbc-tools-row-copy">
                              <span>{option.isDefault ? wbcT("workbenchChat.defaultWorkspace", "Default workspace") : (wbcWorkspaceDisplayName(option.path) || wbcT("workbenchChat.workspacePath", "Workspace path"))}</span>
                              <small title={option.path}>{option.path}</small>
                            </span>
                            {selected ? <span className="wbc-tools-row-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                      {workspaceAvailable ? <button type="button" className="wbc-tools-enabled-row wbc-tools-choose-row" role="menuitem" onClick={wbcPickWorkspace}>
                        <span className="wbc-tools-row-icon">{WBC_ICONS.plus}</span>
                        <span className="wbc-tools-row-copy"><span>{wbcT("workbenchChat.chooseDirectory", "Choose directory…")}</span></span>
                      </button> : null}
                      {remoteAvailable && remoteDevices.map(function (device) {
                        var deviceId = String(device.id || "");
                        var selected = remoteDeviceIds.indexOf(deviceId) >= 0;
                        var eligible = device.available === true;
                        var stateLabel = eligible
                          ? wbcT("workbenchChat.remoteDeviceHint", "{count} granted capabilities", { count: (device.capabilities || []).length })
                          : wbcT("workbenchChat.contextCapabilityDisabled", "Disabled in settings");
                        return (
                          <button key={deviceId} type="button" disabled={!eligible} className={"wbc-tools-enabled-row" + (selected ? " active" : "")} role="menuitemcheckbox" aria-checked={selected} onClick={function () { wbcToggleRemoteDevice(deviceId); }}>
                            <span className="wbc-tools-row-icon">{WBC_ICONS.device}</span>
                            <span className="wbc-tools-row-copy">
                              <span>{device.name || deviceId}</span>
                              <small>{stateLabel}</small>
                            </span>
                            {selected ? <span className="wbc-tools-row-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                    </div>
                  </section>
                  {composerContextAvailable ? <section className="wbc-tools-section wbc-tools-context-capabilities" aria-label={wbcT("workbenchChat.contextCapabilities", "Context capabilities")}>
                    <div className="wbc-tools-section-title">
                      {wbcT("workbenchChat.contextCapabilities", "Context capabilities")}
                    </div>
                    {!builtinContextCapabilities ? (
                      <div className="wbc-tools-empty">{wbcT("workbenchChat.contextCapabilitiesBuiltinOnly", "Context capabilities are available with the built-in Cyrene Agent.")}</div>
                    ) : contextCatalogLoading && !contextCatalogLoaded ? (
                      <div className="wbc-tools-context-loading" role="status"><span className="wb-spinner small" />{wbcT("workbenchChat.contextCapabilitiesLoading", "Loading capabilities…")}</div>
                    ) : !contextResponseAvailable ? (
                      <div className="wbc-tools-empty">{wbcT("workbenchChat.contextCapabilitiesEmpty", "Nothing available in this category.")}</div>
                    ) : (
                      <div className="wbc-tools-context-categories">
                        {contextCapabilityCategories.map(function (category) {
                          var activeCount = contextActivations[category.key].length;
                          var availableCount = category.items.filter(function (item) { return item && item.available === true; }).length;
                          var expanded = contextCatalogPanel === category.key;
                          return (
                            <div className="wbc-tools-context-category" key={category.key}>
                              <button type="button" className={"wbc-tools-enabled-row wbc-tools-context-category-button" + (activeCount ? " active" : "")} aria-expanded={expanded} aria-controls={"wbc-context-category-" + category.key} onClick={function () { setContextCatalogPanel(expanded ? "" : category.key); }}>
                                <span className="wbc-tools-row-icon">{category.icon}</span>
                                <span className="wbc-tools-row-copy">
                                  <span>{category.label}</span>
                                  <small>{activeCount
                                    ? wbcT("workbenchChat.contextCapabilitiesActive", "{count} active", { count: activeCount })
                                    : wbcT("workbenchChat.contextCapabilitiesAvailable", "{count} available", { count: availableCount })}</small>
                                </span>
                                <span className="wbc-tools-context-chevron" aria-hidden="true">{expanded ? WBC_ICONS.chevronDown : WBC_ICONS.chevronRight}</span>
                              </button>
                              {expanded && (
                                <div className="wbc-tools-context-options" id={"wbc-context-category-" + category.key} role="group" aria-label={category.label}>
                                  {category.items.length ? category.items.map(function (item) {
                                    var identity = String(item.id || "");
                                    var selected = contextActivations[category.key].indexOf(identity) >= 0;
                                    var itemLabel = category.key === "pluginPacks"
                                      ? wbcAuthoredContextTranslation(item, "name") || wbcT("toolName." + identity, item.name || identity)
                                      : String(item.name || identity);
                                    var itemDescription = category.key === "pluginPacks"
                                      ? wbcAuthoredContextTranslation(item, "description") || wbcT("pluginPackDesc." + identity, item.description || "")
                                      : String(item.description || item.status || "");
                                    return (
                                      <button key={identity} type="button" disabled={item.available !== true} className={"wbc-tools-enabled-row wbc-tools-context-option" + (selected ? " active" : "")} role="menuitemcheckbox" aria-checked={selected} title={itemDescription} onClick={function () { wbcToggleContextActivation(category.key, identity); }}>
                                        <span className="wbc-tools-row-icon">{category.icon}</span>
                                        <span className="wbc-tools-row-copy"><span>{itemLabel}</span><small>{itemDescription || (item.available !== true ? wbcT("workbenchChat.contextCapabilityDisabled", "Disabled in settings") : identity)}</small></span>
                                        {selected ? <span className="wbc-tools-row-check">{WBC_ICONS.check}</span> : null}
                                      </button>
                                    );
                                  }) : <div className="wbc-tools-empty">{wbcT("workbenchChat.contextCapabilitiesEmpty", "Nothing available in this category.")}</div>}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </section> : null}
                  </>}
                  <section
                    className="wbc-tools-section wbc-tools-commands"
                    aria-label={wbcT("workbenchChat.composer.commandMenu", "Commands")}
                  >
                    <div className="wbc-tools-section-title wbc-tools-command-title">
                      <span>{wbcT("workbenchChat.commands", "Commands")}</span>
                      {slashCommandCatalogLoading ? <span className="wb-spinner small" role="status" aria-label={wbcT("workbenchChat.contextCapabilitiesLoading", "Loading capabilities…")} /> : null}
                    </div>
                    <div className="wbc-tools-command-grid" id="wbc-command-listbox" role="listbox" aria-label={wbcT("workbenchChat.composer.commandMenu", "Commands")}>
                      {slashItems.map(function (c) {
                        var on = command === c.id;
                        var keyboardActive = slashDraftOpen && slashItems[slashActiveIndex] && slashItems[slashActiveIndex].id === c.id;
                        return (
                          <button id={wbcCommandOptionId(c.id)} key={c.id} type="button" title={c.desc} aria-label={c.label + ": " + c.desc} className={"wbc-tools-command" + (on ? " active" : "") + (keyboardActive ? " keyboard-active" : "")} role="option" aria-selected={on || keyboardActive} onMouseEnter={function () { setSlashActiveIndex(slashItems.indexOf(c)); }} onClick={function () {
                            if (on) setCommand("");
                            else chooseSlashCommand(c);
                          }}>
                            <span className="wbc-tools-command-icon">{slashCommandIcon(c)}</span>
                            <span className="wbc-tools-command-copy"><span>{c.label}</span>{c.group && c.group !== "workflow" ? <small>/{c.id}</small> : null}</span>
                            {on ? <span className="wbc-tools-command-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                    </div>
                  </section>
                </div>
              )}
            </span>
          )}
          <span className="wbc-composer-spacer" />{!compact && chatId ? <WbcComposerContextIndicator chat={chat} runtime={runtime} running={running} /> : null}
          {!compact && modelName ? (
            <span className="wbc-pop-anchor wbc-model-anchor" ref={modelPickerRef}>
              <button
                type="button"
                data-tour="chat_model_picker"
                className={"wbc-model-button" + (modelOpen ? " active" : "")}
                title={modelButtonLabel}
                aria-label={modelButtonLabel}
                aria-haspopup="menu"
                aria-expanded={modelOpen}
                disabled={running || awaitingAnswer}
                onClick={function () {
                  setModelOpen(!modelOpen);
                  setModelPanel("root");
                  setToolsOpen(false);
                }}
              >
                <span className="wbc-model-button-icon" aria-hidden="true">{WBC_ICONS.model}</span>
                <span className="wbc-model-button-name">{modelName}</span>
                {!agentManagedModels && effortLabel ? <span className="wbc-model-button-effort">{effortLabel}</span> : null}
                <span className="wbc-model-button-chevron">{WBC_ICONS.chevronDown}</span>
              </button>
              {modelOpen && !awaitingAnswer && (
                <div className="wbc-popmenu wbc-model-menu" role="menu">
                  {modelPanel === "root" && (
                    <>
                      {agentPickerEnabled && (
                        <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("agents"); }}>
                          <span className="wbc-model-menu-key">{wbcT("workbenchChat.agent", "Agent")}</span>
                          <span className="wbc-model-menu-value wbc-model-menu-agent-name">{effectiveAgentName}</span>
                          <span className="wbc-model-menu-chevron">{WBC_ICONS.chevronRight}</span>
                        </button>
                      )}
                      <button type="button" className={"wbc-model-menu-row" + (modelLocked ? " locked" : "")} disabled={modelLocked} aria-disabled={modelLocked ? "true" : undefined} onClick={modelLocked ? undefined : function () { setModelPanel("models"); }}>
                        <span className="wbc-model-menu-key">{wbcT("workbenchChat.model", "Model")}</span>
                        <span className="wbc-model-menu-value">{modelName}</span>
                        {!modelLocked ? <span className="wbc-model-menu-chevron">{WBC_ICONS.chevronRight}</span> : null}
                      </button>
                      {capReasoningEffort && supportedReasoningEfforts.length > 0 && (
                        <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("effort"); }}>
                          <span className="wbc-model-menu-key">{wbcT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                          <span className="wbc-model-menu-value">{effortLabel || "—"}</span>
                          <span className="wbc-model-menu-chevron">{WBC_ICONS.chevronRight}</span>
                        </button>
                      )}
                      {permissionModeVisible && (permissionAgentDefined ? (
                        <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("root"); }}>
                          <span className="wbc-model-menu-key">{wbcT("workbenchChat.permissionMode", "Permission mode")}</span>
                          <span className="wbc-model-menu-value">{wbcT("workbenchChat.permissionAgentManaged", "Managed by Agent")}</span>
                          <span className="wbc-model-menu-chevron">{WBC_ICONS.chevronRight}</span>
                        </button>
                      ) : (
                        <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("permission"); }}>
                          <span className="wbc-model-menu-key">{wbcT("workbenchChat.permissionMode", "Permission mode")}</span>
                          <span className="wbc-model-menu-value">{currentMode.label}</span>
                          <span className="wbc-model-menu-chevron">{WBC_ICONS.chevronRight}</span>
                        </button>
                      ))}
                    </>
                  )}
                  {modelPanel === "agents" && agentPickerEnabled && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{WBC_ICONS.chevronLeft}</span>
                        <span>{wbcT("workbenchChat.agent", "Agent")}</span>
                      </button>
                      {agentBindingLocked ? (
                        <div className="wbc-agent-menu-note" role="status">
                          {wbcT("workbenchChat.agentLockedNote", "This conversation is bound to its Agent. Choose another Agent to continue in a new chat.")}
                        </div>
                      ) : null}
                      <div className="wbc-agent-menu-group">
                        <div className="wbc-agent-menu-group-title">{wbcT("workbenchChat.agentGroup.builtin", "Cyrene built-in")}</div>
                        {wbcComposerAgentRow({
                          key: "builtin",
                          agent: { installationId: WBC_BUILTIN_AGENT_INSTALLATION, agentId: WBC_BUILTIN_AGENT_ID, displayName: "Cyrene", builtin: true, installState: "installed", authState: "connected", runtimeState: "ready" },
                          active: effectiveAgent.installationId === WBC_BUILTIN_AGENT_INSTALLATION,
                          locked: false,
                          canPick: true,
                          onPick: function () {
                            var binding = wbcDefaultAgentBinding();
                            pickAgentBinding(binding);
                            setModelOpen(false); setModelPanel("root");
                          },
                          onOpen: onOpenAgentDetail,
                        })}
                      </div>
                      <div className="wbc-agent-menu-group">
                        <div className="wbc-agent-menu-group-title">{wbcT("workbenchChat.agentGroup.installed", "Installed Agents")}</div>
                        {agentOptions.filter(function (agent) { return !wbcIsBuiltinAgent(agent); }).map(function (agent) {
                          var availability = wbcAgentAvailability(agent);
                          var active = String(effectiveAgent.installationId || "") === String(agent.installationId || "");
                          return wbcComposerAgentRow({
                            key: String(agent.installationId || agent.agentId || ""),
                            agent: agent,
                            active: active,
                            locked: false,
                            availability: availability,
                            canPick: availability.state === "available",
                            onPick: function () {
                              var binding = {
                                agent: { installationId: String(agent.installationId || "") },
                                modelAccess: agent.modelAccess && agent.modelAccess.mode
                                  ? { mode: String(agent.modelAccess.mode), profileId: String(agent.modelAccess.profileId || "primary") }
                                  : { mode: "cyrene_managed", profileId: "primary" },
                              };
                              pickAgentBinding(binding);
                              setModelOpen(false); setModelPanel("root");
                            },
                            onOpen: onOpenAgentDetail,
                          });
                        })}
                        {agentOptionsLoaded && agentOptions.filter(function (agent) { return !wbcIsBuiltinAgent(agent); }).length === 0 ? (
                          <div className="wbc-agent-menu-empty">{wbcT("workbenchChat.agentGroup.installedEmpty", "No installed Agents yet — install one from Extensions.")}</div>
                        ) : null}
                      </div>
                    </>
                  )}
                  {modelPanel === "models" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{WBC_ICONS.chevronLeft}</span>
                        <span>{wbcT("workbenchChat.model", "Model")}</span>
                      </button>
                      {modelLocked ? (
                        <div className="wbc-agent-menu-note" role="status">
                          {wbcT("workbenchChat.modelLockedNote", "This Agent does not support switching models after the first message.")}
                        </div>
                      ) : null}
                      {(agentManagedModels ? (agentModelConfig && agentModelConfig.options || []) : configuredModels).map(function (item) {
                        var id = String(agentManagedModels ? item.value || "" : item.id || item.model || "");
                        var active = agentManagedModels ? id === agentModelValue : id === selectedModelId;
                        return (
                          <button key={id} type="button" className={active ? "active" : ""} disabled={modelLocked} aria-disabled={modelLocked ? "true" : undefined} onClick={modelLocked ? undefined : function () {
                            if (agentManagedModels) {
                              var nextValues = Object.assign({}, agentConfigValues, { [agentModelConfig.id]: id });
                              setAgentConfigValues(nextValues);
                              wbcPublishChatModelChanged(chatId, item, { refresh: false });
                              model.updateAgentConfigValues(chatId, { [agentModelConfig.id]: id }).then(function (nextChat) {
                                if (nextChat && nextChat.agentConfigValues) setAgentConfigValues(nextChat.agentConfigValues);
                                wbcPublishChatModelChanged(chatId, Object.assign({}, item, {
                                  model: nextChat && nextChat.model || item.name || item.value,
                                }));
                                return model.getAgentConfigOptions(chatId);
                              }).then(function (payload) {
                                if (!payload) return;
                                setAgentConfigOptions(Array.isArray(payload.configOptions) ? payload.configOptions : []);
                                setAgentConfigValues(payload.values && typeof payload.values === "object" ? payload.values : {});
                              }).catch(function () {
                                setAgentConfigValues(agentConfigValues);
                                wbcPublishChatModelChanged(chatId, {}, { refresh: true });
                              });
                            } else {
                              setSelectedModelId(id);
                              var nextEffort = wbcReasoningEffortForModel(item, "");
                              setReasoningEffort(nextEffort);
                              wbcPublishChatModelChanged(chatId, item, { refresh: false });
                              if (chatId) {
                                model.updateChatPreferences(chatId, { model: id, reasoningEffort: nextEffort }).then(function (nextChat) {
                                  if (chat && nextChat) Object.assign(chat, nextChat);
                                  wbcPublishChatModelChanged(chatId, Object.assign({}, item, {
                                    model: nextChat && nextChat.model || item.model || item.name,
                                  }));
                                }).catch(function () {
                                  wbcPublishChatModelChanged(chatId, {}, { refresh: true });
                                });
                              }
                            }
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{item.name || item.model || item.value}</span>
                            {wbcLocalizedModelDescription(item) ? <span className="wbc-popmenu-desc">{wbcLocalizedModelDescription(item)}</span> : null}
                            {active ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                      {agentManagedModels && !agentConfigLoading && (!agentModelConfig || !(agentModelConfig.options || []).length) ? (
                        <div className="wbc-agent-menu-empty">{wbcT("workbenchChat.agentModelUnavailable", "This Agent did not provide model choices.")}</div>
                      ) : null}
                    </>
                  )}
                  {modelPanel === "effort" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{WBC_ICONS.chevronLeft}</span>
                        <span>{wbcT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                      </button>
                      {supportedReasoningEfforts.map(function (effort) {
                        var active = effort === effectiveReasoningEffort;
                        return (
                          <button key={effort} type="button" className={active ? "active" : ""} onClick={function () {
                            if (agentManagedModels && agentReasoningConfig) {
                              var previousValues = agentConfigValues;
                              var nextValues = Object.assign({}, previousValues, { [agentReasoningConfig.id]: effort });
                              setAgentConfigValues(nextValues);
                              model.updateAgentConfigValues(chatId, { [agentReasoningConfig.id]: effort }).then(function (nextChat) {
                                if (nextChat && nextChat.agentConfigValues) setAgentConfigValues(nextChat.agentConfigValues);
                              }).catch(function () {
                                setAgentConfigValues(previousValues);
                              });
                            } else {
                              setReasoningEffort(effort);
                              if (chatId) {
                                model.updateChatPreferences(chatId, { reasoningEffort: effort }).then(function (nextChat) {
                                  if (chat && nextChat) Object.assign(chat, nextChat);
                                }).catch(function () {});
                              }
                            }
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{wbcT("settings.reasoningEffortValue." + effort, effort)}</span>
                            {active ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                  {modelPanel === "permission" && permissionCapability === "supported" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{WBC_ICONS.chevronLeft}</span>
                        <span>{wbcT("workbenchChat.permissionMode", "Permission mode")}</span>
                      </button>
                      {translatedModes.map(function (item) {
                        var active = mode === item.id;
                        return (
                          <button key={item.id} type="button" className={active ? "active" : ""} onClick={function () {
                            setMode(item.id);
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{item.label}</span>
                            <span className="wbc-popmenu-desc">{item.desc}</span>
                            {active ? <span className="wbc-popmenu-check">{WBC_ICONS.check}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                </div>
              )}
            </span>
          ) : null}
          {!compact && capAudio && voiceSnapshot.status.asr_ready ? (
            <button
              type="button"
              data-tour="chat_voice"
              className={"wbc-composer-icon wbc-voice-input" + (voicePhase ? " " + voicePhase : "")}
              onClick={toggleVoiceInput}
              disabled={awaitingAnswer || voicePhase === "starting" || voicePhase === "transcribing"}
              title={voicePhase === "recording"
                ? (voiceSnapshot.status.auto_stop_on_silence !== false
                    ? wbcT("workbenchChat.voiceInputAutoStop", "Recording · pauses automatically start recognition")
                    : wbcT("workbenchChat.voiceInputStop", "Stop recording"))
                : voicePhase === "starting"
                  ? wbcT("workbenchChat.voiceInputStarting", "Accessing microphone…")
                  : voicePhase === "transcribing"
                  ? wbcT("workbenchChat.voiceTranscribing", "Recognizing speech…")
                  : wbcT("workbenchChat.voiceInputStart", "Voice input")}
              aria-label={voicePhase === "recording"
                ? wbcT("workbenchChat.voiceInputStop", "Stop recording")
                : wbcT("workbenchChat.voiceInputStart", "Voice input")}
              aria-pressed={voicePhase === "recording"}
              aria-busy={voicePhase === "starting" || voicePhase === "transcribing"}
            >
              {voicePhase === "starting" || voicePhase === "transcribing"
                ? <span className="wb-spinner small" />
                : ComposerBrowserIcon ? <ComposerBrowserIcon name="microphone" size={16} /> : null}
            </button>
          ) : null}
          <button
            ref={sendButtonRef}
            type="button"
            className={"wbc-send" + (showStopButton ? " stop" : "")}
            onClick={running && !hasRuntimeGuidance ? onInterrupt : submit}
            disabled={sendDisabled}
            title={running
              ? (hasRuntimeGuidance
                  ? wbcT("workbenchChat.sendGuidance", "Send guidance")
                  : showStopButton
                    ? wbcT("workbenchChat.stop", "Stop")
                    : wbcT("workbenchChat.capability.waitForAgent", "Waiting for the Agent to finish…"))
              : wbcT("workbenchChat.send", "Send")}
            aria-label={running
              ? (hasRuntimeGuidance
                  ? wbcT("workbenchChat.sendGuidance", "Send guidance")
                  : showStopButton
                    ? wbcT("workbenchChat.stop", "Stop")
                    : wbcT("workbenchChat.capability.waitForAgent", "Waiting for the Agent to finish…"))
              : wbcT("workbenchChat.send", "Send")}
          >
            {showStopButton ? WBC_ICONS.stop : WBC_ICONS.send}
          </button>
        </div>
      </div>
    </div>
  );
}

// Shared with the quick-chat surface (workbench-quick-chat.jsx), which renders
// the exact same composer (attachments, commands, permission mode, IME-safe
// send) rather than forking a second input box.
// Also shared with the quick-chat surface so its transcript renders with the
// exact same message cards (tool-call traces, agent files, attachments, the live
// "thinking/calling tools" card) as the main conversation instead of a
// simplified text bubble. They are self-contained (only module-level helpers +
// optional callbacks), so the quick-chat window can mount them standalone.
// Clears a persisted draft + attachments for one chat in a given namespace.
// The quick-chat window keeps its draft on a failed send (clearOnSend=false),
// so it calls this on success to wipe the namespaced draft before remounting.
function wbcClearComposerDraft(chatId, ns) {
  wbcSaveDraft(chatId, "", ns);
  wbcSaveAttachments(chatId, [], ns);
}

// Context picker popup controls now live directly in WbcComposer's single tools menu.

// ---------------------------------------------------------------------------
// Branch tree (fork lineage navigator)
// ---------------------------------------------------------------------------

// Resolve the fork lineage the active chat belongs to. Walks up
// forkedFromChatId to the lineage root, then confirms the lineage spans more
// than one chat (a lone conversation has no branches to show). Returns
// { root, byId, children } or null when there's nothing to draw.
function wbcBranchLineage(chats, activeChatId) {
  if (!activeChatId || !Array.isArray(chats) || !chats.length) return null;
  var byId = {};
  chats.forEach(function (c) { if (c && c.id) byId[c.id] = c; });
  var active = byId[activeChatId];
  if (!active) return null;
  var children = {};
  chats.forEach(function (c) {
    var parent = c && c.forkedFromChatId;
    if (parent && byId[parent]) (children[parent] = children[parent] || []).push(c);
  });
  Object.keys(children).forEach(function (key) {
    children[key].sort(function (a, b) {
      return String(a.createdAt || "").localeCompare(String(b.createdAt || ""));
    });
  });
  // Climb to the lineage root (a missing/deleted parent terminates the walk).
  var root = active;
  var guard = 0;
  while (root.forkedFromChatId && byId[root.forkedFromChatId] && guard < 500) {
    root = byId[root.forkedFromChatId];
    guard += 1;
  }
  var size = 0;
  (function count(node) {
    size += 1;
    (children[node.id] || []).forEach(count);
  })(root);
  if (size < 2) return null;
  return { root: root, byId: byId, children: children };
}

// Flatten a lineage into render rows via DFS. Each chat is a vertical lane at
// its depth: a head row (root start or fork divergence) and, once it has a
// reply, a tip row (its latest message). Children nest between the two at
// depth+1. Per-row line flags drive the connectors so the lane stays unbroken:
//   lineDown — own column runs from the dot to the row bottom (head with more
//              below it); lineUp — own column runs from the top into the dot
//              (tip closing the lane); elbow — horizontal join from the parent
//              column (only a fork head taps its parent's trunk).
function wbcBranchRows(lineage) {
  if (!lineage) return [];
  var rows = [];
  (function walk(chat, depth, isRoot) {
    var children = lineage.children[chat.id] || [];
    var head = isRoot
      ? String(chat.firstMessage || chat.preview || "")
      : String(chat.forkMessage || chat.firstMessage || chat.preview || "");
    var tip = String(chat.preview || "");
    // A branch with no reply yet has tip === head; render only the head node.
    var hasTip = !!(tip && tip !== head);
    var hasKids = children.length > 0;
    rows.push({
      chatId: chat.id, kind: isRoot ? "root" : "fork", depth: depth,
      text: head, title: chat.title, isHead: true,
      lineUp: false, lineDown: hasTip || hasKids, elbow: depth > 0,
    });
    children.forEach(function (child) { walk(child, depth + 1, false); });
    if (hasTip) {
      rows.push({
        chatId: chat.id, kind: "tip", depth: depth,
        text: tip, title: chat.title, isHead: false,
        lineUp: true, lineDown: false, elbow: false,
      });
    }
  })(lineage.root, 0, true);
  return rows;
}

// Connector segments for one compact Git-style row. Each depth gets a narrow
// lane; the root lane uses the source-control blue while nested lanes use the
// Workbench accent. Keeping the tone on each segment lets a fork stay readable
// without adding cards, badges, or other decoration around the row.
function wbcBranchConnectors(row) {
  var U = 14, CY = 28, BASE = 14, CURVE_W = 14, CURVE_H = 24, d = row.depth;
  function cx(col) { return col * U + BASE; }
  function tone(col) { return col === 0 ? "main-lane" : "fork-lane"; }
  var segs = [];
  for (var c = 0; c < d; c += 1) {
    segs.push({ cls: "v " + tone(c), style: { left: cx(c) + "px", top: 0, bottom: 0 } });
  }
  if (row.lineDown) segs.push({ cls: "v " + tone(d), style: { left: cx(d) + "px", top: CY + "px", bottom: 0 } });
  if (row.lineUp) segs.push({ cls: "v " + tone(d), style: { left: cx(d) + "px", top: 0, height: CY + "px" } });
  if (row.elbow) {
    var nodeX = cx(d), parentX = cx(d - 1);
    var curveWidth = Math.min(CURVE_W, nodeX - parentX);
    var straightWidth = nodeX - curveWidth - parentX;
    if (straightWidth > 0) {
      segs.push({ cls: "h fork-lane", style: { left: parentX + "px", top: (CY - CURVE_H) + "px", width: (straightWidth + 1) + "px" } });
    }
    segs.push({
      cls: "arc fork-lane",
      style: {
        left: (nodeX - curveWidth) + "px",
        top: (CY - CURVE_H) + "px",
        width: curveWidth + "px",
        height: CURVE_H + "px",
      },
    });
  }
  return segs;
}

function wbcBranchKindLabel(kind) {
  if (kind === "root") return wbcT("workbenchChat.branchStart", "Start");
  if (kind === "tip") return wbcT("workbenchChat.branchEnd", "Latest");
  return wbcT("workbenchChat.branchFork", "Branch");
}

function wbcBrowserStateForChat(chatId) {
  var id = String(chatId || "").trim();
  if (!id) return {};
  var dataState = workbenchServices.data().state;
  var byChat = dataState.browserByChat || {};
  if (byChat[id]) return byChat[id];
  var browser = dataState.browser || {};
  var browserSessionId = String(browser.sessionId || browser.chatId || "").trim();
  return browserSessionId && browserSessionId === id ? browser : {};
}

// Right-panel tab rendering the fork lineage as a node-and-line tree. Clicking
// a node switches to that branch; the active chat's nodes stay highlighted.

export { WbcComposer, wbcBranchConnectors, wbcBranchKindLabel, wbcBranchLineage, wbcBranchRows, wbcBrowserStateForChat, wbcClearComposerDraft, wbcNormalizeContextActivations, wbcParseSlashCommandText }
