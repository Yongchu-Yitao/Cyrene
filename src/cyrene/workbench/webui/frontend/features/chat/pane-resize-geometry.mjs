export function paneRowGeometry(ratio) {
  var safeRatio = Math.max(0.2, Math.min(0.8, Number(ratio) || 0.5));
  // Grid gaps do not participate in fr sizing. Position the separator at the
  // exact centre of the 12px gap instead of at a percentage of the full
  // column, which drifts into one of the cards as the ratio changes.
  var seamOffset = 6 - (safeRatio * 12);
  var seamTop = "calc(" + (safeRatio * 100) + "% "
    + (seamOffset < 0 ? "- " : "+ ") + Math.abs(seamOffset) + "px)";
  return { safeRatio, seamTop };
}

export function paneColumnBounds(layout) {
    var rect = layout.getBoundingClientRect();
    // 24px outer padding + 12px card gap. Both tracks receive the exact same
    // 380px floor; on compact windows that floor shrinks symmetrically.
    var trackWidth = Math.max(0, rect.width - 36);
    var minimum = Math.min(380, trackWidth / 2);
    return {
      minimum: minimum,
      maximum: Math.max(minimum, trackWidth - minimum),
    };
  }
