'use strict';

// Supplemental DOM helpers for content that the historical top-document
// browser path cannot see. The existing browser snapshot/target scripts remain
// the first choice; these scripts run only for child frames or open shadow roots.

const BROWSER_CLEAR_DEEP_REFS_SCRIPT = `
(() => {
  const roots = [document];
  for (let index = 0; index < roots.length; index += 1) {
    const root = roots[index];
    for (const element of Array.from(root.querySelectorAll ? root.querySelectorAll('*') : [])) {
      element.removeAttribute('data-cyrene-ref');
      element.removeAttribute('data-cyrene-scroll-probe');
      if (element.shadowRoot && !roots.includes(element.shadowRoot)) roots.push(element.shadowRoot);
    }
  }
  return true;
})()
`;

const BROWSER_INSPECT_NESTED_SCRIPT = `
(function(maxArg, textArg, startArg, includeLightDomArg) {
  const maxElements = Math.max(0, Math.min(200, Number(maxArg) || 0));
  const textLimit = Math.max(20, Math.min(500, Number(textArg) || 160));
  const startIndex = Math.max(0, Number(startArg) || 0);
  const includeLightDom = includeLightDomArg === true;
  const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
  const roots = [{ root: document, shadowDepth: 0 }];
  for (let index = 0; index < roots.length; index += 1) {
    const entry = roots[index];
    for (const node of Array.from(entry.root.querySelectorAll ? entry.root.querySelectorAll('*') : [])) {
      if (node.shadowRoot && !roots.some((item) => item.root === node.shadowRoot)) {
        roots.push({ root: node.shadowRoot, shadowDepth: entry.shadowDepth + 1 });
      }
    }
  }
  const selectedRoots = includeLightDom ? roots : roots.slice(1);
  const clean = (value, limit = textLimit) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const cssEscape = (value) => {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  };
  const parentOf = (node) => node && (
    node.parentElement || (node.getRootNode && node.getRootNode().host) || null
  );
  const deepElementFromPoint = (x, y) => {
    let hit = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
    while (hit && hit.shadowRoot && typeof hit.shadowRoot.elementFromPoint === 'function') {
      const nested = hit.shadowRoot.elementFromPoint(x, y);
      if (!nested || nested === hit) break;
      hit = nested;
    }
    return hit;
  };
  const roleOf = (el, tag) => {
    const explicit = clean(el.getAttribute('role'), 60);
    if (explicit) return explicit;
    if (tag === 'a' && el.href) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const type = String(el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
      if (type === 'file') return 'file-upload';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'img') return 'img';
    if (el.isContentEditable) return 'textbox';
    return '';
  };
  const selectorFor = (el, tag, refNumber) => {
    const id = clean(el.id, 120);
    if (id) return '#' + cssEscape(id);
    const testId = clean(el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy'), 120);
    if (testId) return tag + '[data-testid="' + testId.replace(/"/g, '\\"') + '"]';
    const href = clean(el.getAttribute('href'), 180);
    if (tag === 'a' && href) return 'a[href="' + href.replace(/"/g, '\\"') + '"]';
    return '[data-cyrene-ref="' + refNumber + '"]';
  };
  const visibleRect = (el) => {
    if (el.hidden) return null;
    for (let node = el; node instanceof Element; node = parentOf(node)) {
      if (node.hidden || node.hasAttribute('inert')
          || String(node.getAttribute('aria-hidden') || '').toLowerCase() === 'true') return null;
      const style = window.getComputedStyle(node);
      if (!style || style.display === 'none' || style.visibility === 'hidden'
          || style.visibility === 'collapse' || style.contentVisibility === 'hidden'
          || Number(style.opacity) <= 0.001) return null;
    }
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    const left = Math.max(0, rect.left);
    const top = Math.max(0, rect.top);
    const right = Math.min(viewportW, rect.right);
    const bottom = Math.min(viewportH, rect.bottom);
    if (right <= left || bottom <= top) return null;
    const hit = deepElementFromPoint((left + right) / 2, (top + bottom) / 2);
    return hit && (hit === el || el.contains(hit)) ? rect : null;
  };
  const interactiveSelector = 'input,textarea,select,button,a[href],[contenteditable="true"],[role="textbox"],[role="searchbox"],[role="combobox"],[role="button"],[role="link"],[tabindex]';
  const fallbackSelector = 'summary,label,[role],img,video,section,article,div,span';
  const out = [];
  const seen = new Set();
  for (const entry of selectedRoots) {
    const candidates = [
      ...Array.from(entry.root.querySelectorAll(interactiveSelector)),
      ...Array.from(entry.root.querySelectorAll(fallbackSelector)),
    ];
    for (const el of candidates) {
      if (!(el instanceof Element) || seen.has(el)) continue;
      seen.add(el);
      const rect = visibleRect(el);
      if (!rect) continue;
      const tag = String(el.tagName || '').toLowerCase();
      const role = roleOf(el, tag);
      const disabled = el.matches(':disabled') || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
      const style = window.getComputedStyle(el);
      const interactive = !disabled && (
        ['a', 'button', 'input', 'textarea', 'select', 'summary'].includes(tag)
        || el.isContentEditable || el.tabIndex >= 0 || typeof el.onclick === 'function'
        || ['button', 'link', 'textbox', 'searchbox', 'combobox', 'checkbox', 'radio', 'switch', 'menuitem', 'tab'].includes(role)
        || (style && style.cursor === 'pointer')
      );
      const inputType = tag === 'input' ? clean(el.getAttribute('type') || 'text', 40).toLowerCase() : '';
      const text = tag === 'input' || tag === 'textarea'
        ? (inputType === 'password' ? '' : clean(el.value))
        : clean(el.innerText || el.textContent || el.getAttribute('value') || el.getAttribute('title') || el.getAttribute('alt'));
      const ariaLabel = clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('alt'));
      const placeholder = clean(el.getAttribute('placeholder'));
      const href = el.href ? String(el.href) : clean(el.getAttribute('href'), 300);
      const src = el.currentSrc || el.src || clean(el.getAttribute('src'), 300);
      const interesting = role || href || placeholder || ariaLabel || tag === 'img'
        || tag === 'input' || tag === 'textarea' || tag === 'select' || text.length >= 2;
      if (!interesting) continue;
      const refNumber = startIndex + out.length + 1;
      el.setAttribute('data-cyrene-ref', String(refNumber));
      out.push({
        ref: 'e' + refNumber,
        tag,
        role,
        visible: true,
        interactive,
        disabled,
        inputType,
        accept: tag === 'input' ? clean(el.getAttribute('accept'), 240) : '',
        multiple: tag === 'input' && el.hasAttribute('multiple'),
        text,
        ariaLabel,
        placeholder,
        href,
        src: tag === 'img' ? src : '',
        alt: tag === 'img' ? clean(el.getAttribute('alt')) : '',
        selector: selectorFor(el, tag, refNumber),
        rect: { x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) },
        shadowDepth: entry.shadowDepth,
      });
      if (out.length >= maxElements) break;
    }
    if (out.length >= maxElements) break;
  }
  return {
    ok: true,
    url: location.href,
    title: document.title || '',
    text: clean(Array.from(new Set(out.map((item) => item.text).filter(Boolean))).join(' '), 2000),
    viewport: { width: viewportW, height: viewportH, scrollX: window.scrollX || 0, scrollY: window.scrollY || 0 },
    elements: out,
  };
})
`;

