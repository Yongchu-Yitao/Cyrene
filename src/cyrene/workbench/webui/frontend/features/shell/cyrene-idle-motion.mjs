// The four approved idle performances share attention, but keep their own timing.
const styles = {
  snappy: { tilt: 8, bounce: .9, delay: .13, hold: 1.25 },
  soft: { tilt: 5, bounce: .7, delay: .23, hold: 1.65 },
  orbit: { tilt: 3, bounce: .45, delay: .17, hold: 1.5 },
  playful: { tilt: 12, bounce: 1.15, delay: .16, hold: 1.1 },
};

export function createCyreneIdleMotion({ random = Math.random } = {}) {
  let kind = 'wander', age = 0, duration = 1.5, cooldown = 1.8;
  let blinkAge = 9, nextBlink = 2.5, inputCooldown = 0, pendingInput = false;
  let look = { x: -.8, y: -.45 }, head = { ...look }, returnKind = '';
  let pointer = null, input = null;
  function direction() {
    return (kind === 'input' ? input : kind === 'pointer' ? pointer : null) || look;
  }
  function choose(next, cfg) {
    const previous = direction();
    kind = next; age = 0; returnKind = '';
    if (next === 'wander') {
      const a = random() * Math.PI * 2;
      look = { x: Math.cos(a), y: Math.sin(a) * .8 };
      duration = 1.2 + random() * 1.3;
    } else if (next === 'away') {
      look = { x: -previous.x * .75, y: Math.min(.4, -previous.y * .35 + .15) };
      duration = .65; blinkAge = 0;
    } else if (next === 'rest') {
      look = { x: 0, y: 0 }; duration = .7;
    } else {
      look = { ...(next === 'input' ? input : pointer) };
      duration = cfg.hold; cooldown = 4.5 + random() * 2.5;
    }
  }
  function updateAttention(cfg) {
    if (pendingInput) {
      pendingInput = false;
      if (input) { choose('input', cfg); inputCooldown = 5; }
    }
    if ((kind === 'input' && !input) || (kind === 'pointer' && !pointer)) choose('away', cfg);
  }
  function canPeek(style) {
    return kind === 'away' && style === 'playful' && returnKind && (returnKind === 'input' ? input : pointer);
  }
  function chooseNext(cfg, style) {
    if (kind === 'input' || kind === 'pointer') {
      const prior = kind; choose('away', cfg); returnKind = prior;
    } else if (canPeek(style)) {
      choose(returnKind, cfg); look = { ...direction() }; kind = 'peek'; duration = .45;
    } else if (kind === 'peek') {
      choose('rest', cfg); duration = 1.3;
    } else if (cooldown <= 0 && (pointer || input)) {
      choose(pointer && (!input || random() < .55) ? 'pointer' : 'input', cfg);
    } else {
      choose(random() < .22 ? 'rest' : 'wander', cfg);
    }
  }
  return {
    get attention() { return kind; },
    setTargets(targets) { pointer = targets.pointer || null; input = targets.input || null; },
    noticeInput() { if (input && inputCooldown <= 0) pendingInput = true; },
    clearTargets() { pointer = input = null; pendingInput = false; },
    step(dt, style, phase) {
      const cfg = styles[style];
      age += dt; cooldown -= dt; inputCooldown -= dt; blinkAge += dt; nextBlink -= dt;
      if (nextBlink <= 0) { blinkAge = 0; nextBlink = 2.8 + random() * 2.4; }
      updateAttention(cfg);
      if (age > duration) chooseNext(cfg, style);
      const target = direction();
      if (age > cfg.delay) {
        head.x += (target.x - head.x) * (1 - Math.exp(-dt * 7));
        head.y += (target.y - head.y) * (1 - Math.exp(-dt * 7));
      }
      const breath = Math.sin(phase * 2);
      const pose = {
        x: head.x * .65, y: -cfg.bounce * breath + head.y * .55, r: head.x * cfg.tilt,
        sx: 1 + .025 * breath, sy: 1 + .025 * breath,
        ex: target.x * 2.6, ey: target.y * 1.65,
        blink: blinkAge < .12 || (style === 'soft' && blinkAge > .2 && blinkAge < .3) ? .12 : 1,
        leftEye: 1, rightEye: 1,
        angle: style === 'orbit' ? head.x * 16 + Math.sin(phase * 1.2) * 7 : head.x * 2,
        glow: style === 'orbit' ? .25 + .2 * Math.sin(phase * 2) : .12 * Math.max(0, breath),
      };
      if (style === 'playful' && kind === 'away' && age > .25 && age < .45) {
        pose.leftEye = .12; pose.blink = 1;
      }
      if (style === 'soft') { pose.r += 2 * Math.sin(phase * 2); pose.sx = pose.sy = 1; }
      return pose;
    },
  };
}
