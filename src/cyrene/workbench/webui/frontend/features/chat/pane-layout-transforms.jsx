import { wbcDefaultPaneLayout, wbcPaneCardLocation } from "./drag-layout.jsx"

function wbcPromotePaneSourceLayout(layout, source, card) {
  var sourceColumn = (layout[source.side] || []).slice();
  var oppositeSide = source.side === "left" ? "right" : "left";
  // A lone vertical stack still has an unused outer column. Keep both stacked
  // panes and use that column instead of dropping the source pane's sibling.
  if (sourceColumn.length === 2 && !(layout[oppositeSide] || []).length) {
    return {
      left: sourceColumn,
      right: [card],
      leftRatio: source.side === "right" ? layout.rightRatio : layout.leftRatio,
      rightRatio: layout.rightRatio,
    };
  }
  return {
    left: [source.card],
    right: [card],
    leftRatio: layout.leftRatio,
    rightRatio: layout.rightRatio,
  };
}


function wbcMovePaneCardLayout(layout, cardId, options) {
  var opts = options || {};
  var targetSide = String(opts.side || "");
  if (targetSide !== "left" && targetSide !== "right") {
    throw new Error("pane side must be left or right");
  }
  var source = wbcPaneCardLocation(layout, cardId);
  if (!source) throw new Error("pane card is not available");
  var position = String(opts.position || "");
  if (!position) position = source.side === targetSide && source.index === 0 ? "top" : "bottom";
  if (position !== "top" && position !== "bottom") {
    throw new Error("pane position must be top or bottom");
  }
  if (source.side !== targetSide && (layout[targetSide] || []).length >= 2) {
    throw new Error("target pane column is full");
  }
  var next = {
    left: layout.left.slice(), right: layout.right.slice(),
    leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
  };
  var moving = next[source.side].splice(source.index, 1)[0];
  var target = next[targetSide];
  if (position === "top") target.unshift(moving);
  else target.push(moving);
  return next;
}


function wbcSwapPaneCardsLayout(layout, firstCardId, secondCardId) {
  var first = wbcPaneCardLocation(layout, firstCardId);
  var second = wbcPaneCardLocation(layout, secondCardId);
  if (!first || !second) throw new Error("both pane cards must be available");
  if (String(firstCardId || "") === String(secondCardId || "")) return layout;
  var next = {
    left: layout.left.slice(), right: layout.right.slice(),
    leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
  };
  next[first.side][first.index] = second.card;
  next[second.side][second.index] = first.card;
  return next;
}


export function openPaneLayout(layout, card, opts) {
    var source = opts.sourceCardId ? wbcPaneCardLocation(layout, opts.sourceCardId) : null;
    var targetSide = opts.side === "left" || opts.side === "right"
      ? opts.side : (source && source.side === "right" ? "left" : "right");
    var next = {
      left: layout.left.slice(), right: layout.right.slice(),
      leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
    };
    if (opts.replaceWorkspace) { next.left = [card]; next.right = []; return next; }
    if (opts.promoteSourceLeft && source) return wbcPromotePaneSourceLayout(layout, source, card);
    next[targetSide] = [card];
    return next;
}


export function closePaneLayout(layout, cardId, ownerChatId, location) {
  var remaining = layout.left.concat(layout.right).filter(function (card) { return String(card.id) !== String(cardId); });
  var nextChat = location.card.kind === "chat" && String(location.card.payload || "") === ownerChatId
    ? remaining.find(function (card) { return card.kind === "chat"; }) : null;
  var next = {
    left: layout.left.filter(function (card) { return String(card.id) !== String(cardId); }),
    right: layout.right.filter(function (card) { return String(card.id) !== String(cardId); }),
    leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
  };
  if (!next.left.length && next.right.length) { next.left = next.right; next.right = []; }
  if (!next.left.length && !next.right.length) next = wbcDefaultPaneLayout(ownerChatId);
  return { next: next, nextChat: nextChat };
}


export function flipPaneLayout(layout, cardId) {
    var location = wbcPaneCardLocation(layout, cardId);
    if (!location) return layout;
    var next = {
      left: layout.left.slice(), right: layout.right.slice(),
      leftRatio: layout.leftRatio, rightRatio: layout.rightRatio,
    };
    if (next[location.side].length === 2) next[location.side].reverse();
    else { var left = next.left; next.left = next.right; next.right = left; }
    return next;
}

export { wbcPromotePaneSourceLayout, wbcMovePaneCardLayout, wbcSwapPaneCardsLayout };
