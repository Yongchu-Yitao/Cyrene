function wbcNormalizeReasoningText(text) {
  return String(text || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map(function (line) { return line.trimEnd(); })
    .filter(function (line) { return !!line.trim(); })
    .join("\n")
    .trim();
}

function wbcTraceTimelineItems(entries, reasoning) {
  var traceEntries = Array.isArray(entries) ? entries : [];
  var rawReasoning = String(reasoning || "");
  var anchored = [];
  var trailing = [];
  traceEntries.forEach(function (entry, index) {
    // The phase-one row only summarizes the reasoning text and repeats that
    // same content in its preview. Keep it for the collapsed status, but omit
    // it from the expanded timeline where the full reasoning is already shown.
    if (entry && entry.kind === "phase1") return;
    var offset = Number(entry && entry.reasoningOffset);
    if (Number.isFinite(offset) && offset >= 0) anchored.push({ entry: entry, index: index, offset: offset });
    else trailing.push({ entry: entry, index: index });
  });
  anchored.sort(function (left, right) {
    return left.offset === right.offset ? left.index - right.index : left.offset - right.offset;
  });
  var items = [];
  var cursor = 0;
  anchored.forEach(function (anchor) {
    var offset = Math.max(cursor, Math.min(rawReasoning.length, anchor.offset));
    var reasoningText = wbcNormalizeReasoningText(rawReasoning.slice(cursor, offset));
    if (reasoningText) items.push({ kind: "reasoning", text: reasoningText });
    items.push({ kind: "trace", entry: anchor.entry, index: anchor.index });
    cursor = offset;
  });
  var remainingReasoning = wbcNormalizeReasoningText(rawReasoning.slice(cursor));
  if (remainingReasoning) items.push({ kind: "reasoning", text: remainingReasoning });
  trailing.forEach(function (item) {
    items.push({ kind: "trace", entry: item.entry, index: item.index });
  });
  return items;
}

export { wbcTraceTimelineItems };
