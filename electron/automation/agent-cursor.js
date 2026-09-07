'use strict';

const AGENT_CURSOR_SIZE = 32;
const AGENT_CURSOR_HOTSPOT = Object.freeze({ x: 6, y: 6 });
const AGENT_CURSOR_IDLE_MS = 3000;
const AGENT_CURSOR_FADE_IN_MS = 150;
const AGENT_CURSOR_FADE_OUT_MS = 250;
const AGENT_CURSOR_MOVE_MS = 180;
const AGENT_CURSOR_PRESS_MS = 100;
const AGENT_CURSOR_MAX_VISUAL_SCALE = 8;

const AGENT_CURSOR_SVG = `
<svg width="32" height="32" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="cyrene-agent-cursor-fill" x1="13" y1="8" x2="48" y2="52" gradientUnits="userSpaceOnUse">
      <stop stop-color="#A78BFA"/>
      <stop offset="0.52" stop-color="#6366F1"/>
      <stop offset="1" stop-color="#22D3EE"/>
    </linearGradient>
  </defs>
  <path d="M16.8 11.1 C13.5 9.2 10.5 11.5 11.2 15.2 L17.3 46.9 C18.2 51.8 22.5 53.1 25.2 48.9 L30.5 40.6 C31.6 38.9 32.7 38.2 34.7 37.8 L45.8 35.7 C50.8 34.8 51.9 30.3 47.5 27.8 L16.8 11.1Z" fill="url(#cyrene-agent-cursor-fill)" stroke="#FFFFFF" stroke-width="2.5" stroke-linejoin="round"/>
</svg>`.trim();

function agentCursorVisualScaleForZoom(value) {
  const zoom = Number(value);
  if (!Number.isFinite(zoom) || zoom <= 0) return 1;
  return Math.max(1, Math.min(AGENT_CURSOR_MAX_VISUAL_SCALE, 1 / zoom));
}