const BROWSER_FIND_NESTED_TARGET_SCRIPT = `
(function(modeArg, valueArg, exactArg, visibleOnlyArg, includeLightDomArg) {
  const mode = String(modeArg || 'selector');
  const value = String(valueArg || '');
  const exact = exactArg === true;
  const visibleOnly = visibleOnlyArg !== false;
  const includeLightDom = includeLightDomArg === true;
  const norm = (input) => String(input || '').replace(/\\s+/g, ' ').trim();
  const roots = [document];
  for (let index = 0; index < roots.length; index += 1) {
    const root = roots[index];
    for (const node of Array.from(root.querySelectorAll ? root.querySelectorAll('*') : [])) {
      if (node.shadowRoot && !roots.includes(node.shadowRoot)) roots.push(node.shadowRoot);
    }
  }
  const selectedRoots = includeLightDom ? roots : roots.slice(1);
  const queryOne = (selector) => {
    for (const root of selectedRoots) {
      const found = root.querySelector ? root.querySelector(selector) : null;
      if (found) return found;
    }
    return null;
  };
  const queryAll = (selector) => selectedRoots.flatMap((root) => (
    Array.from(root.querySelectorAll ? root.querySelectorAll(selector) : [])
  ));
  const deepElementFromPoint = (x, y) => {
    let hit = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
    while (hit && hit.shadowRoot && typeof hit.shadowRoot.elementFromPoint === 'function') {
      const nested = hit.shadowRoot.elementFromPoint(x, y);
      if (!nested || nested === hit) break;
      hit = nested;
    }
    return hit;
  };
  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return !!rect && rect.width > 0 && rect.height > 0;
  };
  const labelOf = (el) => norm(
    el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title')
      || el.getAttribute('placeholder') || el.getAttribute('value')
  );
  let element = null;
  if (mode === 'ref') {
    const number = value.replace(/^e/i, '').replace(/"/g, '\\"');
    element = queryOne('[data-cyrene-ref="' + number + '"]');
  } else if (mode === 'text') {
    const needle = norm(value).toLowerCase();
    const matches = queryAll('a[href],button,input,textarea,select,[role],[tabindex],[contenteditable="true"],label,summary,div,span,section,article').filter((node) => {
      if (visibleOnly && !isVisible(node)) return false;
      const haystack = labelOf(node).toLowerCase();
      return exact ? haystack === needle : haystack.includes(needle);
    });
    const exactMatches = exact ? matches : matches.filter((node) => labelOf(node).toLowerCase() === needle);
    const unique = Array.from(new Set(exactMatches.length ? exactMatches : matches));
    if (unique.length !== 1) {
      return { ok: false, code: unique.length ? 'AMBIGUOUS_TEXT_TARGET' : 'TARGET_NOT_FOUND', matchCount: unique.length };
    }
    element = unique[0];
  } else {
    element = queryOne(value);
  }
  if (!element) return { ok: false, code: 'TARGET_NOT_FOUND', error: 'nf' };
  if (visibleOnly && !isVisible(element)) return { ok: false, code: 'TARGET_NOT_VISIBLE', error: 'not visible' };
  if (element.disabled === true || element.getAttribute('aria-disabled') === 'true') {
    return { ok: false, code: 'TARGET_DISABLED', error: 'disabled' };
  }
  element.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' });
  const rect = element.getBoundingClientRect();
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  const left = Math.max(0, rect.left);
  const top = Math.max(0, rect.top);
  const right = Math.min(viewportWidth, rect.right);
  const bottom = Math.min(viewportHeight, rect.bottom);
  if (right <= left || bottom <= top) return { ok: false, code: 'TARGET_NOT_VISIBLE', error: 'outside viewport' };
  const x = Math.round((left + right) / 2);
  const y = Math.round((top + bottom) / 2);
  const hit = deepElementFromPoint(x, y);
  return {
    ok: true,
    x,
    y,
    hitMatches: !!hit && (hit === element || element.contains(hit)),
    box: { x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) },
    tag: String(element.tagName || '').toLowerCase(),
    inputType: String(element.getAttribute && element.getAttribute('type') || '').toLowerCase(),
    accept: String(element.getAttribute && element.getAttribute('accept') || ''),
    multiple: !!(element.hasAttribute && element.hasAttribute('multiple')),
  };
})
`;

