var WBC_LIVE_FADE_MAX_CHARACTERS = 2048;
var WBC_LIVE_FADE_DURATION_MS = 520;

function wbcClearStreamingFades(body) {
  if (!body || typeof document === "undefined") return;
  Array.from(body.querySelectorAll(".wbc-stream-fade")).forEach(function (existingFade) {
    var parent = existingFade.parentNode;
    if (!parent) return;
    while (existingFade.firstChild) parent.insertBefore(existingFade.firstChild, existingFade);
    parent.removeChild(existingFade);
    parent.normalize();
  });
}

// Preserve each arriving batch's clock across Markdown DOM replacements.
// Negative animation delays resume the same fade instead of restarting it.
function wbcStreamingFadeRanges(ranges, length, addedCharacterCount, now) {
  var earliest = Math.max(0, length - WBC_LIVE_FADE_MAX_CHARACTERS);
  var pending = addedCharacterCount < 0 ? [] : (ranges || []).filter(function (range) {
    return now - range.at < WBC_LIVE_FADE_DURATION_MS && range.end > earliest && range.end <= length;
  });
  if (addedCharacterCount > 0) pending.push({
    start: Math.max(earliest, length - addedCharacterCount), end: length, at: now,
  });
  return pending;
}

function wbcFadeInStreamingTail(body, addedCharacterCount, state) {
  if (!body || typeof document === "undefined") return;
  var current = state || { ranges: [] };
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    current.ranges = [];
    return;
  }
  var now = performance.now();
  var length = String(body.textContent || "").length;
  current.ranges = wbcStreamingFadeRanges(current.ranges, length, addedCharacterCount, now);
  if (!current.ranges.length) return;
  var earliest = Math.max(0, length - WBC_LIVE_FADE_MAX_CHARACTERS, current.ranges[0].start);
  var walker = document.createTreeWalker(body, 4);
  var textNodes = [];
  var cursor = length;
  var node = walker.lastChild();
  // Include whitespace in offsets so inline markup and code retain their exact
  // text. Collect before wrapping to avoid revisiting the inserted spans.
  while (node && cursor > earliest) {
    var value = String(node.nodeValue || "");
    textNodes.push({ node: node, start: cursor - value.length, end: cursor });
    cursor -= value.length;
    node = walker.previousNode();
  }
  textNodes.forEach(function (item) {
    for (var index = current.ranges.length - 1; index >= 0; index -= 1) {
      var range = current.ranges[index];
      var start = Math.max(item.start, range.start, earliest);
      var end = Math.min(item.end, range.end);
      if (end <= start) continue;
      var textNode = item.node;
      var fragment = textNode.splitText(start - item.start);
      fragment.splitText(end - start);
      var fade = document.createElement("span");
      fade.className = "wbc-stream-fade";
      fade.style.animationDuration = WBC_LIVE_FADE_DURATION_MS + "ms";
      fade.style.animationDelay = -Math.max(0, now - range.at) + "ms";
      fragment.parentNode.insertBefore(fade, fragment);
      fade.appendChild(fragment);
    }
  });
}

export { wbcClearStreamingFades, wbcFadeInStreamingTail, wbcStreamingFadeRanges }
