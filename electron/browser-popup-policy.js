'use strict';

const POPUP_WIDTH = 520;
const POPUP_HEIGHT = 720;
const MIN_POPUP_WIDTH = 360;
const MIN_POPUP_HEIGHT = 420;
const MAX_POPUP_WIDTH = 1400;
const MAX_POPUP_HEIGHT = 1200;

function parseWindowFeatures(value) {
  const result = {};
  for (const part of String(value || '').split(',')) {
    const [rawName, ...rawValue] = part.trim().split('=');
    const name = String(rawName || '').trim().toLowerCase();
    if (!name) continue;
    result[name] = rawValue.length ? rawValue.join('=').trim() : 'yes';
  }
  return result;
}

function clampDimension(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value || ''), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, parsed));
}

function isSafeBrowserPageUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'http:'
      || url.protocol === 'https:'
      || (url.protocol === 'about:' && url.pathname === 'blank');
  } catch (_) {
    return false;
  }
}

function decideBrowserWindowOpen(details = {}) {
  const url = String(details.url || '');
  if (!isSafeBrowserPageUrl(url)) return { mode: 'deny', url };

  const features = parseWindowFeatures(details.features);
  const hasPopupGeometry = ['width', 'height', 'left', 'top', 'screenx', 'screeny']
    .some((name) => Object.prototype.hasOwnProperty.call(features, name));
  const popupFeature = Object.prototype.hasOwnProperty.call(features, 'popup')
    && !['0', 'no', 'false'].includes(String(features.popup).toLowerCase());
  const frameName = String(details.frameName || '').trim().toLowerCase();
  const hasNamedPopupTarget = Boolean(frameName)
    && !['_blank', '_self', '_parent', '_top'].includes(frameName);
  const needsOpenerWindow = details.disposition === 'new-window'
    || hasPopupGeometry
    || popupFeature
    || hasNamedPopupTarget
    || Boolean(details.postBody);
  if (!needsOpenerWindow) return { mode: 'tab', url };

  return {
    mode: 'popup',
    url,
    width: clampDimension(features.width, POPUP_WIDTH, MIN_POPUP_WIDTH, MAX_POPUP_WIDTH),
    height: clampDimension(features.height, POPUP_HEIGHT, MIN_POPUP_HEIGHT, MAX_POPUP_HEIGHT),
  };
}

function popupWindowOpenResponse(decision, partition) {
  return {
    action: 'allow',
    outlivesOpener: false,
    overrideBrowserWindowOptions: {
      width: decision.width || POPUP_WIDTH,
      height: decision.height || POPUP_HEIGHT,
      minWidth: MIN_POPUP_WIDTH,
      minHeight: MIN_POPUP_HEIGHT,
      autoHideMenuBar: true,
      alwaysOnTop: false,
      fullscreen: false,
      kiosk: false,
      modal: false,
      frame: true,
      transparent: false,
      webPreferences: {
        partition,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
        webviewTag: false,
      },
    },
  };
}

module.exports = {
  decideBrowserWindowOpen,
  isSafeBrowserPageUrl,
  parseWindowFeatures,
  popupWindowOpenResponse,
};