function browserFrameElementGeometryScript(childIndex) {
  return `(() => {
    const targetWindow = window.frames[${JSON.stringify(Number(childIndex))}];
    if (!targetWindow) return { ok: false };
    const roots = [document];
    const frames = [];
    for (let index = 0; index < roots.length; index += 1) {
      const root = roots[index];
      for (const node of Array.from(root.querySelectorAll ? root.querySelectorAll('*') : [])) {
        if ((node.tagName === 'IFRAME' || node.tagName === 'FRAME') && node.contentWindow === targetWindow) frames.push(node);
        if (node.shadowRoot && !roots.includes(node.shadowRoot)) roots.push(node.shadowRoot);
      }
    }
    const element = frames[0];
    if (!element) return { ok: false };
    const rect = element.getBoundingClientRect();
    const offsetWidth = Math.max(1, Number(element.offsetWidth) || Number(rect.width) || 1);
    const offsetHeight = Math.max(1, Number(element.offsetHeight) || Number(rect.height) || 1);
    const scaleX = Number(rect.width) / offsetWidth;
    const scaleY = Number(rect.height) / offsetHeight;
    return {
      ok: true,
      x: Number(rect.left) + (Number(element.clientLeft) || 0) * scaleX,
      y: Number(rect.top) + (Number(element.clientTop) || 0) * scaleY,
      scaleX,
      scaleY,
    };
  })()`;
}

const BROWSER_DEEP_RESOLVE_ELEMENT_SCRIPT = `
(function(modeArg, valueArg, includeLightDomArg) {
  const mode = String(modeArg || 'selector');
  const value = String(valueArg || '');
  const roots = [document];
  for (let index = 0; index < roots.length; index += 1) {
    const root = roots[index];
    for (const node of Array.from(root.querySelectorAll ? root.querySelectorAll('*') : [])) {
      if (node.shadowRoot && !roots.includes(node.shadowRoot)) roots.push(node.shadowRoot);
    }
  }
  const selectedRoots = includeLightDomArg === true ? roots : roots.slice(1);
  const selector = mode === 'ref'
    ? '[data-cyrene-ref="' + value.replace(/^e/i, '').replace(/"/g, '\\"') + '"]'
    : value;
  for (const root of selectedRoots) {
    const found = root.querySelector ? root.querySelector(selector) : null;
    if (found) return found;
  }
  return null;
})
`;

module.exports = {
  BROWSER_CLEAR_DEEP_REFS_SCRIPT,
  BROWSER_DEEP_RESOLVE_ELEMENT_SCRIPT,
  BROWSER_FIND_NESTED_TARGET_SCRIPT,
  BROWSER_INSPECT_NESTED_SCRIPT,
  browserFrameElementGeometryScript,
};
