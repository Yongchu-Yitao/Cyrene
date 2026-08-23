import {
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionTitle,
  FieldRow,
  Toggle,
} from "./shared.jsx"

// ── Channels Panel ──
function ChannelsPanel(p) {
  var { t, telegramToken, setTelegramToken, telegramSaved, setTelegramSaved, notifyTelegram, setNotifyTelegram, notifyWechat, setNotifyWechat } = p;

  function saveTelegram() {
    if (!telegramToken || telegramToken.startsWith("••")) { showSettingsToast(t("settings.noChanges"), "info"); return; }
    setTelegramSaved(t("settings.saving"));
    settingsFetch("/api/settings/keys", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ TELEGRAM_BOT_TOKEN: telegramToken }) })
      .then(function () { setTelegramSaved(""); showSettingsToast(t("settings.saved"), "success"); })
      .catch(function (error) { setTelegramSaved(""); showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error"); });
  }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.channels"), t("settings.channelsSubtitle")),

    React.createElement("div", { className: "wb-channel-card" },
      React.createElement("div", { className: "wb-channel-head" },
        React.createElement("span", { className: "wb-channel-icon" }, "⌖"),
        React.createElement("b", null, t("settings.telegram")),
      ),
      React.createElement("p", { className: "wb-channel-desc" }, t("settings.telegramTokenHint")),
      FieldRow(t("settings.telegramToken"), null,
        [
          React.createElement("div", { className: "wb-inline-row" },
            React.createElement("input", { className: "wb-input mono", type: "password", value: telegramToken, onChange: function (e) { setTelegramToken(e.target.value); }, placeholder: t("settings.placeholderOptional") }),
            React.createElement("button", { className: "wb-btn primary", onClick: saveTelegram }, t("settings.saveNotification")),
          ),
          telegramSaved && React.createElement("span", { className: "wb-hint saved" }, telegramSaved),
        ],
        undefined, "setting-telegram",
      ),
      FieldRow(t("settings.notifyTelegram"), t("settings.notifyTelegramHint"),
        Toggle(notifyTelegram, function () {
          var next = !notifyTelegram;
          setNotifyTelegram(next);
          settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notify_telegram: next }) }).catch(function () { setNotifyTelegram(!next); });
        }),
      ),
    ),

    React.createElement(WeChatConnectionPanel, { t, notifyWechat, setNotifyWechat, anchorId: "setting-wechat" }),
  );
}

