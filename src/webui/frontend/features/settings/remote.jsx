import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  SectionTitle,
  SectionBlock,
  FieldRow,
  Toggle,
} from "./shared.jsx"

// ── Remote Control Panel ──
function remoteTransportDetail(t, transport) {
  var status = String((transport && transport.status) || "disabled");
  if (status === "connected" && transport && transport.port_fallback) {
    return t("settings.remoteTransportAlternatePort", {
      port: Number(transport.lan_port) || 37841,
    });
  }
  var key = {
    disabled: "settings.remoteTransportDisabled",
    configured: "settings.remoteTransportConfigured",
    connecting: "settings.remoteTransportConnecting",
    connected: "settings.remoteTransportConnected",
  }[status];
  if (key) return t(key);
  if (status === "error") {
    var detail = String((transport && transport.detail) || "").trim();
    return detail
      ? t("settings.remoteTransportErrorDetail", { detail: detail })
      : t("settings.remoteTransportError");
  }
  return t("settings.remoteTransportUnknown");
}

function remoteEventFallback(value) {
  var text = String(value || "").replace(/_/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "—";
}

function remoteEventLabel(t, eventType) {
  return t(
    "settings.remoteEvent." + eventType,
    null,
    remoteEventFallback(eventType),
  );
}

function remoteOutcomeLabel(t, outcome) {
  var value = String(outcome || "recorded");
  return t(
    "settings.remoteOutcome." + value,
    null,
    remoteEventFallback(value),
  );
}

function remoteEventTime(value) {
  if (!value) return "—";
  return workbenchServices.i18n().formatDate(value, { dateStyle: "medium", timeStyle: "short" }) || "—";
}

function RemotePanel(p) {
  var { t } = p;
  var [remote, setRemote] = useStateSt(null);
  var [loading, setLoading] = useStateSt(true);
  var [busy, setBusy] = useStateSt("");
  var [notice, setNotice] = useStateSt("");
  var [pairingMode, setPairingMode] = useStateSt("share");
  var [inviteProjects, setInviteProjects] = useStateSt([]);
  var [pairingKey, setPairingKey] = useStateSt("");
  var [remoteAddress, setRemoteAddress] = useStateSt("");
  var [incomingPairingKey, setIncomingPairingKey] = useStateSt("");
  var [auditEvents, setAuditEvents] = useStateSt([]);
  var remoteSaveTimerRef = useRefSt(null);
  var remoteSaveQueueRef = useRefSt(Promise.resolve());
  var remoteSaveVersionRef = useRefSt(0);
  var remoteDraftRef = useRefSt(null);
  var inviteDefaultsInitializedRef = useRefSt(false);
  var pairingPeerIdsRef = useRefSt([]);
  var pairingExpiresAtRef = useRefSt(0);

  function showRemoteNotice(message, type) {
    var feedback = workbenchServices.feedback();
    if (feedback && typeof feedback.showToast === "function") {
      feedback.showToast(message, type || "success");
      return;
    }
    setNotice(message);
  }

  function notifyRemoteDevicesChanged(reason) {
    try {
      window.dispatchEvent(new CustomEvent("cyrene:remote-devices-changed", {
        detail: { reason: reason || "settings" },
      }));
    } catch (e) {}
  }

  function loadRemote(options) {
    var background = !!(options && options.background);
    if (!background) setLoading(true);
    return settingsFetch("/api/remote/settings").then(readSettingsResponse).then(function (payload) {
      setRemote(payload);
      remoteDraftRef.current = payload;
      if (!inviteDefaultsInitializedRef.current) {
        var defaultProjects = (payload.projects || []).map(function (project) {
          return project.id;
        });
        inviteDefaultsInitializedRef.current = true;
        setInviteProjects(defaultProjects);
      }
      if (!background) setLoading(false);
      return payload;
    }).catch(function (error) {
      if (!background) {
        setNotice(t("settings.remoteLoadFailed") + ": " + error.message);
        setLoading(false);
      }
    });
  }

  function upsertRemotePeer(peer) {
    var current = remoteDraftRef.current;
    if (!current || !peer || !peer.device_id) return;
    var peers = (current.peers || []).filter(function (item) {
      return item.device_id !== peer.device_id;
    });
    var next = { ...current, peers: peers.concat([peer]) };
    remoteDraftRef.current = next;
    setRemote(next);
  }

  function loadAudit() {
    return settingsFetch("/api/remote/audit?limit=30").then(readSettingsResponse).then(function (payload) {
      setAuditEvents(payload.events || []);
    }).catch(function () {});
  }

  useEffectSt(function () {
    loadRemote();
    loadAudit();
    return function () {
      if (remoteSaveTimerRef.current) {
        clearTimeout(remoteSaveTimerRef.current);
      }
    };
  }, []);

  useEffectSt(function () {
    if (!pairingKey) return undefined;
    var refresh = function () {
      if (pairingExpiresAtRef.current && Date.now() >= pairingExpiresAtRef.current) {
        setPairingKey("");
        return;
      }
      loadRemote({ background: true }).then(function (payload) {
        if (!payload) return;
        var previousIds = pairingPeerIdsRef.current;
        var hasNewPeer = (payload.peers || []).some(function (peer) {
          return previousIds.indexOf(peer.device_id) < 0;
        });
        if (!hasNewPeer) return;
        setPairingKey("");
        showRemoteNotice(t("settings.remotePairingComplete"));
        notifyRemoteDevicesChanged("paired");
        loadAudit();
      });
    };
    var timer = setInterval(refresh, 1000);
    return function () { clearInterval(timer); };
  }, [pairingKey]);

  function persistSettings(nextRemote, version) {
    if (!nextRemote) return;
    var snapshot = {
      enabled: !!nextRemote.enabled,
      relay_url: "",
      device_name: String(nextRemote.device_name || "").trim(),
    };
    setBusy("settings");
    var request = remoteSaveQueueRef.current.catch(function () {}).then(function () {
      return settingsFetch("/api/remote/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(snapshot),
      }).then(readSettingsResponse);
    });
    remoteSaveQueueRef.current = request;
    request.then(function (payload) {
      if (version !== remoteSaveVersionRef.current) return;
      setRemote(payload);
      loadAudit();
    }).catch(function (error) {
      if (version === remoteSaveVersionRef.current) {
        showRemoteNotice(t("settings.error") + ": " + error.message, "error");
      }
    }).finally(function () {
      if (version === remoteSaveVersionRef.current) {
        setBusy("");
      }
    });
  }

  function updateRemoteSettings(nextRemote, immediate) {
    remoteDraftRef.current = nextRemote;
    var version = ++remoteSaveVersionRef.current;
    setRemote(nextRemote);
    if (remoteSaveTimerRef.current) {
      clearTimeout(remoteSaveTimerRef.current);
      remoteSaveTimerRef.current = null;
    }
    if (immediate) {
      persistSettings(nextRemote, version);
      return;
    }
    remoteSaveTimerRef.current = setTimeout(function () {
      remoteSaveTimerRef.current = null;
      persistSettings(remoteDraftRef.current, version);
    }, 600);
  }

  function flushRemoteSettings() {
    if (!remoteSaveTimerRef.current) return;
    clearTimeout(remoteSaveTimerRef.current);
    remoteSaveTimerRef.current = null;
    persistSettings(remoteDraftRef.current, remoteSaveVersionRef.current);
  }

  function toggleList(value, setter) {
    setter(function (current) {
      return current.indexOf(value) >= 0
        ? current.filter(function (item) { return item !== value; })
        : current.concat([value]);
    });
  }

  function createInvitation() {
    pairingPeerIdsRef.current = ((remoteDraftRef.current || remote).peers || []).map(function (peer) {
      return peer.device_id;
    });
    setBusy("invite");
    settingsFetch("/api/remote/pairing/short-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        capabilities: Array.isArray(remote.default_capabilities)
          ? remote.default_capabilities
          : [],
        project_scopes: inviteProjects,
        ttl_seconds: 120,
      }),
    }).then(readSettingsResponse).then(function (payload) {
      pairingExpiresAtRef.current = Date.parse(payload.expires_at || "") || (Date.now() + 120000);
      setPairingKey(payload.pairing_key || "");
      showRemoteNotice(t("settings.remoteInvitationCreated"));
      loadAudit();
    }).catch(function (error) {
      showRemoteNotice(t("settings.error") + ": " + error.message, "error");
    }).finally(function () {
      setBusy("");
    });
  }

  function connectRemoteDevice() {
    if (!remoteAddress.trim() || !incomingPairingKey.trim()) return;
    setBusy("accept");
    settingsFetch("/api/remote/pairing/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address: remoteAddress.trim(),
        pairing_key: incomingPairingKey.trim(),
      }),
    }).then(readSettingsResponse).then(function (payload) {
      setIncomingPairingKey("");
      upsertRemotePeer(payload.peer);
      showRemoteNotice(t("settings.remotePairingComplete"));
      notifyRemoteDevicesChanged("paired");
      loadRemote({ background: true });
      loadAudit();
    }).catch(function (error) {
      showRemoteNotice(error.code === "remote_pairing_peer_update_required"
        ? t("settings.remotePeerUpdateRequired")
        : t("settings.error") + ": " + error.message, "error");
    }).finally(function () {
      setBusy("");
    });
  }

  function copyText(value) {
    if (!value) return;
    var write;
    if (window.cyrene && typeof window.cyrene.writeClipboardText === "function") {
      write = Promise.resolve(window.cyrene.writeClipboardText(value));
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      write = navigator.clipboard.writeText(value);
    } else {
      write = new Promise(function (resolve, reject) {
        var input = document.createElement("textarea");
        input.value = value;
        input.setAttribute("readonly", "");
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        try {
          if (!document.execCommand("copy")) {
            throw new Error(t("settings.remoteCopyFailed"));
          }
          resolve();
        } catch (error) {
          reject(error);
        } finally {
          input.remove();
        }
      });
    }
    write.then(function () {
      showRemoteNotice(t("settings.remoteCopied"));
    }).catch(function (error) {
      showRemoteNotice(t("settings.error") + ": " + error.message, "error");
    });
  }

  if (loading && !remote) {
    return React.createElement("div", { className: "settings-panel" },
      SectionTitle(t("settings.remote"), t("settings.remoteSubtitle")),
      React.createElement("p", { className: "wb-hint" }, t("settings.loading")),
    );
  }

  if (!remote) {
    return React.createElement("div", { className: "settings-panel" },
      SectionTitle(t("settings.remote"), t("settings.remoteSubtitle")),
      React.createElement("p", { className: "wb-hint" }, notice || t("settings.remoteLoadFailed")),
    );
  }

  var identity = remote.identity || {};
  var transport = remote.transport || {};
  var directPairing = remote.direct_pairing || {};
  var localAddresses = directPairing.addresses || [];
  return React.createElement("div", { className: "settings-panel remote-settings-panel" },
    SectionTitle(t("settings.remote"), t("settings.remoteSubtitle")),

    FieldRow(
      remote.enabled ? t("settings.remoteEnabled") : t("settings.remoteDisabled"),
      remoteTransportDetail(t, transport),
      Toggle(!!remote.enabled, function () {
        updateRemoteSettings({ ...remote, enabled: !remote.enabled }, true);
      }, busy === "settings", t("settings.remoteEnable")),
    ),

    SectionBlock(t("settings.remoteThisDevice"), null,
      React.createElement("div", { className: "remote-identity-grid" },
        React.createElement("label", null,
          React.createElement("span", null, t("settings.remoteDeviceName")),
          React.createElement("input", {
            className: "wb-input",
            value: remote.device_name || "",
            maxLength: 120,
            onChange: function (e) {
              updateRemoteSettings(
                { ...remote, device_name: e.target.value },
                false,
              );
            },
            onBlur: flushRemoteSettings,
          }),
        ),
      ),
      React.createElement("div", { className: "remote-identity-facts" },
        React.createElement("div", null, React.createElement("span", null, t("settings.remoteLocalAddress")), React.createElement("code", null, localAddresses[0] || t("settings.remoteAddressUnavailable"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.remoteDeviceId")), React.createElement("code", null, identity.device_id || "—")),
        React.createElement("div", null, React.createElement("span", null, t("settings.remoteFingerprint")), React.createElement("code", null, identity.fingerprint || "—")),
      ),
    ),

    SectionBlock(t("settings.remotePairDevice"), null,
      React.createElement("div", { className: "remote-pairing-layout" },
        React.createElement("div", { className: "remote-pairing-toolbar" },
          React.createElement("p", null, t("settings.remotePairDeviceHint")),
          React.createElement("div", { className: "wb-seg remote-pairing-tabs", role: "tablist", "aria-label": t("settings.remotePairDevice") },
            React.createElement("button", {
              type: "button",
              role: "tab",
              "aria-selected": pairingMode === "share",
              className: "wb-seg-btn" + (pairingMode === "share" ? " active" : ""),
              onClick: function () { setPairingMode("share"); },
            }, t("settings.remotePairModeShare")),
            React.createElement("button", {
              type: "button",
              role: "tab",
              "aria-selected": pairingMode === "control",
              className: "wb-seg-btn" + (pairingMode === "control" ? " active" : ""),
              onClick: function () { setPairingMode("control"); },
            }, t("settings.remotePairModeControl")),
          ),
        ),
        pairingMode === "share"
          ? React.createElement("div", { className: "remote-pairing-pane", role: "tabpanel" },
              React.createElement("div", { className: "remote-pairing-copy" },
                React.createElement("b", null, t("settings.remoteAllowController")),
                React.createElement("small", null, t("settings.remoteAllowControllerHint")),
              ),
              React.createElement("div", { className: "remote-pairing-group" },
                React.createElement("span", { className: "remote-pairing-group-title" }, t("settings.remoteSharedProjects")),
                React.createElement("div", { className: "remote-project-choices" },
                  (remote.projects || []).map(function (project) {
                    return React.createElement("label", { key: project.id, className: "remote-option" },
                      React.createElement("input", { type: "checkbox", checked: inviteProjects.indexOf(project.id) >= 0, onChange: function () { toggleList(project.id, setInviteProjects); } }),
                      React.createElement("span", null, project.name || project.id),
                    );
                  }),
                ),
              ),
              React.createElement("div", { className: "remote-pairing-actions" },
                React.createElement("button", { className: "wb-btn primary", onClick: createInvitation, disabled: !remote.enabled || !inviteProjects.length || busy === "invite" }, busy === "invite" ? t("settings.loading") : t("settings.remoteCreateInvitation")),
              ),
              pairingKey && React.createElement("div", { className: "remote-direct-offer" },
                React.createElement("div", null,
                  React.createElement("small", null, t("settings.remoteLocalAddress")),
                  React.createElement("code", null, localAddresses[0] || t("settings.remoteAddressUnavailable")),
                ),
                React.createElement("div", null,
                  React.createElement("small", null, t("settings.remotePairingKey")),
                  React.createElement("button", {
                    type: "button",
                    className: "remote-pairing-key",
                    "data-cyrene-secret": "true",
                    onClick: function () { copyText(pairingKey); },
                    title: t("settings.remoteCopyPairingKey"),
                    "aria-label": t("settings.remoteCopyPairingKey"),
                  }, pairingKey),
                ),
                React.createElement("p", null, t("settings.remoteShortKeyExpires")),
              ),
            )
          : React.createElement("div", { className: "remote-pairing-pane", role: "tabpanel" },
              React.createElement("div", { className: "remote-pairing-copy" },
                React.createElement("b", null, t("settings.remoteControlAnother")),
                React.createElement("small", null, t("settings.remoteControlAnotherHint")),
              ),
              React.createElement("div", { className: "remote-pairing-control" },
                React.createElement("label", { className: "remote-response-field" },
                  React.createElement("span", null, t("settings.remoteDeviceAddress")),
                  React.createElement("input", { className: "wb-input mono", value: remoteAddress, spellCheck: false, autoCapitalize: "off", autoCorrect: "off", placeholder: "192.168.1.20:37841", onChange: function (e) { setRemoteAddress(e.target.value); } }),
                ),
                React.createElement("label", { className: "remote-response-field" },
                  React.createElement("span", null, t("settings.remotePairingKey")),
                  React.createElement("input", { className: "wb-input mono remote-key-input", "data-cyrene-user-ceremony": "true", value: incomingPairingKey, maxLength: 11, spellCheck: false, autoCapitalize: "characters", autoCorrect: "off", placeholder: "ABCDE-23456", onChange: function (e) { setIncomingPairingKey(e.target.value.toUpperCase()); } }),
                ),
                React.createElement("div", { className: "remote-pairing-actions" },
                  React.createElement("button", { className: "wb-btn primary", onClick: connectRemoteDevice, disabled: !remoteAddress.trim() || !incomingPairingKey.trim() || busy === "accept" }, busy === "accept" ? t("settings.remoteConnectingDevice") : t("settings.remoteConnectDevice")),
                ),
              ),
            ),
      ),
    ),

    SectionBlock(t("settings.remoteTrustedDevices"), null,
      !(remote.peers || []).length && React.createElement("p", { className: "wb-hint" }, t("settings.remoteNoDevices")),
      (remote.peers || []).map(function (peer) {
        return React.createElement(RemotePeerCard, {
          key: peer.device_id,
          t: t,
          peer: peer,
          projects: remote.projects || [],
          onChanged: function () { loadRemote(); loadAudit(); },
          onNotice: showRemoteNotice,
        });
      }),
    ),

    SectionBlock(t("settings.remoteAudit"), null,
      React.createElement("div", { className: "remote-audit-list" },
        !auditEvents.length && React.createElement("p", { className: "wb-hint" }, t("settings.remoteNoAudit")),
        auditEvents.map(function (event) {
          return React.createElement("div", { key: event.event_id, className: "remote-audit-row" },
            React.createElement("span", { className: "remote-audit-outcome " + (event.outcome === "error" ? "error" : "") }, remoteOutcomeLabel(t, event.outcome)),
            React.createElement("div", null,
              React.createElement("b", null, remoteEventLabel(t, event.event_type)),
              React.createElement("small", null, [event.command, event.peer_device_id, remoteEventTime(event.created_at)].filter(Boolean).join(" · ")),
            ),
          );
        }),
      ),
    ),
  );
}

function RemotePeerCard(p) {
  var { t, peer, projects, onChanged, onNotice } = p;
  var [editing, setEditing] = useStateSt(false);
  var [busy, setBusy] = useStateSt(false);
  var [grantedProjects, setGrantedProjects] = useStateSt(peer.granted_project_scopes || []);

  function toggle(value, setter) {
    setter(function (current) {
      return current.indexOf(value) >= 0
        ? current.filter(function (item) { return item !== value; })
        : current.concat([value]);
    });
  }

  function saveGrant() {
    setBusy(true);
    settingsFetch("/api/remote/peers/" + encodeURIComponent(peer.device_id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        capabilities: Array.isArray(peer.granted_capabilities)
          ? peer.granted_capabilities
          : [],
        project_scopes: grantedProjects,
      }),
    }).then(readSettingsResponse).then(function () {
      setEditing(false);
      onNotice(t("settings.remoteGrantSaved"), "success");
      try { window.dispatchEvent(new CustomEvent("cyrene:remote-devices-changed", { detail: { reason: "grant_updated" } })); } catch (e) {}
      onChanged();
    }).catch(function (error) {
      onNotice(t("settings.error") + ": " + error.message, "error");
    }).finally(function () { setBusy(false); });
  }

  function revoke() {
    setBusy(true);
    settingsFetch("/api/remote/peers/" + encodeURIComponent(peer.device_id), { method: "DELETE" })
      .then(readSettingsResponse).then(function () {
        onNotice(t("settings.remoteDeviceRevoked"), "success");
        try { window.dispatchEvent(new CustomEvent("cyrene:remote-devices-changed", { detail: { reason: "revoked" } })); } catch (e) {}
        onChanged();
      }).catch(function (error) {
        onNotice(t("settings.error") + ": " + error.message, "error");
      }).finally(function () { setBusy(false); });
  }

  return React.createElement("div", { className: "remote-peer-card" },
    React.createElement("div", { className: "remote-peer-header" },
      React.createElement("div", null,
        React.createElement("b", null, peer.display_name || peer.device_id),
        React.createElement("code", null, peer.device_id),
      ),
      React.createElement("div", { className: "remote-peer-actions" },
        React.createElement("button", { className: "wb-btn muted", onClick: function () {
          if (!editing) {
            setGrantedProjects(peer.granted_project_scopes || []);
          }
          setEditing(!editing);
        }, disabled: busy }, editing ? t("settings.close") : t("settings.remoteEditGrant")),
        React.createElement("button", { className: "wb-btn danger", onClick: revoke, disabled: busy }, t("settings.remoteRevoke")),
      ),
    ),
    React.createElement("div", { className: "remote-peer-summary" },
      React.createElement("span", null, t("settings.remoteDeviceAddress") + ": " + (peer.lan_address || "—")),
      React.createElement("span", null, t("settings.remoteGrantedToPeer") + ": " + (peer.granted_capabilities || []).length),
      React.createElement("span", null, t("settings.remoteReceivedFromPeer") + ": " + (peer.received_capabilities || []).length),
      React.createElement("span", null, peer.fingerprint || ""),
    ),
    editing && React.createElement("div", { className: "remote-grant-editor" },
      React.createElement("div", { className: "remote-project-list" },
        projects.map(function (project) {
          return React.createElement("label", { key: project.id, className: "remote-option" },
            React.createElement("input", { type: "checkbox", checked: grantedProjects.indexOf(project.id) >= 0, onChange: function () { toggle(project.id, setGrantedProjects); } }),
            React.createElement("span", null, project.name || project.id),
          );
        }),
      ),
      React.createElement("button", { className: "wb-btn primary", onClick: saveGrant, disabled: busy }, t("settings.remoteSaveGrant")),
    ),
  );
}

export { RemotePanel, RemotePeerCard };
