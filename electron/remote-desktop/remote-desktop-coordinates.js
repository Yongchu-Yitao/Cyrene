'use strict';

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function displayDipPoint(display, event) {
  const bounds = display && display.bounds || {};
  const x = Math.max(0, Math.min(1, finiteNumber(event && event.x_normalized)));
  const y = Math.max(0, Math.min(1, finiteNumber(event && event.y_normalized)));
  return {
    x: Math.round(finiteNumber(bounds.x) + x * Math.max(1, finiteNumber(bounds.width, 1) - 1)),
    y: Math.round(finiteNumber(bounds.y) + y * Math.max(1, finiteNumber(bounds.height, 1) - 1)),
  };
}

function inputPoint(display, event, options = {}) {
  const point = displayDipPoint(display, event);
  if (options.platform !== 'win32' || typeof options.screenApi?.dipToScreenPoint !== 'function') {
    return point;
  }
  const physical = options.screenApi.dipToScreenPoint(point);
  return {
    x: Math.round(finiteNumber(physical && physical.x, point.x)),
    y: Math.round(finiteNumber(physical && physical.y, point.y)),
  };
}

function inputBounds(display, options = {}) {
  const raw = display && display.bounds || {};
  const bounds = {
    x: Math.round(finiteNumber(raw.x)),
    y: Math.round(finiteNumber(raw.y)),
    width: Math.max(1, Math.round(finiteNumber(raw.width, 1))),
    height: Math.max(1, Math.round(finiteNumber(raw.height, 1))),
  };
  if (options.platform !== 'win32' || typeof options.screenApi?.dipToScreenRect !== 'function') {
    return bounds;
  }
  const physical = options.screenApi.dipToScreenRect(null, bounds);
  return {
    x: Math.round(finiteNumber(physical && physical.x, bounds.x)),
    y: Math.round(finiteNumber(physical && physical.y, bounds.y)),
    width: Math.max(1, Math.round(finiteNumber(physical && physical.width, bounds.width))),
    height: Math.max(1, Math.round(finiteNumber(physical && physical.height, bounds.height))),
  };
}

module.exports = {
  displayDipPoint,
  inputBounds,
  inputPoint,
};
