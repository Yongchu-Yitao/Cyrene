const duration = 1;
const ease = value => {
  const t = Math.max(0, Math.min(1, value));
  return t * t * (3 - 2 * t);
};

// Sleep is a presentation overlay; it never replaces the assistant's run state.
export function createCyreneSleepMotion() {
  let sleeping = false, age = 0, start = null, target = null;
  function paint(pose, velocity) {
    const progress = ease(age / duration);
    for (const key of Object.keys(pose)) {
      const amount = key === 'blink' ? ease(age / .55)
        : key === 'sleep' ? ease((age - .45) / .55) : progress;
      pose[key] = start[key] + (target[key] - start[key]) * amount;
      velocity[key] = 0;
    }
  }
  return {
    get sleeping() { return sleeping; },
    get settled() { return sleeping && age >= duration; },
    set(value, pose, velocity, immediate = false) {
      if (value && !sleeping) {
        age = 0;
        start = { ...pose };
        target = { ...pose, x: 0, y: 2, r: -10, sx: 1.03, sy: .94,
          ex: 0, ey: .8, blink: .08, leftEye: 1, rightEye: 1, glow: 0,
          angle: Math.round(pose.angle / 360) * 360, sleep: 1,
          waiting: 0, done: 0, error: 0, stopped: 0, reconnecting: 0 };
      }
      sleeping = value;
      if (sleeping && immediate) { age = duration; paint(pose, velocity); }
    },
    step(dt, pose, velocity) {
      if (!sleeping || age >= duration) return;
      age = Math.min(duration, age + dt);
      paint(pose, velocity);
    },
  };
}
