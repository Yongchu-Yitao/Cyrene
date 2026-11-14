// The approved "caught napping" performance, in seconds and 32px SVG units.
const handoff = 1.42, duration = 1.8;
const tracks = {
  blink: [[0,.08],[.045,.18],[.115,1.4],[.32,1.4],[.62,1.08],[1.08,1.08],[1.16,.09],[1.26,1]],
  y: [[0,2],[.055,3.2],[.20,-4.8],[.29,-4.4],[.46,1.55],[.62,-.8],[.78,0]],
  x: [[0,0],[.20,-.7],[.46,.35],[.78,0],[.88,-.3],[1.04,.35],[1.25,0]],
  r: [[0,-10],[.055,-13],[.20,13],[.30,10],[.46,-6],[.63,3],[.75,0],[.88,-5],[1.04,5],[1.25,0]],
  sx: [[0,1.03],[.055,1.12],[.18,.91],[.30,.95],[.46,1.12],[.63,.97],[.78,1]],
  sy: [[0,.94],[.055,.83],[.18,1.17],[.30,1.08],[.46,.88],[.63,1.04],[.78,1]],
  ex: [[0,0],[.13,.3],[.67,.3],[.77,-2.1],[.86,-2.1],[.97,2.1],[1.06,2.1],[1.20,0]],
  ey: [[0,.8],[.13,-1.15],[.32,-1.15],[.62,0]],
  angle: [[0,0],[.21,7],[.46,-3],[.75,0]],
};
const smooth = (time, from, to) => {
  const t = Math.max(0, Math.min(1, (time - from) / (to - from)));
  return t * t * t * (t * (t * 6 - 15) + 10);
};
function track(time, points) {
  for (let i = 1; i < points.length; i++) {
    const [end, value] = points[i], [start, previous] = points[i - 1];
    if (time <= end) return previous + (value - previous) * smooth(time, start, end);
  }
  return points.at(-1)[1];
}
function sample(time, start) {
  const pose = { ...start }, correction = 1 - smooth(time, 0, .18);
  for (const [key, points] of Object.entries(tracks)) {
    pose[key] = track(time, points) + (key === 'angle' ? start.angle : (start[key] - points[0][1]) * correction);
  }
  pose.sleep = start.sleep * (1 - smooth(time, .015, .13));
  pose.eyeWidth = 1 + .25 * smooth(time, .02, .12) * (1 - smooth(time, .32, .65)) + (start.eyeWidth - 1) * correction;
  pose.glow = .35 * smooth(time, .08, .18) * (1 - smooth(time, .26, .55)) + start.glow * correction;
  for (const key of ['leftEye', 'rightEye']) pose[key] = 1 + (start[key] - 1) * correction;
  for (const key of ['waiting', 'done', 'error', 'stopped', 'reconnecting']) pose[key] = start[key] * correction;
  return pose;
}

export function createCyreneWakeMotion() {
  let active = false, age = 0, start = null, destination = null, speed = null;
  return {
    get active() { return active; },
    begin(pose) {
      active = true; age = 0; start = { ...pose };
      destination = sample(handoff, start);
      speed = Object.fromEntries(Object.keys(pose).map(key => [key, 0]));
    },
    cancel() { active = false; },
    step(dt, pose, velocity, advance) {
      const previous = age;
      age = Math.min(duration, age + dt);
      // Advance an independent live spring only during the handoff, so looking
      // around cannot compete with the scripted eyes and head before it ends.
      if (age > handoff) advance(age - Math.max(previous, handoff), destination, speed);
      const frame = sample(age, start), epsilon = .00001;
      const before = sample(age - epsilon, start), after = sample(age + epsilon, start);
      const blend = smooth(age, handoff, duration);
      const blendSpeed = (smooth(age + epsilon, handoff, duration) - smooth(age - epsilon, handoff, duration)) / (2 * epsilon);
      for (const key of Object.keys(pose)) {
        pose[key] = frame[key] * (1 - blend) + destination[key] * blend;
        velocity[key] = (after[key] - before[key]) / (2 * epsilon) * (1 - blend)
          + speed[key] * blend + (destination[key] - frame[key]) * blendSpeed;
      }
      if (age >= duration) {
        Object.assign(velocity, speed);
        active = false;
        if (dt > age - previous) advance(dt - (age - previous), pose, velocity);
      }
    },
  };
}
