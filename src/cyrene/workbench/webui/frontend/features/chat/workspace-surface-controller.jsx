import { workbenchServices } from "../../shared/runtime/services.jsx"
import { PluginFrontendService } from "../../platform/plugins.jsx"
import { useWbcState, useWbcRef, useWbcEffect, wbcT, wbcNormalizePaneLayout } from "../../workbench-chat.jsx"
import { WBC_PROJECT_FILE_DRAFTS, wbcProjectFileDraftKey } from "./split-pane.jsx"
import { WBC_SURFACE_INTENT_EVENT } from "./dynamic-surfaces.jsx"
import { wbcClaimSurfaceCard, wbcNormalizeSurfaceIntent, wbcRevealSurface, wbcSurfaceResourceKey } from "./dynamic-surface-broker.mjs"

function wbcWorkspaceSurfaceDescriptor(value, catalog, chatId) {
  var normalized = wbcNormalizeSurfaceIntent(Object.assign({}, value || {}, {
    chatId: String(chatId || ""),
  }), catalog);
  if (!normalized) return null;
  var surface = (Array.isArray(catalog) ? catalog : []).find(function (item) {
    return String(item && item.id || "") === normalized.surfaceId
      && String(item && item.pack_id || "") === normalized.packId;
  });
  var renderer = surface && surface.renderer && typeof surface.renderer === "object"
    ? surface.renderer : {};
  return renderer.kind === "native" && renderer.id === "workspace-composite"
    ? normalized : null;
}

function wbcDurableWorkspaceSurfaceDescriptor(descriptor, chatId) {
  if (!descriptor) return null;
  return {
    schemaVersion: 1,
    surfaceId: descriptor.surfaceId,
    packId: descriptor.packId,
    resource: descriptor.resource,
    resourceKey: descriptor.resourceKey,
    activity: descriptor.activity,
    attention: "reveal",
    priority: descriptor.priority,
    lifetime: descriptor.lifetime,
    preferredSide: descriptor.preferredSide,
    chatId: String(chatId || ""),
  };
}

function useWbcWorkspaceSurfaceState(chats, model, setChats, setActiveChat) {
  var [descriptors, setDescriptors] = useWbcState({});
  var [catalog, setCatalog] = useWbcState(function () {
    var snapshot = PluginFrontendService.snapshot();
    return Array.isArray(snapshot.workbenchSurfaces) ? snapshot.workbenchSurfaces : [];
  });
  var persistedRef = useWbcRef(new Map());
  useWbcEffect(function () {
    return PluginFrontendService.subscribe(function (snapshot) {
      setCatalog(Array.isArray(snapshot.workbenchSurfaces) ? snapshot.workbenchSurfaces : []);
    });
  }, []);
  useWbcEffect(function () {
    chats.forEach(function (chat) {
      if (!chat || !chat.id || !chat.workspaceSurface) return;
      persistedRef.current.set(String(chat.id), JSON.stringify(chat.workspaceSurface));
    });
  }, [chats]);
  function persist(chatId, descriptor) {
    var durable = wbcDurableWorkspaceSurfaceDescriptor(descriptor, chatId);
    if (!chatId || !durable) return;
    var signature = JSON.stringify(durable);
    if (persistedRef.current.get(chatId) === signature) return;
    persistedRef.current.set(chatId, signature);
    model.updateChatPreferences(chatId, { workspaceSurface: durable })
      .then(function (updated) {
        var persisted = updated && updated.workspaceSurface ? updated.workspaceSurface : durable;
        persistedRef.current.set(chatId, JSON.stringify(persisted));
        setChats(function (current) {
          return current.map(function (chat) {
            return String(chat && chat.id || "") === chatId
              ? Object.assign({}, chat, { workspaceSurface: persisted }) : chat;
          });
        });
        setActiveChat(function (current) {
          return current && String(current.id || "") === chatId
            ? Object.assign({}, current, { workspaceSurface: persisted }) : current;
        });
      })
      .catch(function () {
        if (persistedRef.current.get(chatId) === signature) persistedRef.current.delete(chatId);
        workbenchServices.feedback().showToast(
          wbcT("workbenchChat.workspaceStateSaveFailed", "The workspace state could not be saved."),
          "error"
        );
      });
  }
  return { catalog: catalog, descriptors: descriptors, persist: persist, setDescriptors: setDescriptors };
}

