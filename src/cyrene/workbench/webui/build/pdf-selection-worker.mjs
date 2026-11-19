// PDF.js normally represents a justified line as a single browser text run.
// Keep its exact inter-word positions for horizontal Latin selection, while
// preserving extraction, bidi ordering and the original page operator list.
function appendSelectionRun(items, run, strings, gaps) {
  const origin = run.transform
  if (!gaps.length || run.dir !== 'ltr' || origin[0] <= 0 || origin[1] || origin[2] ||
      !/^[\x20-\x7e]+$/.test(run.str) || run.str !== strings.join('')) {
    items.push(run)
    return
  }
  let start = origin
  const right = origin[4] + run.width
  for (const gap of gaps) {
    if (gap.before[5] !== origin[5] || gap.after[5] !== origin[5] ||
        gap.before[4] < start[4] || gap.after[4] < gap.before[4] || gap.after[4] > right) {
      items.push(run)
      return
    }
    start = gap.after
  }
  start = origin
  let index = 0
  for (const gap of gaps) {
    items.push({ ...run, str: strings.slice(index, gap.index).join(''), transform: start,
      width: gap.before[4] - start[4], hasEOL: false })
    items.push({ ...run, str: ' ', transform: gap.before,
      width: gap.after[4] - gap.before[4], hasEOL: false })
    start = gap.after
    index = gap.index + 1
  }
  items.push({ ...run, str: strings.slice(index).join(''), transform: start, width: right - start[4] })
}

export function customizeSelectionWorker(source) {
  // Match readable upstream source and fail the build if an upgrade changes
  // these hooks, rather than silently shipping a partly adjusted worker.
  function replaceOnce(anchor, replacement) {
    if (!source.includes(anchor) || source.indexOf(anchor) !== source.lastIndexOf(anchor)) {
      throw new Error('PDF.js selection integration changed: ' + anchor)
    }
    source = source.replace(anchor, () => replacement)
  }
  replaceOnce('function ensureTextContentItem() {', `
    const forSelection = includeMarkedContent && disableNormalization && !intersector;
    const selectionGaps = [];
    ${appendSelectionRun.toString()}
    function ensureTextContentItem() {`)
  replaceOnce('fontFamily: font.fallbackName,', `fontFamily: forSelection && !font.missingFile &&
    !font.isType3Font && !font.vertical && !font.composite
      ? loadedName + ", " + font.fallbackName : font.fallbackName,`)
  replaceOnce('textContentItem.str.push(" ");', `
    if (forSelection && !textContentItem.vertical) {
      selectionGaps.push({ index: textContentItem.str.length, before: transf, after: getCurrentTextTransform() });
    }
    textContentItem.str.push(" ");`)
  replaceOnce('textContent.items.push(runBidiTransform(textContentItem));', `
    appendSelectionRun(textContent.items, runBidiTransform(textContentItem), textContentItem.str, selectionGaps);
    selectionGaps.length = 0;`)
  return source
}
