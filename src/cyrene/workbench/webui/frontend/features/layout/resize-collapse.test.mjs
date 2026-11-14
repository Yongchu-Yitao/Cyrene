import assert from "node:assert/strict";
import test from "node:test";
import { createResizeCollapseGuard } from "./resize-collapse.mjs";

test("both edges close after 32px overshoot without a dwell or a boundary event", () => {
  for (const [boundary, direction, edge] of [[300, -1, "minimum"], [800, 1, "maximum"]]) {
    const move = createResizeCollapseGuard();
    assert.equal(move(boundary, 300, 800), "");
    assert.equal(move(boundary + direction * 31, 300, 800), "");
    assert.equal(move(boundary + direction * 32, 300, 800), edge);
    assert.equal(createResizeCollapseGuard()(boundary + direction * 150, 300, 800), edge);
  }
});

test("normal sizing, boundary jitter and stationary holds cannot close", () => {
  const move = createResizeCollapseGuard();
  for (const value of [500, 300, 301, 299, 280, 280, 800, 810, 831, 800]) {
    assert.equal(move(value, 300, 800), "");
  }
});

test("reversing a collapsed preview requires fresh travel before closing again", () => {
  for (const [size, direction, edge] of [[200, -1, "minimum"], [900, 1, "maximum"]]) {
    const move = createResizeCollapseGuard();
    assert.equal(move(size, 300, 800), edge);
    move.reset();
    const reversed = size - direction * 24;
    assert.equal(move(reversed, 300, 800), "");
    assert.equal(move(reversed + direction * 31, 300, 800), "");
    assert.equal(move(reversed + direction * 32, 300, 800), edge);
  }
});

test("returning into range resets the reference to the actual boundary", () => {
  const move = createResizeCollapseGuard();
  move(200, 300, 800);
  move.reset();
  assert.equal(move(500, 300, 800), "");
  assert.equal(move(268, 300, 800), "minimum");
  assert.equal(move(832, 300, 800), "maximum");
});

test("equal bounds support closing in either direction, and disposal disables closing", () => {
  for (const [value, edge] of [[268, "minimum"], [332, "maximum"]]) {
    const move = createResizeCollapseGuard();
    assert.equal(move(300, 300, 300), "");
    assert.equal(move(value, 300, 300), edge);
    move.dispose();
    move.reset();
    assert.equal(move(value, 300, 300), "");
  }
});