function agentCursorCommand(config = {}) {
  const requestedMoveDuration = Number(config.moveDurationMs);
  const requestedVisualScale = Number(config.visualScale);
  const payload = {
    x: Number(config.x) || 0,
    y: Number(config.y) || 0,
    press: config.press === true,
    running: config.running === true,
    moveDurationMs: Number.isFinite(requestedMoveDuration)
      ? Math.max(0, requestedMoveDuration)
      : AGENT_CURSOR_MOVE_MS,
    visualScale: Number.isFinite(requestedVisualScale)
      ? Math.max(0.125, Math.min(8, requestedVisualScale))
      : 1,
    idleMs: AGENT_CURSOR_IDLE_MS,
    fadeInMs: AGENT_CURSOR_FADE_IN_MS,
    fadeOutMs: AGENT_CURSOR_FADE_OUT_MS,
    pressMs: AGENT_CURSOR_PRESS_MS,
    hotspotX: AGENT_CURSOR_HOTSPOT.x,
    hotspotY: AGENT_CURSOR_HOTSPOT.y,
    size: AGENT_CURSOR_SIZE,
    svg: AGENT_CURSOR_SVG,
  };
  return `(() => {
    const config = ${JSON.stringify(payload)};
    const stateKey = '__cyreneAgentCursorState';
    const id = 'cyrene-agent-cursor';
    let state = window[stateKey];
    if (!state) {
      state = window[stateKey] = {
        sequence: 0, visible: false, fading: false, idleTimer: null, fadeTimer: null,
        targetElement: null, listenersInstalled: false, observer: null,
        running: config.running, lastActivityAt: 0, lastX: null, lastY: null,
        lastVisualScale: null,
      };
    }
    state.running = config.running;
    state.sequence += 1;
    const sequence = state.sequence;
    if (state.idleTimer) clearTimeout(state.idleTimer);
    if (state.fadeTimer) clearTimeout(state.fadeTimer);
    state.idleTimer = null;
    state.fadeTimer = null;

    let cursor = document.getElementById(id);
    if (!cursor) {
      cursor = document.createElement('div');
      cursor.id = id;
      cursor.setAttribute('aria-hidden', 'true');
      cursor.style.cssText = [
        'position:fixed', 'left:0', 'top:0', 'width:' + config.size + 'px',
        'height:' + config.size + 'px', 'pointer-events:none', 'user-select:none',
        'z-index:2147483647', 'opacity:0', 'contain:layout style paint',
        'will-change:transform,opacity', 'transform-origin:0 0',
      ].join(';');
      const art = document.createElement('div');
      art.setAttribute('data-cyrene-agent-cursor-art', 'true');
      art.style.cssText = 'width:100%;height:100%;transform-origin:' + config.hotspotX + 'px ' + config.hotspotY + 'px';
      art.innerHTML = config.svg;
      cursor.appendChild(art);
      (document.body || document.documentElement).appendChild(cursor);
      if (!document.getElementById(id + '-style')) {
        const style = document.createElement('style');
        style.id = id + '-style';
        style.textContent = '@keyframes cyrene-agent-cursor-press{0%,100%{transform:scale(1)}50%{transform:scale(.92)}}'
          + '#cyrene-agent-cursor [data-cyrene-agent-cursor-art].is-pressing{animation:cyrene-agent-cursor-press '
          + config.pressMs + 'ms ease-in-out 1}'
          + '@media (prefers-reduced-motion:reduce){#cyrene-agent-cursor [data-cyrene-agent-cursor-art].is-pressing{animation:none!important}}';
        (document.head || document.documentElement).appendChild(style);
      }
    }

    const position = 'translate3d(' + Math.round(config.x - (config.hotspotX * config.visualScale)) + 'px,'
      + Math.round(config.y - (config.hotspotY * config.visualScale)) + 'px,0) scale(' + config.visualScale + ')';
    const wasVisible = state.visible;
    const wasFading = state.fading;
    const moved = !wasVisible || state.lastX !== config.x || state.lastY !== config.y
      || state.lastVisualScale !== config.visualScale;
    state.visible = true;
    state.fading = false;
    state.lastActivityAt = Date.now();
    state.lastX = config.x;
    state.lastY = config.y;
    state.lastVisualScale = config.visualScale;
    state.targetElement = document.elementFromPoint
      ? document.elementFromPoint(Math.round(config.x), Math.round(config.y))
      : null;

    state.fadeNow = (expectedSequence = null) => {
      if (expectedSequence !== null && state.sequence !== expectedSequence) return false;
      if (state.idleTimer) clearTimeout(state.idleTimer);
      if (state.fadeTimer) clearTimeout(state.fadeTimer);
      state.idleTimer = null;
      state.fading = true;
      cursor.style.transition = 'opacity ' + config.fadeOutMs + 'ms ease-in';
      cursor.style.opacity = '0';
      const current = state.sequence;
      state.fadeTimer = setTimeout(() => {
        if (state.sequence !== current) return;
        state.visible = false;
        state.fading = false;
        state.targetElement = null;
      }, config.fadeOutMs);
      return true;
    };
    if (!state.listenersInstalled) {
      state.listenersInstalled = true;
      document.addEventListener('visibilitychange', () => {
        if (document.hidden || document.visibilityState === 'hidden') state.fadeNow && state.fadeNow();
      });
      window.addEventListener('pagehide', () => state.fadeNow && state.fadeNow());
      if (window.MutationObserver) {
        state.observer = new MutationObserver(() => {
          const target = state.targetElement;
          if (!target || !state.visible) return;
          let unavailable = target.isConnected === false;
          if (!unavailable && target.getBoundingClientRect) {
            const rect = target.getBoundingClientRect();
            const style = getComputedStyle(target);
            unavailable = (rect.width <= 0 && rect.height <= 0)
              || style.display === 'none' || style.visibility === 'hidden';
          }
          if (unavailable && state.fadeNow) state.fadeNow(state.sequence);
        });
        state.observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
      }
    }

    if (!wasVisible) {
      cursor.style.transition = 'none';
      cursor.style.transform = position;
      cursor.style.opacity = '0';
      void cursor.offsetWidth;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (state.sequence !== sequence) return;
        cursor.style.transition = 'opacity ' + config.fadeInMs + 'ms ease-out';
        cursor.style.opacity = '1';
      }));
    } else {
      if (wasFading) {
        cursor.style.transition = 'none';
        cursor.style.opacity = '1';
        void cursor.offsetWidth;
      }
      if (moved) {
        cursor.style.transition = 'transform ' + config.moveDurationMs
          + 'ms cubic-bezier(.2,.8,.2,1), opacity ' + config.fadeOutMs + 'ms ease-in';
        cursor.style.transform = position;
      }
      cursor.style.opacity = '1';
    }

    if (config.press) {
      const art = cursor.querySelector('[data-cyrene-agent-cursor-art]');
      if (art) {
        art.classList.remove('is-pressing');
        void art.offsetWidth;
        art.classList.add('is-pressing');
        setTimeout(() => {
          if (state.sequence === sequence) art.classList.remove('is-pressing');
        }, config.pressMs);
      }
    }

    // Running describes the agent lifecycle, not cursor activity. Keeping
    // the last pointer visible for an entire run makes an old target look like
    // the current operation whenever the agent switches tools or surfaces.
    // Every actual cursor update refreshes this timer instead.
    state.idleTimer = setTimeout(() => {
      if (state.fadeNow) state.fadeNow(sequence);
    }, config.idleMs);
    return {
      sequence,
      first: !wasVisible,
      moved,
      waitMs: !wasVisible ? config.fadeInMs + 34 : (moved ? config.moveDurationMs : 0),
      pressMs: config.press ? config.pressMs : 0,
    };
  })()`;
}

