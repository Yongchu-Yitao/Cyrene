(function () {
  'use strict';

  const pending = new Map();
  let requestSequence = 0;
  let context = null;
  let deviceId = '';
  let sessionId = '';
  let mode = 'current_desktop';
  let qualityMode = 'auto';
  let peer = null;
  let inputChannel = null;
  let controlChannel = null;
  let microphoneStream = null;
  let microphoneSender = null;
  let observationTimer = null;
  let clipboardTimer = null;
  let securityTimer = null;
  let connectionLossTimer = null;
  let clipboardRevision = 0;
  let lastLocalClipboardText = '';
  let remoteMediaStream = null;
  let activePointerId = null;
  let pointerDownPoint = null;
  let targetPlatform = '';
  let language = 'en';
  let authorizedModes = null;
  let autoConnectStarted = false;
  let reconnectInProgress = false;
  let secureSurface = false;
  let sessionPermissions = {};
  const providerCapabilitiesByMode = {};
  const clipboardImageOffers = new Set();
  const clipboardFileOffers = new Set();

  const messages = {
    en: {
      remoteDesktop: 'Remote Desktop', ready: 'Ready', sendFiles: 'Send files', switchDisplay: 'Switch display',
      shareMicrophone: 'Share microphone', microphoneShared: 'Microphone is being shared', disconnect: 'Disconnect',
      desktopDisplay: 'Remote desktop display', connectTitle: 'Connect to this desktop',
      connectDetail: 'The picture, audio and controls stay inside the encrypted remote session.',
      connectionMode: 'Connection mode', currentDesktop: 'Current desktop', currentDesktopDetail: 'Share the signed-in session',
      systemLogin: 'System login', systemLoginDetail: 'Windows / Linux RDP session', connect: 'Connect', retry: 'Try again',
      connecting: 'Connecting…', openingLogin: 'Opening system login…', negotiating: 'Negotiating a direct encrypted connection',
      connected: 'Connected', encryptedWebrtc: 'Encrypted WebRTC', directOnly: 'Direct ICE only · TURN is not configured', connectionLost: 'Connection lost',
      connectionLostDetail: 'The encrypted media connection ended. You can reconnect without restoring credentials.',
      couldNotConnect: 'Could not connect', signInCancelled: 'Sign-in was cancelled.', connectionFailed: 'Connection failed.',
      notConnected: 'Not connected', qualityAuto: 'Auto quality', qualitySmooth: 'Smooth quality',
      qualityBalanced: 'Balanced quality', qualityClear: 'Clear quality', clipboardReady: 'Clipboard ready',
      clipboardSynced: 'Clipboard synced', remoteClipboardUpdated: 'Remote clipboard updated', receivingImage: 'Receiving image…',
      remoteImageUpdated: 'Remote image clipboard updated', receivingFiles: 'Receiving files…',
      remoteFilesReady: '{count} remote file(s) ready', fileLimit: 'File clipboard limit: 512 files / 64 MB',
      preparingFiles: 'Preparing {count} file(s)…', filesSent: '{count} file(s) sent', sendingImage: 'Sending image…',
      imageSent: 'Image clipboard sent', clipboardSent: 'Clipboard sent', hostTimeout: 'The Remote Desktop host did not respond.',
      capabilityTimeout: 'The desktop host capability did not respond.', pluginCallFailed: 'Plugin call failed.',
      imageReadFailed: 'Clipboard image could not be read.', fileReadFailed: 'Clipboard file could not be read.', displays: 'Displays', fileClipboard: 'File clipboard',
      fileChannelDetail: 'Use existing encrypted file channel', sendFolder: 'Send folder', folderDetail: 'Keep its directory structure',
      reconnecting: 'Reconnecting…', protectedSurface: 'The remote device is locked or showing a protected system surface.',
      microphoneUnavailable: 'Remote microphone injection needs a configured virtual-audio input on this device.',
      inputUnavailable: 'This desktop can be viewed, but its native input bridge is unavailable.',
    },
    zh: {
      remoteDesktop: '远程桌面', ready: '就绪', sendFiles: '发送文件', switchDisplay: '切换显示器',
      shareMicrophone: '共享麦克风', microphoneShared: '麦克风正在共享', disconnect: '断开连接',
      desktopDisplay: '远程桌面画面', connectTitle: '连接到此桌面',
      connectDetail: '画面、音频和控制操作仅在加密的远程会话内传输。',
      connectionMode: '连接模式', currentDesktop: '当前桌面', currentDesktopDetail: '共享已登录的桌面会话',
      systemLogin: '系统登录', systemLoginDetail: 'Windows / Linux RDP 会话', connect: '连接', retry: '重试',
      connecting: '正在连接…', openingLogin: '正在打开系统登录…', negotiating: '正在协商端到端加密连接',
      connected: '已连接', encryptedWebrtc: '加密 WebRTC', directOnly: '仅直连 ICE · 尚未配置 TURN', connectionLost: '连接已中断',
      connectionLostDetail: '加密媒体连接已经结束。重新连接时不会恢复此前的登录凭据。',
      couldNotConnect: '无法连接', signInCancelled: '已取消系统登录。', connectionFailed: '连接失败。',
      notConnected: '未连接', qualityAuto: '自动画质', qualitySmooth: '流畅画质',
      qualityBalanced: '均衡画质', qualityClear: '清晰画质', clipboardReady: '剪贴板已就绪',
      clipboardSynced: '剪贴板已同步', remoteClipboardUpdated: '远端文本剪贴板已更新', receivingImage: '正在接收图片…',
      remoteImageUpdated: '远端图片剪贴板已更新', receivingFiles: '正在接收文件…',
      remoteFilesReady: '已接收 {count} 个远端文件', fileLimit: '文件剪贴板上限：512 个文件 / 64 MB',
      preparingFiles: '正在准备 {count} 个文件…', filesSent: '已发送 {count} 个文件', sendingImage: '正在发送图片…',
      imageSent: '图片剪贴板已发送', clipboardSent: '剪贴板已发送', hostTimeout: '远程桌面宿主未响应。',
      capabilityTimeout: '桌面宿主能力未响应。', pluginCallFailed: '插件调用失败。',
      imageReadFailed: '无法读取剪贴板图片。', fileReadFailed: '无法读取剪贴板文件。', displays: '显示器', fileClipboard: '文件剪贴板',
      fileChannelDetail: '使用现有的加密文件通道', sendFolder: '发送文件夹', folderDetail: '保留目录结构',
      reconnecting: '正在重新连接…', protectedSurface: '远端设备已锁定或正在显示受保护的系统界面。',
      microphoneUnavailable: '当前桌面麦克风回传需要在被控端配置虚拟音频输入。',
      inputUnavailable: '当前可以查看画面，但被控端缺少可用的原生输入桥。',
    },
  };

  function t(key, values) {
    const catalog = messages[language] || messages.en;
    let value = String(catalog[key] || messages.en[key] || key);
    Object.keys(values || {}).forEach(function (name) {
      value = value.split(`{${name}}`).join(String(values[name]));
    });
    return value;
  }

  function applyLanguage(locale) {
    language = String(locale || 'en').toLowerCase().startsWith('zh') ? 'zh' : 'en';
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-i18n]').forEach(function (node) {
      node.textContent = t(String(node.dataset.i18n || ''));
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (node) {
      const value = t(String(node.dataset.i18nTitle || ''));
      node.title = value;
      node.setAttribute('aria-label', value);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (node) {
      node.setAttribute('aria-label', t(String(node.dataset.i18nAria || '')));
    });
  }

  function applyTheme(value) {
    const theme = String(value || '').toLowerCase() === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  }

  const app = document.getElementById('app');
  const stage = document.getElementById('stage');
  const imeInput = document.getElementById('ime-input');
  const video = document.getElementById('remote-video');
  const audio = document.getElementById('remote-audio');
  const empty = document.getElementById('empty-state');
  const busy = document.getElementById('busy-state');
  const retryButton = document.getElementById('retry-button');
  const disconnectButton = document.getElementById('disconnect-button');
  const microphoneButton = document.getElementById('microphone-button');
  const displayButton = document.getElementById('display-button');
  const displayMenu = document.getElementById('display-menu');
  const displayList = document.getElementById('display-list');
  const fileButton = document.getElementById('file-button');
  const fileMenu = document.getElementById('file-menu');
  const fileInput = document.getElementById('file-input');
  const folderInput = document.getElementById('folder-input');
  let viewportUpdateFrame = 0;

  function callTimeoutMs(method) {
    if (method === 'remoteDesktop.session.connect' || method === 'remoteDesktop.session.reconnect') return 90_000;
    if (method.indexOf('remoteDesktop.clipboard.files.') === 0) return 180_000;
    if (method.indexOf('remoteDesktop.clipboard.image.') === 0) return 90_000;
    return 25_000;
  }

  function call(method, args) {
    const requestId = `desktop-${Date.now()}-${++requestSequence}`;
    const timeoutMs = callTimeoutMs(method);
    return new Promise(function (resolve, reject) {
      pending.set(requestId, { resolve, reject });
      window.parent.postMessage({
        source: 'cyrene-plugin',
        type: 'call',
        requestId,
        method,
        args: args || {},
        timeoutMs,
      }, '*');
      window.setTimeout(function () {
        const request = pending.get(requestId);
        if (!request) return;
        pending.delete(requestId);
        request.reject(new Error(t('hostTimeout')));
      }, timeoutMs);
    });
  }

  function hostCall(method, args) {
    const requestId = `desktop-host-${Date.now()}-${++requestSequence}`;
    return new Promise(function (resolve, reject) {
      pending.set(requestId, { resolve, reject });
      window.parent.postMessage({
        source: 'cyrene-plugin',
        type: 'host-call',
        requestId,
        method,
        args: args || {},
      }, '*');
      window.setTimeout(function () {
        const request = pending.get(requestId);
        if (!request) return;
        pending.delete(requestId);
        request.reject(new Error(t('capabilityTimeout')));
      }, 5000);
    });
  }

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value || '');
  }

  function publishState(values) {
    window.parent.postMessage({
      source: 'cyrene-plugin',
      type: 'state',
      state: values && typeof values === 'object' ? values : {},
    }, '*');
  }

  function state(name, title, detail) {
    app.dataset.state = name;
    empty.hidden = name !== 'idle' && name !== 'failed';
    busy.hidden = name !== 'connecting';
    video.hidden = name !== 'connected';
    disconnectButton.hidden = name !== 'connected';
    retryButton.hidden = name !== 'failed';
    const connected = name === 'connected';
    fileButton.disabled = !connected || sessionPermissions.clipboard_file !== true;
    displayButton.disabled = !connected || sessionPermissions.display_select !== true;
    const modeCapabilities = providerCapabilitiesByMode[mode] || [];
    const microphoneAvailable = modeCapabilities.indexOf('microphone') >= 0
      && sessionPermissions.microphone === true;
    microphoneButton.disabled = !connected || !microphoneAvailable;
    if (connected && !microphoneAvailable) {
      microphoneButton.title = t('microphoneUnavailable');
      microphoneButton.setAttribute('aria-label', t('microphoneUnavailable'));
    } else if (connected) {
      const microphoneTitle = microphoneButton.getAttribute('aria-pressed') === 'true'
        ? t('microphoneShared') : t('shareMicrophone');
      microphoneButton.title = microphoneTitle;
      microphoneButton.setAttribute('aria-label', microphoneTitle);
    }
    const permissionBanner = document.getElementById('permission-banner');
    if (connected && (
      modeCapabilities.indexOf('input') < 0
      || sessionPermissions.input !== true
    )) {
      permissionBanner.dataset.kind = 'input';
      permissionBanner.textContent = t('inputUnavailable');
      permissionBanner.hidden = false;
    } else if (permissionBanner.dataset.kind === 'input') {
      permissionBanner.hidden = true;
      delete permissionBanner.dataset.kind;
    }
    if (title) setText(name === 'connecting' ? 'busy-title' : 'empty-title', title);
    if (detail) setText(name === 'connecting' ? 'busy-detail' : 'empty-detail', detail);
    setText('status-copy', title || name);
  }

  function selectMode(nextMode) {
    mode = String(nextMode || 'current_desktop');
  }

  function applyProviderProbe(probe) {
    const providers = Array.isArray(probe && probe.providers) ? probe.providers : [];
    if (probe && probe.ok === false) {
      throw new Error(String(probe.error || probe.code || t('connectionFailed')));
    }
    const available = [];
    Object.keys(providerCapabilitiesByMode).forEach(function (key) { delete providerCapabilitiesByMode[key]; });
    providers.forEach(function (provider) {
      if (!provider || (provider.status !== 'supported' && provider.status !== 'degraded')) return;
      (Array.isArray(provider.modes) ? provider.modes : []).forEach(function (candidate) {
        const normalized = String(candidate || '');
        if (normalized && available.indexOf(normalized) < 0) available.push(normalized);
        const capabilities = Array.isArray(provider.capabilities) ? provider.capabilities.map(String) : [];
        providerCapabilitiesByMode[normalized] = Array.from(new Set((providerCapabilitiesByMode[normalized] || []).concat(capabilities)));
      });
    });
    const usable = available.filter(function (candidate) {
      return authorizedModes === null || authorizedModes.indexOf(candidate) >= 0;
    });
    if (!providers.length) return [];
    if (usable.length) return usable;
    const diagnostics = providers.reduce(function (items, provider) {
      return items.concat(Array.isArray(provider && provider.diagnostics) ? provider.diagnostics : []);
    }, []);
    const message = String(diagnostics[0] && diagnostics[0].message || probe && (probe.error || probe.code) || t('connectionFailed'));
    throw new Error(message);
  }

  function unsupportedModeError(probe, requestedMode) {
    const providers = Array.isArray(probe && probe.providers) ? probe.providers : [];
    const diagnostics = providers.filter(function (provider) {
      if (!provider) return false;
      if (requestedMode === 'remote_login') return String(provider.id || '').indexOf('freerdp') >= 0;
      return String(provider.id || '') === 'electron-current-desktop';
    }).reduce(function (items, provider) {
      return items.concat(Array.isArray(provider.diagnostics) ? provider.diagnostics : []);
    }, []);
    const preferred = diagnostics.find(function (item) { return String(item && item.severity || '') === 'error'; })
      || diagnostics[0];
    return new Error(String(preferred && preferred.message || t('connectionFailed')));
  }

  function channelSend(channel, payload) {
    if (!channel || channel.readyState !== 'open') return false;
    channel.send(JSON.stringify(payload));
    return true;
  }

  function publishViewportSize() {
    if (!controlChannel || controlChannel.readyState !== 'open') return;
    const bounds = stage.getBoundingClientRect();
    channelSend(controlChannel, {
      type: 'viewport',
      width: Math.max(1, Math.round(bounds.width)),
      height: Math.max(1, Math.round(bounds.height)),
      device_pixel_ratio: Math.max(0.5, Math.min(2, Number(window.devicePixelRatio || 1))),
    });
  }

  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(function () {
      if (viewportUpdateFrame) window.cancelAnimationFrame(viewportUpdateFrame);
      viewportUpdateFrame = window.requestAnimationFrame(function () {
        viewportUpdateFrame = 0;
        publishViewportSize();
      });
    }).observe(stage);
  }

  function waitForIce(pc, timeoutMs) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise(function (resolve) {
      let done = false;
      function finish() {
        if (done) return;
        done = true;
        pc.removeEventListener('icegatheringstatechange', changed);
        resolve();
      }
      function changed() { if (pc.iceGatheringState === 'complete') finish(); }
      pc.addEventListener('icegatheringstatechange', changed);
      window.setTimeout(finish, timeoutMs || 8000);
    });
  }

  function waitForConnectedVideo(activePeer, timeoutMs) {
    return new Promise(function (resolve, reject) {
      const deadline = Date.now() + Math.max(1000, Number(timeoutMs || 15000));
      const timer = window.setInterval(function () {
        if (peer !== activePeer) {
          window.clearInterval(timer);
          reject(new Error(t('connectionLostDetail')));
          return;
        }
        const connectionState = String(activePeer.connectionState || '');
        if (connectionState === 'failed' || connectionState === 'closed') {
          window.clearInterval(timer);
          reject(new Error(t('connectionLostDetail')));
          return;
        }
        const videoTrack = remoteMediaStream && remoteMediaStream.getVideoTracks()[0];
        if (
          connectionState === 'connected'
          && videoTrack
          && videoTrack.readyState === 'live'
          && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
        ) {
          window.clearInterval(timer);
          resolve();
          return;
        }
        if (Date.now() >= deadline) {
          window.clearInterval(timer);
          reject(new Error(t('connectionFailed')));
        }
      }, 50);
    });
  }

  function closePeer() {
    if (observationTimer) window.clearInterval(observationTimer);
    if (clipboardTimer) window.clearInterval(clipboardTimer);
    if (securityTimer) window.clearInterval(securityTimer);
    if (connectionLossTimer) window.clearTimeout(connectionLossTimer);
    observationTimer = null;
    clipboardTimer = null;
    securityTimer = null;
    connectionLossTimer = null;
    if (microphoneStream) microphoneStream.getTracks().forEach(function (track) { track.stop(); });
    microphoneStream = null;
    microphoneSender = null;
    if (peer) peer.close();
    peer = null;
    inputChannel = null;
    controlChannel = null;
    video.srcObject = null;
    audio.srcObject = null;
    remoteMediaStream = null;
    secureSurface = false;
    activePointerId = null;
    pointerDownPoint = null;
    microphoneButton.setAttribute('aria-pressed', 'false');
  }

  function applySessionPermissions(value) {
    sessionPermissions = value && typeof value === 'object' ? { ...value } : {};
  }

  function startClipboardSync() {
    if (clipboardTimer || !sessionId || sessionPermissions.clipboard_text !== true) return;
    hostCall('clipboard.readText').then(function (text) {
      lastLocalClipboardText = String(text || '');
    }).catch(function () {});
    clipboardTimer = window.setInterval(function () {
      if (!sessionId || !controlChannel || controlChannel.readyState !== 'open') return;
      hostCall('clipboard.readText').then(function (value) {
        const text = String(value || '').slice(0, 1024 * 1024);
        if (text === lastLocalClipboardText) return;
        lastLocalClipboardText = text;
        clipboardRevision += 1;
        channelSend(controlChannel, { type: 'clipboard:text', text, revision: clipboardRevision });
        setText('clipboard-copy', t('clipboardSynced'));
      }).catch(function () {});
    }, 650);
  }

  async function preparePeer(iceServers) {
    const activePeer = new RTCPeerConnection({ iceServers: Array.isArray(iceServers) ? iceServers : [] });
    peer = activePeer;
    inputChannel = activePeer.createDataChannel('cyrene-input', { ordered: false, maxRetransmits: 0 });
    controlChannel = activePeer.createDataChannel('cyrene-control', { ordered: true });
    controlChannel.onopen = publishViewportSize;
    // An answer cannot introduce a new media section. Declare the receive-only
    // video transceiver in the offer so the controlled host can attach its
    // captured desktop track to the negotiated video m-line.
    activePeer.addTransceiver('video', { direction: 'recvonly' });
    const microphoneTransceiver = activePeer.addTransceiver('audio', { direction: 'sendrecv' });
    microphoneSender = microphoneTransceiver.sender;
    controlChannel.onmessage = function (event) {
      try {
        const message = JSON.parse(String(event.data || '{}'));
        if (message.type === 'clipboard:text') {
          if (sessionPermissions.clipboard_text !== true) return;
          clipboardRevision = Math.max(clipboardRevision, Number(message.revision || 0));
          lastLocalClipboardText = String(message.text || '').slice(0, 1024 * 1024);
          setText('clipboard-copy', t('remoteClipboardUpdated'));
          hostCall('clipboard.writeText', { text: lastLocalClipboardText }).catch(function () {});
        } else if (message.type === 'clipboard:image-offer') {
          if (sessionPermissions.clipboard_image !== true) return;
          const offerId = String(message.offer_id || '');
          if (!offerId || clipboardImageOffers.has(offerId)) return;
          clipboardImageOffers.add(offerId);
          setText('clipboard-copy', t('receivingImage'));
          call('remoteDesktop.clipboard.image.receive', {
            session_id: sessionId,
            offer_id: offerId,
            size: Number(message.size || 0),
            sha256: String(message.sha256 || ''),
          }).then(function () {
            setText('clipboard-copy', t('remoteImageUpdated'));
          }).catch(function (error) {
            clipboardImageOffers.delete(offerId);
            setText('clipboard-copy', String(error && error.message || error));
          });
        } else if (message.type === 'clipboard:file-offer') {
          if (sessionPermissions.clipboard_file !== true) return;
          const offerId = String(message.offer_id || '');
          if (!offerId || clipboardFileOffers.has(offerId)) return;
          clipboardFileOffers.add(offerId);
          setText('clipboard-copy', t('receivingFiles'));
          call('remoteDesktop.clipboard.files.receive', {
            session_id: sessionId,
            offer_id: offerId,
          }).then(function (result) {
            const count = Number(result && result.count || 0);
            setText('clipboard-copy', t('remoteFilesReady', { count }));
          }).catch(function (error) {
            clipboardFileOffers.delete(offerId);
            setText('clipboard-copy', String(error && error.message || error));
          });
        } else if (message.type === 'security:surface') {
          secureSurface = message.secure_surface === true;
          applySecurityState({ secure_surface: secureSurface });
          refreshSecurityState();
        }
      } catch (_) {}
    };
    remoteMediaStream = new MediaStream();
    activePeer.ontrack = function (event) {
      if (peer !== activePeer) return;
      if (!remoteMediaStream.getTracks().some(function (track) { return track.id === event.track.id; })) {
        remoteMediaStream.addTrack(event.track);
      }
      if (event.track.kind === 'video') {
        video.srcObject = new MediaStream(remoteMediaStream.getVideoTracks());
        video.play().catch(function () {});
      } else if (event.track.kind === 'audio') {
        if (sessionPermissions.system_audio !== true) return;
        audio.srcObject = new MediaStream(remoteMediaStream.getAudioTracks());
        audio.play().catch(function () {});
      }
    };
    activePeer.onconnectionstatechange = function () {
      if (peer !== activePeer) return;
      const next = activePeer.connectionState;
      if (next === 'connected') {
        if (connectionLossTimer) window.clearTimeout(connectionLossTimer);
        connectionLossTimer = null;
      } else if (next === 'failed' || next === 'disconnected') {
        if (connectionLossTimer) window.clearTimeout(connectionLossTimer);
        connectionLossTimer = window.setTimeout(function () {
          if (peer !== activePeer || (activePeer.connectionState !== 'failed' && activePeer.connectionState !== 'disconnected')) return;
          const lostSession = sessionId;
          if (lostSession && mode === 'current_desktop' && !reconnectInProgress) {
            reconnectCurrentDesktop(lostSession);
            return;
          }
          if (reconnectInProgress) return;
          sessionId = '';
          if (lostSession) call('remoteDesktop.session.disconnect', { session_id: lostSession }).catch(function () {});
          closePeer();
          state('failed', t('connectionLost'), t('connectionLostDetail'));
        }, next === 'failed' ? 0 : 4000);
      }
    };
    const offer = await activePeer.createOffer();
    await activePeer.setLocalDescription(offer);
    await waitForIce(activePeer, 8000);
    return { type: activePeer.localDescription.type, sdp: activePeer.localDescription.sdp };
  }

  function delay(milliseconds) {
    return new Promise(function (resolve) { window.setTimeout(resolve, milliseconds); });
  }

  async function reconnectCurrentDesktop(lostSession) {
    if (reconnectInProgress || !lostSession) return;
    reconnectInProgress = true;
    closePeer();
    state('connecting', t('reconnecting'), t('negotiating'));
    let lastError = null;
    try {
      const delays = [0, 800, 2200];
      for (let attempt = 0; attempt < delays.length; attempt += 1) {
        let candidateSessionId = '';
        if (delays[attempt]) await delay(delays[attempt]);
        try {
          const prepared = await call('remoteDesktop.session.prepare', { device_id: deviceId });
          if (prepared && prepared.network && prepared.network.turn_configured === false) {
            setText('transport-copy', t('directOnly'));
          }
          const probe = prepared && prepared.remote_probe || {};
          const usableModes = applyProviderProbe(probe);
          if (usableModes.indexOf('current_desktop') < 0) throw unsupportedModeError(probe, 'current_desktop');
          const offer = await preparePeer(prepared && prepared.ice_servers || []);
          const result = await call('remoteDesktop.session.reconnect', {
            session_id: lostSession,
            offer,
          });
          candidateSessionId = String(result && result.session && result.session.session_id || '');
          if (!result || result.ok === false || !result.answer) {
            throw new Error(String(result && (result.error || result.code) || t('connectionFailed')));
          }
          sessionId = candidateSessionId;
          applySessionPermissions(result.permissions);
          await peer.setRemoteDescription(result.answer);
          await waitForConnectedVideo(peer, 15000);
          publishState(Object.assign({}, result.session || {}, {
            resource_kind: 'remote_desktop',
            resource_id: sessionId,
          }));
          state('connected', t('connected'));
          startObservationPoll();
          startClipboardSync();
          startSecurityPoll();
          return;
        } catch (error) {
          lastError = error;
          if (candidateSessionId) {
            await call('remoteDesktop.session.disconnect', { session_id: candidateSessionId }).catch(function () {});
            if (sessionId === candidateSessionId) sessionId = '';
          }
          closePeer();
        }
      }
      sessionId = '';
      await call('remoteDesktop.session.disconnect', { session_id: lostSession }).catch(function () {});
      state('failed', t('connectionLost'), String(lastError && lastError.message || t('connectionLostDetail')));
    } finally {
      reconnectInProgress = false;
    }
  }

  async function connect() {
    if (!context || !deviceId) return;
    closePeer();
    applySessionPermissions({});
    state('connecting', mode === 'remote_login' ? t('openingLogin') : t('connecting'), t('negotiating'));
    let candidateSessionId = '';
    try {
      const requestedMode = mode;
      const prepared = await call('remoteDesktop.session.prepare', { device_id: deviceId });
      if (prepared && prepared.network && prepared.network.turn_configured === false) {
        setText('transport-copy', t('directOnly'));
      }
      const probe = prepared && prepared.remote_probe || {};
      const usableModes = applyProviderProbe(probe);
      if (usableModes.indexOf(requestedMode) < 0) {
        const fallbackMode = usableModes.indexOf('current_desktop') >= 0
          ? 'current_desktop'
          : String(usableModes[0] || '');
        if (!fallbackMode) throw unsupportedModeError(probe, requestedMode);
        selectMode(fallbackMode);
      }
      qualityMode = String(prepared && prepared.preference && prepared.preference.quality_mode || 'auto');
      setText('quality-copy', t(`quality${qualityMode[0].toUpperCase()}${qualityMode.slice(1)}`));
      const offer = await preparePeer(prepared && prepared.ice_servers || []);
      let result = await call('remoteDesktop.session.connect', {
        device_id: deviceId,
        mode,
        offer,
        quality_mode: qualityMode,
      });
      if (result && result.code === 'desktop_credentials_required' && result.session && result.session.session_id) {
        candidateSessionId = String(result.session.session_id);
        sessionId = candidateSessionId;
        const credential = await call('remoteDesktop.credentials.request', { session_id: sessionId });
        if (!credential || credential.ok === false) throw new Error(t('signInCancelled'));
        result = await call('remoteDesktop.session.connect', {
          device_id: deviceId,
          mode,
          offer,
          quality_mode: qualityMode,
          credential_handle: credential.credential_handle,
        });
      }
      candidateSessionId = String(result && result.session && result.session.session_id || candidateSessionId);
      if (!result || result.ok === false || !result.answer) throw new Error(String(result && (result.error || result.code) || t('connectionFailed')));
      sessionId = candidateSessionId;
      applySessionPermissions(result.permissions);
      publishState(Object.assign({}, result.session || {}, {
        resource_kind: 'remote_desktop',
        resource_id: sessionId,
        preferred_mode: mode,
        clipboard_file_available: sessionPermissions.clipboard_file === true,
        display_select_available: sessionPermissions.display_select === true,
        microphone_available: (providerCapabilitiesByMode[mode] || []).indexOf('microphone') >= 0
          && sessionPermissions.microphone === true,
        network_status: prepared && prepared.network && prepared.network.turn_configured
          ? 'relay_ready' : 'direct_only',
        clipboard_status: sessionPermissions.clipboard_text === true
          || sessionPermissions.clipboard_file === true
          || sessionPermissions.clipboard_image === true ? 'ready' : 'unavailable',
      }));
      await peer.setRemoteDescription(result.answer);
      await waitForConnectedVideo(peer, 15000);
      setText('device-name', String(result.session && result.session.device_name || context.state && context.state.title || t('remoteDesktop')));
      setText('transport-copy', String(result.session && result.session.transport_kind || 'WebRTC').toUpperCase());
      state('connected', t('connected'));
      startObservationPoll();
      startClipboardSync();
      startSecurityPoll();
    } catch (error) {
      closePeer();
      if (candidateSessionId) {
        await call('remoteDesktop.session.disconnect', { session_id: candidateSessionId }).catch(function () {});
        if (sessionId === candidateSessionId) sessionId = '';
      }
      state('failed', t('couldNotConnect'), String(error && error.message || error));
    }
  }

  async function disconnect() {
    const current = sessionId;
    sessionId = '';
    closePeer();
    if (current) await call('remoteDesktop.session.disconnect', { session_id: current }).catch(function () {});
    setText('transport-copy', t('notConnected'));
    publishState({ session_id: '', state: 'ready', microphone_enabled: false });
    state('failed', t('notConnected'), t('connectDetail'));
  }

  function videoPoint(event) {
    if (!video.videoWidth || !video.videoHeight) return null;
    const rect = video.getBoundingClientRect();
    const scale = Math.min(rect.width / video.videoWidth, rect.height / video.videoHeight);
    const width = video.videoWidth * scale;
    const height = video.videoHeight * scale;
    const left = rect.left + (rect.width - width) / 2;
    const top = rect.top + (rect.height - height) / 2;
    if (event.clientX < left || event.clientX > left + width || event.clientY < top || event.clientY > top + height) return null;
    return {
      x_normalized: (event.clientX - left) / width,
      y_normalized: (event.clientY - top) / height,
    };
  }

  function pointerEvent(event, action) {
    if (!sessionId || app.dataset.state !== 'connected') return;
    if ((providerCapabilitiesByMode[mode] || []).indexOf('input') < 0 || sessionPermissions.input !== true) return;
    const point = videoPoint(event) || ((action === 'button_up' && pointerDownPoint) ? pointerDownPoint : null);
    if (!point) return;
    if (activePointerId === event.pointerId && action === 'move') pointerDownPoint = point;
    const payload = {
      type: 'pointer',
      action,
      ...point,
      delta_x: Number(event.deltaX || 0),
      delta_y: Number(event.deltaY || 0),
    };
    if (action === 'move' && activePointerId !== event.pointerId) channelSend(inputChannel, payload);
    else channelSend(controlChannel, { type: 'input', event: payload });
  }

  let lastMoveAt = 0;
  let imeComposing = false;
  stage.addEventListener('pointermove', function (event) {
    if (Date.now() - lastMoveAt < 35) return;
    lastMoveAt = Date.now();
    pointerEvent(event, 'move');
  });
  stage.addEventListener('pointerdown', function (event) {
    if (event.button !== 0 || !videoPoint(event)) return;
    try { imeInput.focus({ preventScroll: true }); } catch (_) { stage.focus(); }
    activePointerId = event.pointerId;
    pointerDownPoint = videoPoint(event);
    try { stage.setPointerCapture(event.pointerId); } catch (_) {}
    pointerEvent(event, 'button_down');
  });
  stage.addEventListener('pointerup', function (event) {
    if (activePointerId !== event.pointerId) return;
    pointerEvent(event, 'button_up');
    try { stage.releasePointerCapture(event.pointerId); } catch (_) {}
    activePointerId = null;
    pointerDownPoint = null;
  });
  stage.addEventListener('pointercancel', function (event) {
    if (activePointerId !== event.pointerId) return;
    pointerEvent(event, 'button_up');
    activePointerId = null;
    pointerDownPoint = null;
  });
  stage.addEventListener('contextmenu', function (event) { event.preventDefault(); pointerEvent(event, 'right_click'); });
  stage.addEventListener('wheel', function (event) { event.preventDefault(); pointerEvent(event, 'scroll'); }, { passive: false });
  stage.addEventListener('keydown', function (event) {
    if (!sessionId || app.dataset.state !== 'connected') return;
    if ((providerCapabilitiesByMode[mode] || []).indexOf('input') < 0 || sessionPermissions.input !== true) return;
    if (event.isComposing || imeComposing) return;
    if (event.ctrlKey && event.altKey && event.shiftKey && event.key === 'Escape') {
      event.preventDefault();
      disconnect();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key || '').toLowerCase() === 'v') {
      // Keep the browser's native paste dispatch alive. The paste handler
      // prevents the local default after it has synchronized the richest
      // clipboard representation, then sends the remote shortcut itself.
      return;
    }
    if (!event.metaKey && !event.ctrlKey && !event.altKey && event.key.length === 1) {
      if (event.target === imeInput) return;
      event.preventDefault();
      channelSend(controlChannel, { type: 'input', event: { type: 'text', text: event.key } });
      return;
    }
    event.preventDefault();
    const modifiers = [];
    if (event.ctrlKey) modifiers.push('ctrl');
    if (event.altKey) modifiers.push('alt');
    if (event.shiftKey) modifiers.push('shift');
    if (event.metaKey) modifiers.push('meta');
    channelSend(controlChannel, {
      type: 'input',
      event: { type: 'key', key: event.key, code: event.code, modifiers },
    });
  });

  function flushImeInput() {
    if (imeComposing || !sessionId || app.dataset.state !== 'connected') return;
    const text = String(imeInput.value || '');
    imeInput.value = '';
    if (!text || (providerCapabilitiesByMode[mode] || []).indexOf('input') < 0 || sessionPermissions.input !== true) return;
    channelSend(controlChannel, { type: 'input', event: { type: 'text', text } });
  }

  imeInput.addEventListener('compositionstart', function () { imeComposing = true; });
  imeInput.addEventListener('compositionend', function () {
    imeComposing = false;
    window.setTimeout(flushImeInput, 0);
  });
  imeInput.addEventListener('input', flushImeInput);
  async function imagePngBase64(file) {
    if (!file || Number(file.size || 0) > 32 * 1024 * 1024) throw new Error(t('imageReadFailed'));
    const bitmap = await createImageBitmap(file);
    try {
      if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height > 33_554_432) {
        throw new Error(t('imageReadFailed'));
      }
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext('2d', { alpha: true }).drawImage(bitmap, 0, 0);
      return String(canvas.toDataURL('image/png')).split(',', 2)[1] || '';
    } finally {
      if (typeof bitmap.close === 'function') bitmap.close();
    }
  }

  function remotePasteShortcut() {
    if (sessionPermissions.input !== true) return;
    const modifiers = targetPlatform.toLowerCase().indexOf('darwin') >= 0
      || targetPlatform.toLowerCase().indexOf('mac') >= 0 ? ['meta'] : ['ctrl'];
    channelSend(controlChannel, {
      type: 'input',
      event: { type: 'key', key: 'v', code: 'KeyV', modifiers },
    });
  }

  function bufferBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 32768) {
      binary += String.fromCharCode.apply(null, bytes.subarray(offset, Math.min(bytes.length, offset + 32768)));
    }
    return btoa(binary);
  }

  async function sha256Hex(buffer) {
    if (!window.crypto || !window.crypto.subtle) throw new Error(t('fileReadFailed'));
    const digest = new Uint8Array(await window.crypto.subtle.digest('SHA-256', buffer));
    return Array.from(digest).map(function (value) {
      return value.toString(16).padStart(2, '0');
    }).join('');
  }

  async function sendClipboardFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!sessionId || !files.length || sessionPermissions.clipboard_file !== true) return;
    const total = files.reduce(function (sum, file) { return sum + Number(file.size || 0); }, 0);
    if (files.length > 512 || total > 64 * 1024 * 1024) {
      setText('clipboard-copy', t('fileLimit'));
      return;
    }
    setText('clipboard-copy', t('preparingFiles', { count: files.length }));
    let uploadId = '';
    try {
      const manifest = files.map(function (file) {
        return {
          relative_path: String(file.webkitRelativePath || file.name || 'file'),
          size: Number(file.size || 0),
        };
      });
      const begun = await call('remoteDesktop.clipboard.files.upload.begin', {
        session_id: sessionId,
        entries: manifest,
      });
      uploadId = String(begun && begun.upload_id || '');
      if (!uploadId) throw new Error(t('fileReadFailed'));
      const chunkBytes = 256 * 1024;
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        const relativePath = manifest[index].relative_path;
        let offset = 0;
        while (offset < Number(file.size || 0)) {
          const buffer = await file.slice(offset, Math.min(file.size, offset + chunkBytes)).arrayBuffer();
          const response = await call('remoteDesktop.clipboard.files.upload.chunk', {
            upload_id: uploadId,
            relative_path: relativePath,
            offset,
            content_base64: bufferBase64(buffer),
            chunk_sha256: await sha256Hex(buffer),
          });
          const nextOffset = Number(response && response.next_offset || 0);
          if (nextOffset !== offset + buffer.byteLength) throw new Error(t('fileReadFailed'));
          offset = nextOffset;
        }
      }
      await call('remoteDesktop.clipboard.files.upload.commit', { upload_id: uploadId });
      uploadId = '';
      remotePasteShortcut();
      setText('clipboard-copy', t('filesSent', { count: files.length }));
    } catch (error) {
      if (uploadId) {
        await call('remoteDesktop.clipboard.files.upload.abort', { upload_id: uploadId }).catch(function () {});
      }
      setText('clipboard-copy', String(error && error.message || error));
    }
  }

  stage.addEventListener('paste', function (event) {
    const imageItem = Array.from(event.clipboardData && event.clipboardData.items || []).find(function (item) {
      return item && String(item.type || '').toLowerCase().indexOf('image/') === 0;
    });
    if (imageItem && sessionPermissions.clipboard_image === true) {
      const file = imageItem.getAsFile();
      if (!file) return;
      event.preventDefault();
      setText('clipboard-copy', t('sendingImage'));
      imagePngBase64(file).then(function (encoded) {
        return call('remoteDesktop.clipboard.image.send', {
          session_id: sessionId,
          png_base64: encoded,
        });
      }).then(function () {
        remotePasteShortcut();
        setText('clipboard-copy', t('imageSent'));
      }).catch(function (error) {
        setText('clipboard-copy', String(error && error.message || error));
      });
      return;
    }
    const pastedFiles = Array.from(event.clipboardData && event.clipboardData.files || []).filter(function (file) {
      return String(file && file.type || '').toLowerCase().indexOf('image/') !== 0;
    });
    if (pastedFiles.length && sessionPermissions.clipboard_file === true) {
      event.preventDefault();
      sendClipboardFiles(pastedFiles);
      return;
    }
    const text = String(event.clipboardData && event.clipboardData.getData('text/plain') || '').slice(0, 1024 * 1024);
    if (!text || sessionPermissions.clipboard_text !== true) return;
    event.preventDefault();
    clipboardRevision += 1;
    lastLocalClipboardText = text;
    channelSend(controlChannel, { type: 'clipboard:text', text, revision: clipboardRevision });
    remotePasteShortcut();
    setText('clipboard-copy', t('clipboardSent'));
  });

  async function setMicrophone() {
    const enabled = microphoneButton.getAttribute('aria-pressed') !== 'true';
    if (!sessionId || !microphoneSender) return;
    try {
      if (enabled) {
        microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        await microphoneSender.replaceTrack(microphoneStream.getAudioTracks()[0] || null);
      } else {
        await microphoneSender.replaceTrack(null);
        if (microphoneStream) microphoneStream.getTracks().forEach(function (track) { track.stop(); });
        microphoneStream = null;
      }
      await call('remoteDesktop.microphone.set', { session_id: sessionId, enabled });
      publishState({ session_id: sessionId, microphone_enabled: enabled });
      microphoneButton.setAttribute('aria-pressed', enabled ? 'true' : 'false');
      const microphoneTitle = enabled ? t('microphoneShared') : t('shareMicrophone');
      microphoneButton.title = microphoneTitle;
      microphoneButton.setAttribute('aria-label', microphoneTitle);
    } catch (error) {
      if (enabled) {
        await microphoneSender.replaceTrack(null).catch(function () {});
        if (microphoneStream) microphoneStream.getTracks().forEach(function (track) { track.stop(); });
        microphoneStream = null;
      } else {
        microphoneButton.setAttribute('aria-pressed', 'false');
        publishState({ session_id: sessionId, microphone_enabled: false });
      }
      const banner = document.getElementById('permission-banner');
      banner.dataset.kind = 'permission';
      banner.textContent = String(error && error.message || error);
      banner.hidden = false;
    }
  }

  async function showDisplays() {
    if (!sessionId) return;
    if (!displayMenu.hidden) { displayMenu.hidden = true; return; }
    try {
      const result = await call('remoteDesktop.display.list', { session_id: sessionId });
      displayList.textContent = '';
      (result.displays || []).forEach(function (display) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = display.id === (result.selected_display_id || '') ? 'active' : '';
        const name = document.createElement('span');
        name.textContent = String(display.name || display.id);
        const size = document.createElement('small');
        size.textContent = `${display.width || 0} × ${display.height || 0}`;
        button.append(name, size);
        button.addEventListener('click', async function () {
          await call('remoteDesktop.display.select', { session_id: sessionId, display_id: display.id });
          displayMenu.hidden = true;
        });
        displayList.appendChild(button);
      });
      displayMenu.hidden = false;
    } catch (_) {}
  }

  async function submitObservation(observation) {
    if (secureSurface) return;
    if (!video.videoWidth || !video.videoHeight) return;
    let sx = 0, sy = 0, sw = video.videoWidth, sh = video.videoHeight;
    const region = observation.region;
    if (region) {
      sx = Math.max(0, Math.min(sw - 1, Number(region.x || 0)));
      sy = Math.max(0, Math.min(sh - 1, Number(region.y || 0)));
      sw = Math.max(1, Math.min(sw - sx, Number(region.width || sw)));
      sh = Math.max(1, Math.min(sh - sy, Number(region.height || sh)));
    }
    const maxDimension = 2560;
    const scale = Math.min(1, maxDimension / Math.max(sw, sh));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(sw * scale));
    canvas.height = Math.max(1, Math.round(sh * scale));
    canvas.getContext('2d', { alpha: false }).drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    const encoded = canvas.toDataURL('image/png').split(',', 2)[1] || '';
    await call('remoteDesktop.observation.submit', {
      observation_id: observation.observation_id,
      png_base64: encoded,
    });
  }

  const submittedObservations = new Set();
  function startObservationPoll() {
    if (observationTimer || !sessionId) return;
    observationTimer = window.setInterval(function () {
      call('remoteDesktop.observations.list', { session_id: sessionId }).then(function (result) {
        (result.observations || []).forEach(function (observation) {
          const id = String(observation.observation_id || '');
          if (!id || submittedObservations.has(id)) return;
          submittedObservations.add(id);
          submitObservation(observation).catch(function () {
            submittedObservations.delete(id);
          }).then(function () {
            window.setTimeout(function () { submittedObservations.delete(id); }, 30_000);
          });
        });
      }).catch(function () {});
    }, 550);
  }

  function applySecurityState(result) {
    const secure = Boolean(result && result.secure_surface);
    secureSurface = secure;
    app.dataset.secureSurface = secure ? 'true' : 'false';
    const banner = document.getElementById('permission-banner');
    if (secure) {
      banner.dataset.kind = 'security';
      banner.textContent = t('protectedSurface');
      banner.hidden = false;
    } else if (banner.dataset.kind === 'security') {
      banner.hidden = true;
      delete banner.dataset.kind;
      state('connected', t('connected'));
    }
    if (result && result.session) publishState(result.session);
  }

  function refreshSecurityState() {
    if (!sessionId) return Promise.resolve();
    return call('remoteDesktop.security.get', { session_id: sessionId })
      .then(applySecurityState)
      .catch(function () {});
  }

  function startSecurityPoll() {
    if (securityTimer || !sessionId) return;
    refreshSecurityState();
    securityTimer = window.setInterval(refreshSecurityState, 1200);
  }

  retryButton.addEventListener('click', connect);
  disconnectButton.addEventListener('click', disconnect);
  microphoneButton.addEventListener('click', setMicrophone);
  displayButton.addEventListener('click', showDisplays);
  fileButton.addEventListener('click', function () { fileMenu.hidden = !fileMenu.hidden; displayMenu.hidden = true; });
  document.getElementById('choose-files').addEventListener('click', function () { fileMenu.hidden = true; fileInput.click(); });
  document.getElementById('choose-folder').addEventListener('click', function () { fileMenu.hidden = true; folderInput.click(); });
  fileInput.addEventListener('change', function () { sendClipboardFiles(fileInput.files); fileInput.value = ''; });
  folderInput.addEventListener('change', function () { sendClipboardFiles(folderInput.files); folderInput.value = ''; });
  stage.addEventListener('dragover', function (event) {
    if (!event.dataTransfer || !event.dataTransfer.files || !event.dataTransfer.files.length) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  });
  stage.addEventListener('drop', function (event) {
    if (!event.dataTransfer || !event.dataTransfer.files || !event.dataTransfer.files.length) return;
    event.preventDefault();
    sendClipboardFiles(event.dataTransfer.files);
  });
  document.addEventListener('pointerdown', function (event) {
    window.parent.postMessage({ source: 'cyrene-plugin', type: 'interaction' }, '*');
    if (!displayMenu.hidden && !displayMenu.contains(event.target) && event.target !== displayButton) displayMenu.hidden = true;
    if (!fileMenu.hidden && !fileMenu.contains(event.target) && event.target !== fileButton) fileMenu.hidden = true;
  });

  window.addEventListener('message', function (event) {
    if (event.source !== window.parent) return;
    const message = event.data && typeof event.data === 'object' ? event.data : {};
    if (message.source !== 'cyrene-host') return;
    if (message.type === 'theme') {
      applyTheme(message.theme);
      return;
    }
    if (message.type === 'response' || message.type === 'host-response') {
      const request = pending.get(String(message.requestId || ''));
      if (!request) return;
      pending.delete(String(message.requestId || ''));
      if (message.ok === false) request.reject(new Error(String(message.error || t('pluginCallFailed'))));
      else request.resolve(message.result);
      return;
    }
    if (message.type === 'command') {
      const command = message.command && typeof message.command === 'object' ? message.command : {};
      const action = String(command.action || '');
      if (action === 'file_transfer') {
        fileMenu.hidden = !fileMenu.hidden;
        displayMenu.hidden = true;
      } else if (action === 'switch_display') {
        showDisplays();
      } else if (action === 'toggle_microphone') {
        setMicrophone();
      } else if (action === 'disconnect') {
        disconnect();
      }
      return;
    }
    if (message.type === 'init') {
      context = message.context || {};
      applyTheme(context.theme);
      applyLanguage(context.language || 'en');
      deviceId = String(context.instanceId || context.state && context.state.device_id || '');
      const cardState = context.state && typeof context.state === 'object' ? context.state : {};
      authorizedModes = Array.isArray(cardState.modes) ? cardState.modes.map(String) : null;
      targetPlatform = String(cardState.platform || cardState.subtitle || '');
      setText('device-name', String(cardState.title || context.instanceId || t('remoteDesktop')));
      const preferredMode = String(cardState.preferred_mode || 'current_desktop');
      const initialMode = authorizedModes === null || authorizedModes.indexOf(preferredMode) >= 0
        ? preferredMode
        : authorizedModes.indexOf('current_desktop') >= 0
          ? 'current_desktop'
          : String(authorizedModes[0] || 'current_desktop');
      selectMode(initialMode);
      if (!autoConnectStarted) {
        autoConnectStarted = true;
        window.setTimeout(connect, 0);
      }
    }
  });

  window.addEventListener('beforeunload', function () {
    closePeer();
    if (sessionId) call('remoteDesktop.session.disconnect', { session_id: sessionId }).catch(function () {});
  });
})();
