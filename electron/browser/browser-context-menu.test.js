'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { buildBrowserContextMenuTemplate } = require('./browser-context-menu');

function labels(template) {
  return template.filter((item) => item.label).map((item) => item.label);
}

test('link context menu opens an internal tab and copies the address', () => {
  const calls = [];
  const template = buildBrowserContextMenuTemplate({
    params: { linkURL: 'https://example.com/path', mediaType: 'none' },
    webContents: { canGoBack: () => false, canGoForward: () => true, reload: () => calls.push('reload') },
    openTab: (url) => calls.push(['tab', url]),
    copyText: (text) => calls.push(['copy', text]),
  });

  assert.deepEqual(labels(template).slice(0, 2), ['Open Link in New Tab', 'Copy Link Address']);
  template.find((item) => item.label === 'Open Link in New Tab').click();
  template.find((item) => item.label === 'Copy Link Address').click();
  assert.deepEqual(calls, [
    ['tab', 'https://example.com/path'],
    ['copy', 'https://example.com/path'],
  ]);
  assert.equal(template.find((item) => item.label === 'Back').enabled, false);
  assert.equal(template.find((item) => item.label === 'Forward').enabled, true);
});

test('editable context menu targets the page webContents', () => {
  const calls = [];
  const webContents = {
    canGoBack: () => false,
    canGoForward: () => false,
    undo: () => calls.push('undo'),
    paste: () => calls.push('paste'),
  };
  const template = buildBrowserContextMenuTemplate({
    params: {
      isEditable: true,
      editFlags: { canUndo: true, canRedo: false, canCut: false, canCopy: false, canPaste: true, canSelectAll: true },
    },
    webContents,
  });

  assert.equal(template.find((item) => item.label === 'Undo').enabled, true);
  assert.equal(template.find((item) => item.label === 'Redo').enabled, false);
  template.find((item) => item.label === 'Undo').click();
  template.find((item) => item.label === 'Paste').click();
  assert.deepEqual(calls, ['undo', 'paste']);
});

test('media actions use the page download flow', () => {
  const calls = [];
  const template = buildBrowserContextMenuTemplate({
    params: { mediaType: 'image', srcURL: 'https://example.com/image.png' },
    webContents: {
      canGoBack: () => false,
      canGoForward: () => false,
      downloadURL: (url) => calls.push(url),
    },
  });

  template.find((item) => item.label === 'Save Image').click();
  assert.deepEqual(calls, ['https://example.com/image.png']);
});
