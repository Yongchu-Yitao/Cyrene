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

  const qualityConstraints = {
    auto: { width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 45, max: 60 } },
    smooth: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 60, max: 60 } },
    balanced: { width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 30, max: 45 } },
    clear: { width: { ideal: 3840 }, height: { ideal: 2160 }, frameRate: { ideal: 30, max: 30 } },
  };

  function currentVideoConstraints() {
    const quality = qualityConstraints[qualityMode] || qualityConstraints.auto;
    if (!viewportConstraints) return quality;
    return {
      width: { ideal: viewportConstraints.width },
      height: { ideal: viewportConstraints.height },
      frameRate: quality.frameRate,
    };
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

  async function capture() {
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

  async function replaceCapture() {
    const next = await capture();
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
    stream = await capture();
    stream.getTracks().forEach(function (track) { peer.addTrack(track, stream); });
    peer.getTransceivers().forEach(function (transceiver) {
      if (!transceiver.receiver || !transceiver.receiver.track || transceiver.receiver.track.kind !== 'audio') return;
      const sendsAudio = Boolean(transceiver.sender && transceiver.sender.track && transceiver.sender.track.kind === 'audio');
      transceiver.direction = permissions.microphone === true
        ? (sendsAudio ? 'sendrecv' : 'recvonly')
        : (sendsAudio ? 'sendonly' : 'inactive');
    });
    const answer = await peer.createAnswer();
    await peer.setLocalDescription(answer);
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
      if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
      if (peer) peer.close();
      stream = null;
      peer = null;
      return;
    }
    if (operation === 'select_display') {
      replaceCapture().then(function () {
        host.state({ session_id: sessionId, display_id: String(command.display_id || '') });
      }).catch(function (error) {
        host.state({ session_id: sessionId, error: String(error && error.message || error) });
      });
      return;
    }
    if (operation === 'set_quality') {
      qualityMode = String(command.quality_mode || 'auto');
      const track = stream && stream.getVideoTracks()[0];
      if (track) track.applyConstraints(currentVideoConstraints()).catch(function () {});
      return;
    }
    if (operation === 'set_viewport') {
      const ratio = Math.max(0.5, Math.min(2, Number(command.device_pixel_ratio || 1)));
      viewportConstraints = {
        width: Math.max(320, Math.min(3840, Math.round(Number(command.width || 1) * ratio))),
        height: Math.max(240, Math.min(2160, Math.round(Number(command.height || 1) * ratio))),
      };
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