function WeChatConnectionPanel(p) {
  var { t, notifyWechat, setNotifyWechat } = p;
  var [connected, setConnected] = useStateSt(false);
  var [running, setRunning] = useStateSt(false);
  var [ownerWxid, setOwnerWxid] = useStateSt("");
  var [qrCode, setQrCode] = useStateSt("");
  var [qrStatus, setQrStatus] = useStateSt("");
  var [busy, setBusy] = useStateSt(false);
  var cancelledRef = useRefSt(false);
  var pollAbortRef = useRefSt(null);

  function refreshStatus() {
    return settingsFetch("/api/wechat/status")
      .then(readSettingsResponse)
      .then(function (status) {
        setConnected(!!status.connected);
        setRunning(!!status.running);
        setOwnerWxid(status.owner_wxid || "");
        return status;
      });
  }

  useEffectSt(function () {
    cancelledRef.current = false;
    refreshStatus().catch(function () {
      if (!cancelledRef.current) setQrStatus(t("settings.wechatStatusFailed"));
    });
    return function () {
      cancelledRef.current = true;
      if (pollAbortRef.current) pollAbortRef.current.abort();
    };
  }, []);

  function closeQrModal() {
    cancelledRef.current = true;
    if (pollAbortRef.current) pollAbortRef.current.abort();
    pollAbortRef.current = null;
    setQrCode("");
    setQrStatus("");
    setBusy(false);
  }

  function qrImageUrl(content) {
    if (String(content || "").startsWith("data:image/")) return content;
    return "https://api.qrserver.com/v1/create-qr-code/?size=280x280&margin=8&data=" + encodeURIComponent(content);
  }

  function pollLogin(qrcodeId) {
    var controller = new AbortController();
    pollAbortRef.current = controller;
    setQrStatus(t("settings.wechatWaitingConfirm"));
    settingsFetch("/api/wechat/poll-login", {
      method: "POST",
      body: JSON.stringify({ qrcode_id: qrcodeId }),
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    }).then(readSettingsResponse).then(function (result) {
      if (cancelledRef.current) return;
      if (!result.ok) {
        setBusy(false);
        setQrStatus(t("settings.wechatQrExpired"));
        return;
      }
      setQrStatus(t("settings.wechatLoginSuccess"));
      return settingsFetch("/api/wechat/start", { method: "POST" })
        .then(readSettingsResponse)
        .then(refreshStatus)
        .then(function () {
          if (cancelledRef.current) return;
          setBusy(false);
          setQrCode("");
          setQrStatus("");
        });
    }).catch(function (error) {
      if (cancelledRef.current || error.name === "AbortError") return;
      setBusy(false);
      setQrStatus(t("settings.wechatConnectionFailed") + ": " + error.message);
    }).finally(function () {
      if (pollAbortRef.current === controller) pollAbortRef.current = null;
    });
  }

  function startLogin() {
    cancelledRef.current = false;
    if (pollAbortRef.current) pollAbortRef.current.abort();
    setBusy(true);
    setQrCode("");
    setQrStatus(t("settings.wechatFetchingQr"));
    settingsFetch("/api/wechat/qr-login", { method: "POST" })
      .then(readSettingsResponse)
      .then(function (result) {
        if (!result.qrcode_id || (!result.qrcode_image && !result.qrcode_img)) {
          throw new Error(t("settings.wechatInvalidQr"));
        }
        if (cancelledRef.current) return;
        setQrCode(qrImageUrl(result.qrcode_image || result.qrcode_img));
        setQrStatus(t("settings.wechatScanPrompt"));
        pollLogin(result.qrcode_id);
      })
      .catch(function (error) {
        if (cancelledRef.current || error.name === "AbortError") return;
        setBusy(false);
        setQrStatus(t("settings.wechatConnectionFailed") + ": " + error.message);
      });
  }

  function startWechat() {
    setBusy(true);
    setQrStatus("");
    settingsFetch("/api/wechat/start", { method: "POST" })
      .then(readSettingsResponse)
      .then(refreshStatus)
      .catch(function (error) {
        setQrStatus(t("settings.wechatStartFailed") + ": " + error.message);
      })
      .finally(function () { setBusy(false); });
  }

  function stopWechat() {
    setBusy(true);
    setQrStatus("");
    settingsFetch("/api/wechat/stop", { method: "POST" })
      .then(readSettingsResponse)
      .then(refreshStatus)
      .catch(function (error) {
        setQrStatus(t("settings.wechatStopFailed") + ": " + error.message);
      })
      .finally(function () { setBusy(false); });
  }

  var statusText = connected
    ? (running ? t("settings.wechatConnectedRunning") : t("settings.wechatConnectedStopped"))
    : t("settings.wechatNotConnected");

  return React.createElement("div", { className: "wb-channel-card wb-wechat-card", id: p.anchorId || undefined },
    React.createElement("div", { className: "wb-channel-head wb-channel-head-spread" },
      React.createElement("div", { className: "wb-channel-title" },
        React.createElement("span", { className: "wb-channel-icon" }, "⌖"),
        React.createElement("b", null, t("settings.wechat")),
      ),
      connected && React.createElement("span", {
        className: "wb-channel-state " + (running ? "running" : "stopped"),
      }, running ? t("settings.wechatRunning") : t("settings.wechatStopped")),
    ),
    React.createElement("p", { className: "wb-channel-desc" }, t("settings.wechatDescription")),
    React.createElement("div", { className: "wb-wechat-status-row" },
      React.createElement("div", { className: "wb-wechat-status-copy" },
        React.createElement("small", null, t("settings.wechatCurrentStatus")),
        React.createElement("span", null,
          React.createElement("i", { className: "wb-channel-dot " + (running ? "running" : (connected ? "stopped" : "off")) }),
          React.createElement("strong", null, statusText),
        ),
        ownerWxid && React.createElement("code", null, ownerWxid),
      ),
      React.createElement("div", { className: "wb-wechat-actions" },
        connected && running && React.createElement("button", {
          className: "wb-btn danger", onClick: stopWechat, disabled: busy,
        }, t("settings.wechatStop")),
        connected && !running && React.createElement("button", {
          className: "wb-btn primary", onClick: startWechat, disabled: busy,
        }, t("settings.wechatStart")),
        !connected && React.createElement("button", {
          className: "wb-btn primary", onClick: startLogin, disabled: busy,
        }, busy ? t("settings.wechatFetchingQr") : t("settings.wechatScanConnect")),
      ),
    ),
    qrStatus && !qrCode && React.createElement("div", { className: "wb-wechat-message", role: "status" }, qrStatus),
    FieldRow(t("settings.notifyWechat"), t("settings.notifyWechatHint"), Toggle(notifyWechat, function () {
      var next = !notifyWechat;
      setNotifyWechat(next);
      settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notify_wechat: next }) }).catch(function () { setNotifyWechat(!next); });
    })),
    qrCode && React.createElement("div", {
      className: "wb-wechat-qr-overlay",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": t("settings.wechatScanningTitle"),
      onClick: closeQrModal,
    },
      React.createElement("div", { className: "wb-wechat-qr-dialog", onClick: function (event) { event.stopPropagation(); } },
        React.createElement("button", {
          className: "wb-wechat-qr-close",
          onClick: closeQrModal,
          title: t("common.close"),
          "aria-label": t("common.close"),
        }, "×"),
        React.createElement("h3", null, t("settings.wechatScanningTitle")),
        React.createElement("img", { src: qrCode, alt: t("settings.wechatQrAlt") }),
        React.createElement("p", { role: "status" }, qrStatus),
        qrStatus === t("settings.wechatQrExpired") && React.createElement("button", {
          className: "wb-btn primary",
          onClick: startLogin,
        }, t("settings.wechatQrRetry")),
      ),
    ),
  );
}

export { ChannelsPanel };