function useWbcSurfaceIntentListener(options) {
  useWbcEffect(function () {
    function onSurfaceIntent(event) {
      var intent = event && event.detail && typeof event.detail === "object" ? event.detail : {};
      var ownerChatId = String(intent.chatId || intent.chat_id || options.activeChatIdRef.current || "");
      var ownerId = ownerChatId || (options.projectId ? "project:" + String(options.projectId) : "");
      if (!ownerId) return;
      var declaredSurfaces = PluginFrontendService.snapshot().workbenchSurfaces;
      var catalog = Array.isArray(declaredSurfaces) ? declaredSurfaces : [];
      var workspaceDescriptor = wbcWorkspaceSurfaceDescriptor(
        wbcNormalizeSurfaceIntent(intent, catalog), catalog, ownerChatId
      );
      var result = null;
      options.setPaneLayoutsByChat(function (current) {
        var previous = wbcNormalizePaneLayout(current[ownerId], ownerChatId);
        result = wbcRevealSurface(previous, Object.assign({}, intent, { chatId: ownerChatId }), {
          catalog: catalog,
          isSuppressed: function (runId, resourceKey) {
            return options.surfaceSuppressionRef.current.has(String(runId || "") + "\n" + String(resourceKey || ""));
          },
          canReplace: function (card) {
            var resource = card && card.payload && card.payload.resource || {};
            if (resource.kind !== "file") return true;
            var draftKey = wbcProjectFileDraftKey({
              source: "project",
              projectId: resource.projectId || resource.project_id || options.projectId,
              path: resource.path,
            });
            return !(draftKey && WBC_PROJECT_FILE_DRAFTS[draftKey]);
          },
          canOpen: function (normalizedIntent) {
            var resourceKind = normalizedIntent && normalizedIntent.resource && normalizedIntent.resource.kind;
            if (["file", "directory"].indexOf(resourceKind) < 0) return true;
            var runId = String(normalizedIntent.runId || "");
            return !runId || !options.surfaceRevealedRunRef.current.has(runId);
          },
        });
        if (result && ["opened", "replaced"].indexOf(result.outcome) >= 0) {
          var resourceKind = intent && intent.resource && intent.resource.kind;
          var openedRunId = String(intent.runId || intent.run_id || "");
          if (openedRunId && ["file", "directory"].indexOf(resourceKind) >= 0) {
            options.surfaceRevealedRunRef.current.add(openedRunId);
            if (options.surfaceRevealedRunRef.current.size > 500) {
              options.surfaceRevealedRunRef.current.delete(options.surfaceRevealedRunRef.current.values().next().value);
            }
          }
        }
        return !result || result.layout === previous
          ? current : Object.assign({}, current, { [ownerId]: result.layout });
      });
      window.setTimeout(function () {
        if (workspaceDescriptor && result && ["opened", "replaced", "updated"].indexOf(result.outcome) >= 0) {
          options.setWorkspaceSurfaceDescriptors(function (current) {
            return Object.assign({}, current, { [ownerId]: workspaceDescriptor });
          });
          options.persistWorkspaceSurface(ownerChatId, workspaceDescriptor);
        }
        window.dispatchEvent(new CustomEvent("cyrene:surface-result", {
          detail: Object.assign({}, result || { outcome: "unavailable" }, {
            surfaceId: String(intent.surfaceId || intent.surface_id || intent.surface || ""),
            resourceKey: String(intent.resourceKey || intent.resource_key || ""),
          }),
        }));
      }, 0);
    }
    window.addEventListener(WBC_SURFACE_INTENT_EVENT, onSurfaceIntent);
    return function () { window.removeEventListener(WBC_SURFACE_INTENT_EVENT, onSurfaceIntent); };
  }, [options.projectId]);
}

