import test from 'node:test';
import assert from 'node:assert/strict';
import { createCyreneMotion, CYRENE_STATES } from '../shell/cyrene-motion-model.mjs';

const create = (random = .64) => createCyreneMotion({ random: () => random, idleRandom: () => .35 });
function advance(motion, seconds) {
  const steps = Math.round(seconds * 120);
  for (let i = 0; i < steps; i++) motion.step(seconds / steps);
}
function wake(motion) { motion.setSleeping(true, true); motion.setSleeping(false); }

test('caught napping leads with wide eyes, recoils, lands, checks both sides and blinks', () => {
  for (const random of [0, .3, .64, .99]) {
    const motion = create(random); wake(motion);
    assert.equal(motion.waking, true);
    advance(motion, .2);
    assert(motion.pose.y < -4 && motion.pose.r > 10, 'startled jump and backward tilt');
    assert(motion.pose.blink > 1.3 && motion.pose.eyeWidth > 1.2, 'eyes widen');
    assert.equal(motion.pose.sleep, 0);
    advance(motion, .26);
    assert(motion.pose.y > 1 && motion.pose.sy < .92, 'squash on landing');
    advance(motion, .34);
    assert(motion.pose.ex < -2, 'check left');
    advance(motion, .2);
    assert(motion.pose.ex > 2, 'check right');
    advance(motion, .16);
    assert(motion.pose.blink < .1, 'blink before pretending to be awake');
    advance(motion, .14);
    assert.equal(motion.pose.ex, 0);
    assert.equal(motion.pose.eyeWidth, 1);
    advance(motion, .51);
    assert.equal(motion.waking, false);
    assert.equal(motion.sleeping, false);
    assert.notEqual(motion.attention, null);
  }
});

test('wake starts at the rendered pose even when sleep entrance is interrupted', () => {
  const motion = create(); advance(motion, .4);
  motion.setSleeping(true); motion.step(.22);
  const pose = { ...motion.pose }, velocity = { ...motion.velocity };
  motion.setSleeping(false);
  assert.deepEqual(motion.pose, pose);
  assert.deepEqual(motion.velocity, velocity);
  motion.step(.00001);
  for (const key of Object.keys(pose)) assert(Math.abs(motion.pose[key] - pose[key]) < .001, key);
  advance(motion, 2);
  assert.equal(motion.waking, false);
});

test('pointer, typing and repeated wake events cannot restart or override the performance', () => {
  const a = create(), b = create(); wake(a); wake(b);
  for (let i = 0; i < 180; i++) {
    a.setSleeping(false);
    a.setAttentionTargets({ pointer: { x: 1, y: 1 }, input: { x: -1, y: -1 } });
    a.noticeInput(); a.setHover(true); a.press(); a.release();
    a.step(1 / 120); b.step(1 / 120);
    assert.deepEqual(a.pose, b.pose);
    assert.equal(a.attention, null);
  }
});

test('handoff preserves pose and velocity while the live idle motion resumes', () => {
  for (const random of [0, .3, .64, .99]) {
    const motion = create(random); wake(motion); advance(motion, 1.4);
    for (let i = 0; i < 70; i++) {
      const before = { ...motion.pose }, speed = { ...motion.velocity };
      motion.step(1 / 120);
      for (const key of Object.keys(before)) {
        assert(Number.isFinite(motion.pose[key]) && Number.isFinite(motion.velocity[key]), key);
        assert(Math.abs(motion.pose[key] - before[key]) < .5, `${key}: no handoff snap`);
        assert(Math.abs(motion.velocity[key] - speed[key]) < 15, `${key}: no handoff impulse`);
      }
    }
    assert.equal(motion.waking, false);
  }
});

test('task states interrupt wake without resetting the rendered pose or velocity', () => {
  for (const time of [.1, .8, 1.6]) for (const state of CYRENE_STATES.filter(value => value !== 'idle')) {
    const motion = create(); wake(motion); advance(motion, time);
    const pose = { ...motion.pose }, velocity = { ...motion.velocity };
    motion.setState(state);
    assert.equal(motion.waking, false);
    assert.equal(motion.state, state);
    assert.deepEqual(motion.pose, pose);
    assert.deepEqual(motion.velocity, velocity);
    advance(motion, 3);
    for (const value of Object.values(motion.pose)) assert(Number.isFinite(value));
    if (['waiting','done','error','stopped','reconnecting'].includes(state)) assert(motion.pose[state] > .99);
  }
});

test('waking into a running task does not delay its motion with the idle performance', () => {
  for (const state of CYRENE_STATES.filter(value => value !== 'idle')) {
    const motion = create(); motion.setSleeping(true, true); motion.setState(state);
    motion.setSleeping(false);
    assert.equal(motion.waking, false);
    advance(motion, .6);
    assert(motion.pose.sleep < .01);
    assert.equal(motion.state, state);
  }
});

test('sleeping again cancels wake, settles without work, and can wake afresh', () => {
  for (const time of [.2, 1.6]) {
    const motion = create(); wake(motion); advance(motion, time);
    const pose = { ...motion.pose };
    motion.setSleeping(true);
    assert.equal(motion.waking, false);
    assert.deepEqual(motion.pose, pose);
    motion.step(1);
    assert.equal(motion.resting, true);
    assert.equal(motion.pose.eyeWidth, 1);
    motion.setSleeping(false); advance(motion, .2);
    assert(motion.pose.y < -4);
  }
});

test('reduced motion and explicit immediate wake skip the performance, including mid-wake', () => {
  const motion = create(); wake(motion); advance(motion, .2);
  motion.setReduced(true);
  assert.equal(motion.waking, false);
  assert.equal(motion.resting, true);
  assert.equal(motion.pose.eyeWidth, 1);
  assert.equal(motion.pose.sleep, 0);
  const pose = { ...motion.pose }; advance(motion, 3); assert.deepEqual(motion.pose, pose);
  motion.setSleeping(true); motion.setSleeping(false);
  assert.equal(motion.waking, false);
  assert.deepEqual(motion.pose, pose);
  motion.setReduced(false); motion.setSleeping(true, true); motion.setSleeping(false, true);
  assert.equal(motion.waking, false);
  assert.equal(motion.pose.sleep, 0);
  const fresh = create(); fresh.setSleeping(false); assert.equal(fresh.waking, false);
});
