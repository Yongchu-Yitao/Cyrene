import { wbcNormalizeContextActivations } from "./composer-settings.jsx"

export function wbcAvailableContextIds(items) {
  return new Set((items || []).filter(function (item) {
    return item && item.available === true;
  }).map(function (item) { return String(item.id || ""); }).filter(Boolean));
}

export function wbcParseSlashCommandText(text, commands) {
  var source = String(text || "").trim();
  if (source.indexOf("/") !== 0) return null;
  var match = source.match(/^\/([^\s]+)(?:\s+([\s\S]*))?$/);
  if (!match) return null;
  var id = match[1];
  var command = (commands || []).find(function (item) { return item.id === id; });
  return command ? { command: command, message: String(match[2] || "").trim() } : null;
}

export function prepareComposerSubmission(text, options, activateContext) {
    var { command, slashPool, attachments } = options;
    var parsedSlash = !command ? wbcParseSlashCommandText(text, slashPool) : null;
    var submittedCommand = command || (parsedSlash && parsedSlash.command.id) || "";
    var submittedDescriptor = command
      ? slashPool.find(function (item) { return item.id === command; })
      : parsedSlash && parsedSlash.command;
    if (parsedSlash) text = parsedSlash.message;
    if (!text && attachments.length === 0 && !submittedCommand) return null;
    var submittedContextActivations = prepareSubmissionContext(options, submittedDescriptor, activateContext);
    return submissionPayload(options, text, submittedCommand, submittedContextActivations);
}

function prepareSubmissionContext(options, submittedDescriptor, activateContext) {
    var { contextActivationsRef, mcpAvailable, skillsAvailable, pluginPacksAvailable, contextCatalog } = options;
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
        activateContext(submittedContextActivations);
      }
    }
    return submittedContextActivations;
}

function submissionPayload(options, text, submittedCommand, submittedContextActivations) {
    var { attachments, mode, agentManagedModels, selectedModelId, reasoningEffort, soulAvailable, personaOn, workspaceAvailable, workspaceOverride, workspaceOn, memoryAvailable, shortTermMemoryOn, projectMemoryOn, composerContextAvailable, remoteAvailable, remoteDeviceIdsRef } = options;
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
    if (memoryAvailable) {
      payload.shortTermMemoryActive = shortTermMemoryOn;
      payload.projectMemoryActive = projectMemoryOn;
    }
    if (composerContextAvailable) {
      payload.remoteDeviceIds = remoteAvailable ? remoteDeviceIdsRef.current.slice() : [];
      payload.contextActivations = submittedContextActivations;
    }
    return payload;
}