function wbcOpenStartedWorkspace(options) {
  if (!options.descriptor) return false;
  var result = null;
  options.setPaneLayoutsByChat(function (current) {
    var previous = wbcNormalizePaneLayout(current[options.ownerId], options.ownerChatId);
    result = wbcRevealSurface(previous, Object.assign({}, options.descriptor, {
      attention: "reveal", chatId: options.ownerChatId,
    }), {
      catalog: PluginFrontendService.snapshot().workbenchSurfaces,
      canReplace: function (card) {
        var resource = card && card.payload && card.payload.resource || {};
        if (resource.kind !== "file") return true;
        var draftKey = wbcProjectFileDraftKey({
          source: "project",
          projectId: resource.projectId || resource.project_id || options.projectId,
          path: resource.path,
        });
        return !(draftKey && WBC_PROJECT_FILE_DRAFTS[draftKey]);
      },
    });
    if (!result || result.layout === previous) return current;
    var claimedLayout = Object.assign({}, result.layout);
    ["left", "right"].forEach(function (side) {
      claimedLayout[side] = result.layout[side].map(function (card) {
        return String(card && card.id || "") === String(result.cardId || "")
          ? wbcClaimSurfaceCard(card) : card;
      });
    });
    return Object.assign({}, current, { [options.ownerId]: claimedLayout });
  });
  window.setTimeout(function () {
    if (result && result.outcome === "deferred") {
      workbenchServices.feedback().showToast(
        wbcT("workbenchChat.workspaceOpenDeferred", "No split slot is available. Close or unpin a split and try again."),
        "warning"
      );
    }
  }, 0);
  options.setSideTab("");
  return true;
}

function useWbcResourceObservations() {
  var observationLifecycleRef = useWbcRef({ ids: {}, finishTimers: {} });
  var [resourceObservationStates, setResourceObservationStates] = useWbcState({});
  useWbcEffect(function () {
    var events = window.CyreneUI && window.CyreneUI.events;
    if (!events || typeof events.subscribe !== "function") return undefined;
    function eventKey(event) {
      var paneCardId = String(event && event.pane_card_id || "");
      if (paneCardId) return "pane:" + paneCardId;
      var kind = String(event && event.resource_kind || "");
      var id = String(event && event.resource_id || "");
      return kind && id ? "resource:" + kind + ":" + id : "";
    }
    var unsubscribe = events.subscribe(function (event) {
      var type = String(event && event.type || "");
      if (type !== "resource_observation.started" && type !== "resource_observation.ended") return;
      var lifecycle = observationLifecycleRef.current;
      var observationId = String(event && event.observation_id || "");
      var key = type === "resource_observation.ended" && observationId
        ? String(lifecycle.ids[observationId] || eventKey(event)) : eventKey(event);
      if (!key) return;
      if (type === "resource_observation.started") {
        if (observationId && lifecycle.ids[observationId]) return;
        if (observationId) lifecycle.ids[observationId] = key;
        if (lifecycle.finishTimers[key]) {
          window.clearTimeout(lifecycle.finishTimers[key]);
          delete lifecycle.finishTimers[key];
        }
        setResourceObservationStates(function (current) {
          var previous = current[key] || { count: 0, finishing: false };
          return Object.assign({}, current, { [key]: { count: previous.count + 1, finishing: false } });
        });
        return;
      }
      if (observationId) delete lifecycle.ids[observationId];
      setResourceObservationStates(function (current) {
        var previous = current[key] || { count: 1, finishing: false };
        var count = Math.max(0, previous.count - 1);
        return Object.assign({}, current, { [key]: { count: count, finishing: count === 0 } });
      });
      lifecycle.finishTimers[key] = window.setTimeout(function () {
        delete lifecycle.finishTimers[key];
        setResourceObservationStates(function (current) {
          var value = current[key];
          if (!value || value.count > 0) return current;
          var next = Object.assign({}, current);
          delete next[key];
          return next;
        });
      }, 520);
    });
    return function () {
      unsubscribe();
      Object.values(observationLifecycleRef.current.finishTimers).forEach(function (timer) {
        window.clearTimeout(timer);
      });
      observationLifecycleRef.current = { ids: {}, finishTimers: {} };
    };
  }, []);
  return resourceObservationStates;
}

export { wbcWorkspaceSurfaceDescriptor, useWbcWorkspaceSurfaceState, useWbcSurfaceIntentListener, useWbcResourceObservations, wbcOpenStartedWorkspace };
