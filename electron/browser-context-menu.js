'use strict';

const DEFAULT_LABELS = Object.freeze({
  back: 'Back',
  forward: 'Forward',
  reload: 'Reload',
  openLinkNewTab: 'Open Link in New Tab',
  copyLink: 'Copy Link Address',
  openImageNewTab: 'Open Image in New Tab',
  saveImage: 'Save Image',
  openMediaNewTab: 'Open Media in New Tab',
  saveMedia: 'Save Media',
  undo: 'Undo',
  redo: 'Redo',
  cut: 'Cut',
  copy: 'Copy',
  paste: 'Paste',
  selectAll: 'Select All',
  addToDictionary: 'Add to Dictionary',
  inspectElement: 'Inspect Element',
});

function safeCall(target, method, ...args) {
  if (!target || typeof target[method] !== 'function') return;
  try { target[method](...args); } catch (_) {}
}

function separator(template) {
  if (template.length && template[template.length - 1].type !== 'separator') {
    template.push({ type: 'separator' });
  }
}

function buildBrowserContextMenuTemplate({
  params = {},
  webContents,
  labels = {},
  openTab = () => {},
  copyText = () => {},
  isDev = false,
} = {}) {
  const t = { ...DEFAULT_LABELS, ...labels };
  const template = [];
  const linkUrl = String(params.linkURL || '').trim();
  const sourceUrl = String(params.srcURL || '').trim();
  const mediaType = String(params.mediaType || 'none');

  if (linkUrl) {
    template.push(
      { label: t.openLinkNewTab, click: () => openTab(linkUrl) },
      { label: t.copyLink, click: () => copyText(linkUrl) },
    );
  }

  if (sourceUrl && mediaType === 'image') {
    if (template.length) separator(template);
    template.push(
      { label: t.openImageNewTab, click: () => openTab(sourceUrl) },
      { label: t.saveImage, click: () => safeCall(webContents, 'downloadURL', sourceUrl) },
    );
  } else if (sourceUrl && (mediaType === 'video' || mediaType === 'audio')) {
    if (template.length) separator(template);
    template.push(
      { label: t.openMediaNewTab, click: () => openTab(sourceUrl) },
      { label: t.saveMedia, click: () => safeCall(webContents, 'downloadURL', sourceUrl) },
    );
  }

  const suggestions = Array.isArray(params.dictionarySuggestions)
    ? params.dictionarySuggestions.filter(Boolean).slice(0, 5)
    : [];
  if (String(params.misspelledWord || '') && suggestions.length) {
    if (template.length) separator(template);
    for (const suggestion of suggestions) {
      template.push({
        label: String(suggestion),
        click: () => safeCall(webContents, 'replaceMisspelling', String(suggestion)),
      });
    }
  }
  if (String(params.misspelledWord || '') && webContents && webContents.session) {
    if (template.length) separator(template);
    template.push({
      label: t.addToDictionary,
      click: () => safeCall(webContents.session, 'addWordToSpellCheckerDictionary', String(params.misspelledWord)),
    });
  }

  if (params.isEditable) {
    if (template.length) separator(template);
    template.push(
      { label: t.undo, enabled: Boolean(params.editFlags && params.editFlags.canUndo), click: () => safeCall(webContents, 'undo') },
      { label: t.redo, enabled: Boolean(params.editFlags && params.editFlags.canRedo), click: () => safeCall(webContents, 'redo') },
      { type: 'separator' },
      { label: t.cut, enabled: Boolean(params.editFlags && params.editFlags.canCut), click: () => safeCall(webContents, 'cut') },
      { label: t.copy, enabled: Boolean(params.editFlags && params.editFlags.canCopy), click: () => safeCall(webContents, 'copy') },
      { label: t.paste, enabled: Boolean(params.editFlags && params.editFlags.canPaste), click: () => safeCall(webContents, 'paste') },
      { label: t.selectAll, enabled: Boolean(params.editFlags && params.editFlags.canSelectAll), click: () => safeCall(webContents, 'selectAll') },
    );
  } else if (String(params.selectionText || '')) {
    if (template.length) separator(template);
    template.push({ label: t.copy, click: () => safeCall(webContents, 'copy') });
  }

  if (template.length) separator(template);
  template.push(
    {
      label: t.back,
      enabled: Boolean(webContents && typeof webContents.canGoBack === 'function' && webContents.canGoBack()),
      click: () => safeCall(webContents, 'goBack'),
    },
    {
      label: t.forward,
      enabled: Boolean(webContents && typeof webContents.canGoForward === 'function' && webContents.canGoForward()),
      click: () => safeCall(webContents, 'goForward'),
    },
    { label: t.reload, click: () => safeCall(webContents, 'reload') },
  );

  if (isDev && Number.isFinite(Number(params.x)) && Number.isFinite(Number(params.y))) {
    separator(template);
    template.push({
      label: t.inspectElement,
      click: () => safeCall(webContents, 'inspectElement', Number(params.x), Number(params.y)),
    });
  }

  return template;
}

module.exports = {
  DEFAULT_LABELS,
  buildBrowserContextMenuTemplate,
};
