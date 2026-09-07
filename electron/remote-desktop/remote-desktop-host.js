(function () {
  'use strict';

  const host = window.cyreneRemoteDesktopHost;
  let sessionId = '';
  let peer = null;
  let stream = null;
  let inputChannel = null;
  let controlChannel = null;
  let qualityMode = 'auto';
  let permissions = {};
  let microphoneEnabled = false;
  let viewportConstraints = null;
  let nativeSurface = null;
  let autoAdaptationLevel = 0;
  let autoHealthySamples = 0;
  let autoStatsTimer = null;
  let previousAutoStats = null;

  const qualityConstraints = {
    auto: { width: { ideal: 1600 }, height: { ideal: 900 }, frameRate: { ideal: 30, max: 30 } },
    smooth: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 60, max: 60 } },
    balanced: { width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 30, max: 45 } },
    clear: { width: { ideal: 3840 }, height: { ideal: 2160 }, frameRate: { ideal: 30, max: 30 } },
  };

  const transmissionProfiles = {
    smooth: { maxBitrate: 10_000_000, maxFramerate: 45 },
    balanced: { maxBitrate: 22_000_000, maxFramerate: 30 },
    clear: { maxBitrate: 42_000_000, maxFramerate: 30 },
    auto: { maxBitrate: 8_000_000, maxFramerate: 30 },
  };

  const autoTransmissionProfiles = [
    { maxBitrate: 8_000_000, maxFramerate: 30, scaleResolutionDownBy: 1 },
    { maxBitrate: 5_000_000, maxFramerate: 30, scaleResolutionDownBy: 1.5 },
    { maxBitrate: 3_000_000, maxFramerate: 24, scaleResolutionDownBy: 2 },
  ];

  function currentVideoConstraints() {
    const quality = qualityConstraints[qualityMode] || qualityConstraints.auto;
    let constraints = { ...quality };
    if (viewportConstraints) {
      const requestedWidth = Math.max(1, Number(viewportConstraints.width) || 1);
      const requestedHeight = Math.max(1, Number(viewportConstraints.height) || 1);
      const maxWidth = Math.max(1, Number(quality.width && quality.width.ideal) || requestedWidth);
      const maxHeight = Math.max(1, Number(quality.height && quality.height.ideal) || requestedHeight);
      const scale = Math.min(1, maxWidth / requestedWidth, maxHeight / requestedHeight);
      constraints = {
        width: { ideal: Math.max(1, Math.round(requestedWidth * scale)) },
        height: { ideal: Math.max(1, Math.round(requestedHeight * scale)) },
        frameRate: quality.frameRate,
      };
    }
    // The controller surface hides its local pointer and shows the controlled
    // machine's captured pointer. This guarantees one authoritative cursor on
    // Windows, where Chromium may ignore a request to exclude the cursor.
    constraints.cursor = 'always';
    return constraints;
  }

  function waitForIceGathering(pc, timeoutMs) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise(function (resolve) {
      let settled = false;
      const finish = function () {
        if (settled) return;
        settled = true;
        pc.removeEventListener('icegatheringstatechange', changed);
        resolve();
      };
      const changed = function () { if (pc.iceGatheringState === 'complete') finish(); };
      pc.addEventListener('icegatheringstatechange', changed);
      window.setTimeout(finish, timeoutMs || 8000);
    });
  }

  function safeSend(channel, value) {
    if (!channel || channel.readyState !== 'open') return false;
    channel.send(JSON.stringify(value));
    return true;
  }

  function bindChannel(channel) {
    if (channel.label === 'cyrene-input') {
      inputChannel = channel;
      channel.onmessage = function (event) {
        try { host.input({ session_id: sessionId, event: JSON.parse(String(event.data || '{}')) }); }
        catch (_) {}
      };
      return;
    }
    controlChannel = channel;
    channel.onmessage = function (event) {
      try { host.control({ session_id: sessionId, message: JSON.parse(String(event.data || '{}')) }); }
      catch (_) {}
    };
  }

  async function configureVideoSender() {
    if (!peer) return;
    const sender = peer.getSenders().find(function (candidate) {
      return candidate.track && candidate.track.kind === 'video';
    });
    if (!sender) return;
    const profile = qualityMode === 'auto'
      ? autoTransmissionProfiles[autoAdaptationLevel]
      : transmissionProfiles[qualityMode] || transmissionProfiles.auto;
    const parameters = sender.getParameters();
    if (!Array.isArray(parameters.encodings) || !parameters.encodings.length) return;
    parameters.encodings[0].maxBitrate = profile.maxBitrate;
    parameters.encodings[0].maxFramerate = profile.maxFramerate;
    parameters.encodings[0].scaleResolutionDownBy = Number(profile.scaleResolutionDownBy || 1);
    parameters.degradationPreference = 'maintain-framerate';
    await sender.setParameters(parameters).catch(function () {});
  }

  async function refreshAutoAdaptation() {
    const activePeer = peer;
    if (!activePeer || qualityMode !== 'auto' || activePeer.connectionState !== 'connected') return;
    try {
      const reports = await activePeer.getStats();
      if (peer !== activePeer || qualityMode !== 'auto') return;
      let outbound = null;
      let remoteInbound = null;
      let selectedPair = null;
      let selectedPairId = '';
      reports.forEach(function (report) {
        if (report.type === 'transport' && report.selectedCandidatePairId) {
          selectedPairId = String(report.selectedCandidatePairId);
        }
        if (report.type === 'outbound-rtp' && report.kind === 'video' && !report.isRemote) outbound = report;
        if (report.type === 'remote-inbound-rtp' && report.kind === 'video') remoteInbound = report;
      });
      if (selectedPairId && typeof reports.get === 'function') selectedPair = reports.get(selectedPairId) || null;
      if (!selectedPair) {
        reports.forEach(function (report) {
          if (selectedPair || report.type !== 'candidate-pair' || report.state !== 'succeeded') return;
          if (report.selected === true || report.nominated === true) selectedPair = report;
        });
      }
      if (!outbound) return;
      const current = {
        frames: Number(outbound.framesEncoded || 0),
        packets: Number(outbound.packetsSent || 0),
        encodeSeconds: Number(outbound.totalEncodeTime || 0),
        lost: Number(remoteInbound && remoteInbound.packetsLost || 0),
      };
      const previous = previousAutoStats;
      previousAutoStats = current;
      if (!previous) return;
      const frameDelta = Math.max(0, current.frames - previous.frames);
      const packetDelta = Math.max(0, current.packets - previous.packets);
      const lostDelta = Math.max(0, current.lost - previous.lost);
      const encodeDelta = Math.max(0, current.encodeSeconds - previous.encodeSeconds);
      const encodeMs = frameDelta > 0 ? encodeDelta * 1000 / frameDelta : 0;
      const lossRatio = packetDelta + lostDelta > 0 ? lostDelta / (packetDelta + lostDelta) : 0;
      const availableBitrate = Number(selectedPair && selectedPair.availableOutgoingBitrate || 0);
      const roundTripTime = Number(
        remoteInbound && remoteInbound.roundTripTime
        || selectedPair && selectedPair.currentRoundTripTime
        || 0,
      );
      const activeProfile = autoTransmissionProfiles[autoAdaptationLevel];
      const limitation = String(outbound.qualityLimitationReason || 'none');
      const pressured = ['cpu', 'bandwidth'].includes(limitation)
        || encodeMs > 28
        || lossRatio > 0.03
        || roundTripTime > 0.15
        || (availableBitrate > 0 && availableBitrate < activeProfile.maxBitrate * 1.15);
      if (pressured) {
        autoHealthySamples = 0;
        if (autoAdaptationLevel < autoTransmissionProfiles.length - 1) {
          autoAdaptationLevel += 1;
          await configureVideoSender();
        }
        return;
      }
      const healthy = frameDelta > 0
        && limitation === 'none'
        && encodeMs < 18
        && lossRatio < 0.01
        && roundTripTime < 0.08
        && (availableBitrate <= 0 || availableBitrate > activeProfile.maxBitrate * 1.5);
      autoHealthySamples = healthy ? autoHealthySamples + 1 : 0;
      if (autoHealthySamples >= 4 && autoAdaptationLevel > 0) {
        autoHealthySamples = 0;
        autoAdaptationLevel -= 1;
        await configureVideoSender();
      }
    } catch (_) {}
  }

  function resetAutoAdaptation() {
    if (autoStatsTimer) window.clearInterval(autoStatsTimer);
    autoStatsTimer = null;
    autoAdaptationLevel = 0;
    autoHealthySamples = 0;
    previousAutoStats = null;
    if (qualityMode !== 'auto') return;
    autoStatsTimer = window.setInterval(refreshAutoAdaptation, 2000);
  }

  function releaseNativeSurface() {
    if (!nativeSurface) return;
    if (nativeSurface.animationFrame) cancelAnimationFrame(nativeSurface.animationFrame);
    nativeSurface.image.src = '';
    nativeSurface.image.remove();
    nativeSurface.canvas.remove();
    nativeSurface = null;
  }

  async function nativeVideoStream(config) {
    releaseNativeSurface();
    const image = document.createElement('img');
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d', { alpha: false });
    if (!context) throw new Error('desktop_native_capture_canvas_unavailable');
    image.crossOrigin = 'anonymous';
    image.style.display = 'none';
    canvas.style.display = 'none';
    canvas.width = Math.max(1, Number(config.width || 1920));
    canvas.height = Math.max(1, Number(config.height || 1080));
    document.body.appendChild(image);
    document.body.appendChild(canvas);
    await new Promise(function (resolve, reject) {
      const timeout = window.setTimeout(function () {
        reject(new Error('desktop_native_capture_image_timeout'));
      }, 8000);
      image.onload = function () {
        window.clearTimeout(timeout);
        resolve();
      };
      image.onerror = function () {
        window.clearTimeout(timeout);
        reject(new Error('desktop_native_capture_image_failed'));
      };
      image.src = String(config.url || '');
    });
    const surface = { image: image, canvas: canvas, animationFrame: 0 };
    const draw = function () {
      try { context.drawImage(image, 0, 0, canvas.width, canvas.height); } catch (_) {}
      surface.animationFrame = requestAnimationFrame(draw);
    };
    draw();
    nativeSurface = surface;
    const captured = canvas.captureStream(Math.max(1, Number(config.frame_rate || 30)));
    const videoTrack = captured.getVideoTracks()[0];
    if (videoTrack) videoTrack.contentHint = 'detail';
    return captured;
  }

  async function captureSystemAudio() {
    if (permissions.system_audio !== true) return null;
    try {
      const audioCapture = await navigator.mediaDevices.getDisplayMedia({
        video: currentVideoConstraints(),
        audio: true,
        systemAudio: 'include',
      });
      audioCapture.getVideoTracks().forEach(function (track) { track.stop(); });
      return audioCapture.getAudioTracks()[0] || null;
    } catch (_) {
      return null;
    }
  }

  async function capture(nativeCapture) {
    if (nativeCapture && nativeCapture.url) {
      const next = await nativeVideoStream(nativeCapture);
      const audio = await captureSystemAudio();
      if (audio) next.addTrack(audio);
      return next;
    }
    const options = {
      video: currentVideoConstraints(),
      audio: permissions.system_audio === true,
    };
    if (permissions.system_audio === true) options.systemAudio = 'include';
    let next;
    try {
      next = await navigator.mediaDevices.getDisplayMedia(options);
    } catch (error) {
      if (permissions.system_audio !== true) throw error;
      // Some Linux/driver combinations expose PipeWire or PulseAudio tools but
      // cannot provide a Chromium loopback track. Keep video available and
      // report the actual audio result to the controller instead of failing the
      // entire desktop session.
      next = await navigator.mediaDevices.getDisplayMedia({
        video: currentVideoConstraints(),
        audio: false,
      });
    }
    const video = next.getVideoTracks()[0];
    if (!video) throw new Error('desktop_video_track_unavailable');
    await video.applyConstraints(currentVideoConstraints()).catch(function () {});
    return next;
  }

  async function replaceCapture(nativeCapture) {
    const next = await capture(nativeCapture);
    const nextVideo = next.getVideoTracks()[0];
    const nextAudio = next.getAudioTracks()[0] || null;
    const videoSender = peer.getSenders().find(function (sender) { return sender.track && sender.track.kind === 'video'; });
    const audioSender = peer.getSenders().find(function (sender) { return sender.track && sender.track.kind === 'audio'; });
    if (videoSender) await videoSender.replaceTrack(nextVideo);
    else peer.addTrack(nextVideo, next);
    if (nextAudio) {
      if (audioSender) await audioSender.replaceTrack(nextAudio);
      else peer.addTrack(nextAudio, next);
    } else if (audioSender) {
      await audioSender.replaceTrack(null);
    }
    if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
    stream = next;
    await configureVideoSender();
  }

  async function start(payload) {
    sessionId = String(payload.session_id || '');
    qualityMode = String(payload.quality_mode || 'auto');
    permissions = payload.permissions && typeof payload.permissions === 'object' ? payload.permissions : {};
    peer = new RTCPeerConnection({ iceServers: Array.isArray(payload.ice_servers) ? payload.ice_servers : [] });
    peer.ondatachannel = function (event) { bindChannel(event.channel); };
    peer.onconnectionstatechange = function () {
      host.state({ session_id: sessionId, connection_state: peer.connectionState });
    };
    peer.ontrack = function (event) {
      if (!event.track || event.track.kind !== 'audio') return;
      const audio = document.getElementById('microphone');
      audio.srcObject = new MediaStream([event.track]);
      audio.muted = permissions.microphone !== true || !microphoneEnabled;
      const sinkId = String(payload.microphone_sink_id || '');
      const selectSink = sinkId && typeof audio.setSinkId === 'function'
        ? audio.setSinkId(sinkId) : Promise.resolve();
      selectSink.then(function () { return audio.play(); }).catch(function () {});
    };
    await peer.setRemoteDescription(payload.offer);
    stream = await capture(payload.native_capture);
    stream.getTracks().forEach(function (track) { peer.addTrack(track, stream); });
    await configureVideoSender();
    peer.getTransceivers().forEach(function (transceiver) {
      if (!transceiver.receiver || !transceiver.receiver.track || transceiver.receiver.track.kind !== 'audio') return;
      const sendsAudio = Boolean(transceiver.sender && transceiver.sender.track && transceiver.sender.track.kind === 'audio');
      transceiver.direction = permissions.microphone === true
        ? (sendsAudio ? 'sendrecv' : 'recvonly')
        : (sendsAudio ? 'sendonly' : 'inactive');
    });
    const answer = await peer.createAnswer();
    await peer.setLocalDescription(answer);
    await configureVideoSender();
    resetAutoAdaptation();
    await waitForIceGathering(peer, 8000);
    const settings = stream.getVideoTracks()[0].getSettings();
    host.answer({
      ok: true,
      session_id: sessionId,
      answer: { type: peer.localDescription.type, sdp: peer.localDescription.sdp },
      width: Number(settings.width || 0),
      height: Number(settings.height || 0),
      system_audio: stream.getAudioTracks().length > 0,
    });
  }

  host.onStart(function (payload) {
    start(payload).catch(function (error) {
      host.answer({
        ok: false,
        session_id: String(payload && payload.session_id || ''),
        code: String(error && error.name || 'desktop_capture_failed'),
        error: String(error && error.message || error || 'Desktop capture failed.'),
      });
    });
  });

  host.onCommand(function (command) {
    const operation = String(command.operation || '');
    if (operation === 'disconnect') {
      if (autoStatsTimer) window.clearInterval(autoStatsTimer);
      autoStatsTimer = null;
      if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
      releaseNativeSurface();
      if (peer) peer.close();
      stream = null;
      peer = null;
      return;
    }
    if (operation === 'select_display') {
      replaceCapture(command.native_capture).then(function () {
        host.state({ session_id: sessionId, display_id: String(command.display_id || '') });
      }).catch(function (error) {
        host.state({ session_id: sessionId, error: String(error && error.message || error) });
      });
      return;
    }
    if (operation === 'set_quality') {
      qualityMode = String(command.quality_mode || 'auto');
      resetAutoAdaptation();
      if (command.native_capture) {
        replaceCapture(command.native_capture).catch(function (error) {
          host.state({ session_id: sessionId, error: String(error && error.message || error) });
        });
        return;
      }
      const track = stream && stream.getVideoTracks()[0];
      if (track) track.applyConstraints(currentVideoConstraints()).catch(function () {});
      configureVideoSender();
      return;
    }
    if (operation === 'set_viewport') {
      const ratio = Math.max(0.5, Math.min(2, Number(command.device_pixel_ratio || 1)));
      viewportConstraints = {
        width: Math.max(320, Math.min(3840, Math.round(Number(command.width || 1) * ratio))),
        height: Math.max(240, Math.min(2160, Math.round(Number(command.height || 1) * ratio))),
      };
      if (command.native_capture) {
        replaceCapture(command.native_capture).catch(function (error) {
          host.state({ session_id: sessionId, error: String(error && error.message || error) });
        });
        return;
      }
      const track = stream && stream.getVideoTracks()[0];
      if (track) track.applyConstraints(currentVideoConstraints()).catch(function () {});
      return;
    }
    if (operation === 'set_microphone') {
      const audio = document.getElementById('microphone');
      microphoneEnabled = permissions.microphone === true && command.enabled === true;
      audio.muted = !microphoneEnabled;
      return;
    }
    if (operation === 'security_state') {
      safeSend(controlChannel, {
        type: 'security:surface',
        secure_surface: command.secure_surface === true,
        security_epoch: Math.max(0, Number(command.security_epoch || 0)),
      });
    }
  });

  host.onClipboard(function (message) {
    safeSend(controlChannel, { type: 'clipboard:text', text: String(message.text || ''), revision: Number(message.revision || 0) });
  });

  host.onClipboardImageOffer(function (message) {
    safeSend(controlChannel, {
      type: 'clipboard:image-offer',
      offer_id: String(message.offer_id || ''),
      sha256: String(message.sha256 || ''),
      size: Number(message.size || 0),
      width: Number(message.width || 0),
      height: Number(message.height || 0),
    });
  });

  host.onClipboardFileOffer(function (message) {
    safeSend(controlChannel, {
      type: 'clipboard:file-offer',
      offer_id: String(message.offer_id || ''),
      entries: Array.isArray(message.entries) ? message.entries.slice(0, 512) : [],
    });
  });
})();