function agentCursorCompletionCommand(sequence, config = {}) {
  const payload = {
    sequence: Number(sequence),
    press: config.press === true,
  };
  return `(async () => {
    const config = ${JSON.stringify(payload)};
    const state = window.__cyreneAgentCursorState;
    const cursor = document.getElementById('cyrene-agent-cursor');
    if (!state || !cursor || state.sequence !== config.sequence) return false;

    // The initial fade is installed on the second animation frame. Waiting for
    // those frames lets getAnimations() observe both that fade and ordinary
    // transform transitions without guessing how busy the renderer is.
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if (state.sequence !== config.sequence) return false;

    let animations = cursor.getAnimations();
    if (config.press) {
      const art = cursor.querySelector('[data-cyrene-agent-cursor-art]');
      if (art && typeof art.getAnimations === 'function') {
        animations = animations.concat(art.getAnimations());
      }
    }
    animations = animations.filter((animation) => (
      animation && animation.finished
      && animation.playState !== 'finished' && animation.playState !== 'idle'
    ));
    if (!animations.length) return state.sequence === config.sequence;

    await Promise.allSettled(animations.map((animation) => animation.finished));
    return state.sequence === config.sequence;
  })()`;
}

function agentCursorRunningCommand(running) {
  const nextRunning = running === true;
  return `(() => {
    const state = window.__cyreneAgentCursorState;
    if (!state) return false;
    state.running = ${JSON.stringify(nextRunning)};
    // Do not cancel, restart, or resurrect cursor activity when a run starts
    // or stops. Cursor visibility is owned by real pointer updates.
    return true;
  })()`;
}

function agentCursorHideCommand(sequence = null) {
  const requestedSequence = Number.isFinite(Number(sequence)) ? Number(sequence) : null;
  return `(() => {
    const state = window.__cyreneAgentCursorState;
    const cursor = document.getElementById('cyrene-agent-cursor');
    if (!state || !cursor) return false;
    const requested = ${JSON.stringify(requestedSequence)};
    if (requested !== null && state.sequence !== requested) return false;
    if (state.idleTimer) clearTimeout(state.idleTimer);
    if (state.fadeTimer) clearTimeout(state.fadeTimer);
    state.idleTimer = null;
    state.fading = true;
    cursor.style.transition = 'opacity ${AGENT_CURSOR_FADE_OUT_MS}ms ease-in';
    cursor.style.opacity = '0';
    const current = state.sequence;
    state.fadeTimer = setTimeout(() => {
      if (state.sequence !== current) return;
      state.visible = false;
      state.fading = false;
      state.targetElement = null;
    }, ${AGENT_CURSOR_FADE_OUT_MS});
    return true;
  })()`;
}

function agentCursorOverlayHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{width:100%;height:100%;margin:0;overflow:hidden;background:transparent;pointer-events:none}
  </style></head><body></body></html>`;
}

module.exports = {
  AGENT_CURSOR_FADE_IN_MS,
  AGENT_CURSOR_FADE_OUT_MS,
  AGENT_CURSOR_HOTSPOT,
  AGENT_CURSOR_IDLE_MS,
  AGENT_CURSOR_MOVE_MS,
  AGENT_CURSOR_MAX_VISUAL_SCALE,
  AGENT_CURSOR_PRESS_MS,
  AGENT_CURSOR_SIZE,
  AGENT_CURSOR_SVG,
  agentCursorCommand,
  agentCursorCompletionCommand,
  agentCursorHideCommand,
  agentCursorOverlayHtml,
  agentCursorRunningCommand,
  agentCursorVisualScaleForZoom,
};
