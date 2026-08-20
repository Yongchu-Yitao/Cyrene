'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  decideBrowserWindowOpen,
  popupWindowOpenResponse,
} = require('./browser-popup-policy');

test('ordinary target blank navigation stays in the Cyrene tab strip', () => {
  assert.deepEqual(decideBrowserWindowOpen({
    url: 'https://example.com/docs',
    disposition: 'foreground-tab',
    features: '',
  }), { mode: 'tab', url: 'https://example.com/docs' });
});

test('popup-shaped and POST windows retain a real opener window', () => {
  assert.deepEqual(decideBrowserWindowOpen({
    url: 'https://accounts.example.com/login',
    disposition: 'new-window',
    features: 'popup,width=640,height=800',
  }), {
    mode: 'popup',
    url: 'https://accounts.example.com/login',
    width: 640,
    height: 800,
  });
  assert.equal(decideBrowserWindowOpen({
    url: 'https://example.com/submit',
    disposition: 'foreground-tab',
    features: '',
    postBody: { data: [] },
  }).mode, 'popup');
  assert.equal(decideBrowserWindowOpen({
    url: 'https://accounts.example.com/login',
    disposition: 'default',
    frameName: 'oauth-login',
    features: '',
  }).mode, 'popup');
  assert.equal(decideBrowserWindowOpen({
    url: 'https://accounts.example.com/login',
    disposition: 'default',
    frameName: '_blank',
    features: 'popup',
  }).mode, 'popup');
});

test('unsafe schemes are denied and popup webPreferences stay isolated', () => {
  assert.equal(decideBrowserWindowOpen({ url: 'file:///etc/passwd', disposition: 'new-window' }).mode, 'deny');
  assert.equal(decideBrowserWindowOpen({ url: 'javascript:alert(1)', disposition: 'new-window' }).mode, 'deny');

  const response = popupWindowOpenResponse({ width: 520, height: 720 }, 'persist:cyrene-browser');
  assert.equal(response.action, 'allow');
  assert.equal(response.outlivesOpener, false);
  assert.deepEqual(response.overrideBrowserWindowOptions.webPreferences, {
    partition: 'persist:cyrene-browser',
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    backgroundThrottling: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    webviewTag: false,
  });
});
