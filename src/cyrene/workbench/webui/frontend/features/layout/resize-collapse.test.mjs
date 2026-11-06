import assert from "node:assert/strict";
import test from "node:test";
import { createResizeCollapseGuard } from "./resize-collapse.mjs";
import { resizeClock } from "./test-helpers/resize-clock.mjs";

function gesture() {
  const clock = resizeClock();
  const update = createResizeCollapseGuard(clock);
  return { move: size => update(size, 300, 800), clock, dispose: update.dispose };
}

test("fast travel during the initial hold never closes", () => {
  for (const direction of [-1, 1]) {
    const { move, clock } = gesture();
    const floor = direction < 0 ? 300 : 800;
    for (let i = 0; i < 20; i++) {
      clock.advance(16);
      assert.equal(move(floor + direction * (100 + i * 10)), "");
    }
  }
});

test("a real pause with NO pointer events enables the first resumed move on either edge", () => {
  for (const [floor, direction, edge] of [[300, -1, "minimum"], [800, 1, "maximum"]]) {
    const { move, clock } = gesture();
    assert.equal(move(floor), "");
    clock.advance();
    assert.equal(move(floor + direction * 48), edge);
  }
});

test("jitter is tolerated, but pre-pause travel is discarded", () => {
  const { move, clock } = gesture();
  move(300);
  clock.advance(100);
  move(200);
  clock.advance(100);
  move(198);
  clock.advance(300);
  assert.equal(move(151), "");
  assert.equal(move(150), "minimum");
});

test("returning into range, reversing, switching edges, and new gestures reset protection", () => {
  for (const retreat of [280, 400, 800]) {
    const { move, clock } = gesture();
    move(250);
    clock.advance();
    assert.equal(move(retreat), "");
    clock.advance(16);
    assert.equal(move(200), "");
  }
  const { move, clock } = gesture();
  clock.advance(1000);
  assert.equal(move(100), "");
});

test("elapsed wall time without running the dwell timer cannot unlock closing", () => {
  const { move, clock } = gesture();
  move(300);
  clock.advance(500, true);
  assert.equal(move(150), "");
  clock.advance();
  assert.equal(move(102), "minimum");
});

test("a brief pause is insufficient and reversing cancels armed closing", () => {
  const { move, clock } = gesture();
  move(300);
  clock.advance(200);
  assert.equal(move(200), "");
  clock.advance();
  assert.equal(move(170), "");
  assert.equal(move(180), "");
  assert.equal(move(100), "");
});

test("ending a gesture cancels pending timers and disables future closing", () => {
  for (const armed of [false, true]) {
    const { move, clock, dispose } = gesture();
    move(300);
    if (armed) clock.advance();
    dispose();
    assert.equal(clock.pending(), 0);
    clock.advance(1000);
    assert.equal(move(200), "");
  }
});

test("native timer unlocks during inactivity without requiring a synthetic stationary move", async () => {
  const move = createResizeCollapseGuard();
  try {
    assert.equal(move(300, 300, 800), "");
    await new Promise(resolve => setTimeout(resolve, 450));
    assert.equal(move(252, 300, 800), "minimum");
  } finally {
    move.dispose();
  }
});


test("continuous dragging can close after the fixed hold without stopping the pointer", () => {
  const { move, clock } = gesture();
  move(300);
  for (let i = 1; i <= 19; i++) {
    clock.advance(20);
    assert.equal(move(300 - i * 5), "");
  }
  clock.advance(20);
  assert.equal(move(200), "");
  assert.equal(move(158), "");
  assert.equal(move(157), "minimum");
});
