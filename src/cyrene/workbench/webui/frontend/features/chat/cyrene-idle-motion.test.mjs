import test from 'node:test';
import assert from 'node:assert/strict';
import { createCyreneIdleMotion } from '../shell/cyrene-idle-motion.mjs';
import { createCyreneMotion, CYRENE_STATES } from '../shell/cyrene-motion-model.mjs';

const advance = (motion, seconds) => { for (let i = 0; i < seconds * 120; i++) motion.step(1 / 120); };
const create = () => createCyreneMotion({ random: () => .5, idleRandom: () => .5 });

test('all four idle styles look first, turn later, and retain distinct performances', () => {
  const performances = [];
  for (const style of ['snappy', 'soft', 'orbit', 'playful']) {
    const idle = createCyreneIdleMotion({ random: () => .5 });
    idle.setTargets({ input: { x: 1, y: 0 } });
    idle.noticeInput();
    const first = idle.step(1 / 120, style, 0);
    assert.equal(first.ex, 2.6);
    assert(first.r < 0, 'body still faces the previous direction');
    let pose;
    for (let i = 1; i <= 60; i++) pose = idle.step(1 / 120, style, i / 120);
    assert(pose.r > 0, 'body follows the eyes');
    performances.push(JSON.stringify(pose));
  }
  assert.equal(new Set(performances).size, 4);
});

test('input glances keep pose and velocity, look away, and do not stare during continuous typing', () => {
  const motion = create(); advance(motion, .4);
  const pose = { ...motion.pose }, velocity = { ...motion.velocity };
  motion.setAttentionTargets({ input: { x: 1, y: 0 } }); motion.noticeInput();
  assert.deepEqual(motion.pose, pose); assert.deepEqual(motion.velocity, velocity);
  advance(motion, .4); assert.equal(motion.attention, 'input'); assert(motion.pose.ex > 2);
  const seen = new Set();
  for (let i = 0; i < 4 * 120; i++) { motion.noticeInput(); motion.step(1 / 120); seen.add(motion.attention); }
  assert(seen.has('away')); assert([...seen].some(value => ['rest', 'wander'].includes(value)));
});

test('missing targets cause a smooth look away; playful idle takes one extra peek', () => {
  const idle = createCyreneIdleMotion({ random: () => .5 });
  idle.setTargets({ input: { x: 1, y: -.2 } }); idle.noticeInput();
  const seen = new Set();
  for (let i = 0; i < 3 * 120; i++) { idle.step(1 / 120, 'playful', i / 120); seen.add(idle.attention); }
  assert(seen.has('input')); assert(seen.has('away')); assert(seen.has('peek')); assert(seen.has('rest'));
  idle.setTargets({ pointer: { x: -1, y: 0 } });
  for (let i = 0; i < 10 * 120 && idle.attention !== 'pointer'; i++) idle.step(1 / 120, 'snappy', i / 120);
  assert.equal(idle.attention, 'pointer');
  idle.clearTargets(); idle.step(1 / 120, 'snappy', 10); assert.equal(idle.attention, 'away');
});

test('random idle style changes never reset the eyes, ring, body, or velocity', () => {
  const motion = create(), styles = [motion.style];
  for (let i = 0; i < 34 * 120; i++) {
    const previous = { ...motion.pose }, previousVelocity = { ...motion.velocity }, style = motion.style;
    motion.step(1 / 120);
    if (style !== motion.style) {
      styles.push(motion.style);
      assert(Math.abs(motion.pose.x - previous.x) < .2);
      assert(Math.abs(motion.pose.ex - previous.ex) < .4);
      assert(Math.abs(motion.pose.angle - previous.angle) < 3);
      assert(Math.abs(motion.velocity.ex - previousVelocity.ex) < 25);
    }
    for (const value of Object.values(motion.pose)) assert(Number.isFinite(value));
  }
  assert.equal(new Set(styles.slice(0, 4)).size, 4);
  for (let i = 1; i < styles.length; i++) assert.notEqual(styles[i], styles[i - 1]);
});

test('attention cannot influence any working state or reduced motion', () => {
  for (const state of CYRENE_STATES.filter(value => value !== 'idle')) {
    const a = create(), b = create(); a.setState(state); b.setState(state);
    for (let i = 0; i < 120; i++) {
      a.setAttentionTargets({ input: { x: 1, y: 1 }, pointer: { x: -1, y: -1 } }); a.noticeInput();
      a.step(1 / 120); b.step(1 / 120);
    }
    assert.deepEqual(a.pose, b.pose, state); assert.equal(a.style, b.style);
  }
  const motion = create(); motion.setReduced(true); const pose = { ...motion.pose };
  motion.setAttentionTargets({ input: { x: 1, y: 1 } }); motion.noticeInput(); advance(motion, 10);
  assert.deepEqual(motion.pose, pose);
});
