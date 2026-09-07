'use strict';

// Runs inside the inspected page. Keep this function self-contained because
// main.js serializes it into executeJavaScript and browser-input.js embeds it
// into its trusted-input preparation script.
const BROWSER_FIND_TARGET_SCRIPT = `
(function(modeArg, valueArg, exactArg, visibleOnlyArg) {
  const mode = String(modeArg || 'selector');
  const value = String(valueArg || '');
  const exact = exactArg === true;
  const visibleOnly = visibleOnlyArg !== false;
  const norm = (v) => String(v || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return !!r && r.width > 0 && r.height > 0;
  };
  const labelOf = (el) => norm(
    el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title')
      || el.getAttribute('placeholder') || el.getAttribute('value')
  );
  const summaryOf = (el) => ({
    tag: String(el.tagName || '').toLowerCase(),
    role: norm(el.getAttribute && el.getAttribute('role')).slice(0, 60),
    text: labelOf(el).slice(0, 160),
    href: String((el.href || (el.getAttribute && el.getAttribute('href'))) || '').slice(0, 300),
  });
  const matchingPool = (nodes, needle) => {
    const matches = nodes.filter((node) => {
      if (visibleOnly && !isVisible(node)) return false;
      const hay = labelOf(node).toLowerCase();
      return exact ? hay === needle : hay.includes(needle);
    });
    if (exact) return matches;
    const exactMatches = matches.filter((node) => labelOf(node).toLowerCase() === needle);
    return exactMatches.length ? exactMatches : matches;
  };
  let el = null;
  if (mode === 'ref') {
    const n = value.replace(/^e/i, '');
    el = document.querySelector('[data-cyrene-ref="' + n.replace(/"/g, '\\\\"') + '"]');
  } else if (mode === 'text') {
    const needle = norm(value).toLowerCase();
    if (!needle) return { ok: false, error: 'empty text target', code: 'EMPTY_TEXT_TARGET' };
    const actionableSelector = 'a[href],button,input,textarea,select,[role="button"],[role="link"],[role="menuitem"],[role="tab"],[role="option"],[tabindex],[contenteditable="true"],[onclick],label,summary';
    let matches = matchingPool(Array.from(document.querySelectorAll(actionableSelector)), needle);
    if (!matches.length) {
      const fallback = matchingPool(
        Array.from(document.querySelectorAll('div,span,section,article')).filter((node) => {
          // Large ancestors collect the text of many unrelated controls. Only
          // accept leaf-like fallback targets whose children do not also match.
          return !Array.from(node.children || []).some((child) => {
            const hay = labelOf(child).toLowerCase();
            return exact ? hay === needle : hay.includes(needle);
          });
        }),
        needle,
      );
      matches = fallback;
    }
    const unique = Array.from(new Set(matches));
    if (unique.length !== 1) {
      return {
        ok: false,
        error: unique.length ? 'ambiguous text target' : 'nf',
        code: unique.length ? 'AMBIGUOUS_TEXT_TARGET' : 'TARGET_NOT_FOUND',
        matchCount: unique.length,
        matches: unique.slice(0, 6).map(summaryOf),
      };
    }
    el = unique[0];
  } else {
    el = document.querySelector(value);
  }
  if (!el) return { ok: false, error: 'nf', code: 'TARGET_NOT_FOUND' };
  if (visibleOnly && !isVisible(el)) return { ok: false, error: 'not visible', code: 'TARGET_NOT_VISIBLE' };
  if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') {
    return { ok: false, error: 'disabled', code: 'TARGET_DISABLED' };
  }
  el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  if (!r || r.width <= 0 || r.height <= 0) return { ok: false, error: 'not visible', code: 'TARGET_NOT_VISIBLE' };
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  const visibleLeft = Math.max(0, r.left);
  const visibleTop = Math.max(0, r.top);
  const visibleRight = Math.min(viewportWidth, r.right);
  const visibleBottom = Math.min(viewportHeight, r.bottom);
  if (visibleRight <= visibleLeft || visibleBottom <= visibleTop) {
    return { ok: false, error: 'outside viewport', code: 'TARGET_NOT_VISIBLE' };
  }
  const x = Math.round(visibleLeft + (visibleRight - visibleLeft) / 2);
  const y = Math.round(visibleTop + (visibleBottom - visibleTop) / 2);
  const hit = document.elementFromPoint ? document.elementFromPoint(x, y) : el;
  const hitMatches = !!hit && (hit === el || el.contains(hit));
  return {
    ok: true,
    x,
    y,
    hitMatches,
    blockedBy: hitMatches || !hit ? null : summaryOf(hit),
    target: summaryOf(el),
    box: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
    tag: String(el.tagName || '').toLowerCase(),
    inputType: String(el.getAttribute && el.getAttribute('type') || '').toLowerCase(),
    accept: String(el.getAttribute && el.getAttribute('accept') || ''),
    multiple: !!(el.hasAttribute && el.hasAttribute('multiple')),
  };
})
`;

module.exports = { BROWSER_FIND_TARGET_SCRIPT };
