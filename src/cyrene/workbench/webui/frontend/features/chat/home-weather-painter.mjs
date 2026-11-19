const tints = {
  clear: ["#f5d99b", "#705730", "#c2c2e5", "#484467"],
  partly_cloudy: ["#dfdfbe", "#595d48", "#c6c6dd", "#484860"],
  cloudy: ["#c3d4df", "#455966", "#bfc9db", "#424d65"],
  rain: ["#aad0e6", "#345b72", "#a9c6d6", "#345464"],
  snow: ["#c6e1e9", "#496672", "#bed8e6", "#40566c"],
  storm: ["#c7c2db", "#574e69", "#c4bcd6", "#4b425c"],
  fog: ["#ccd9cd", "#506156", "#c4d0c9", "#455850"],
  unknown: ["#e1e5e7", "#3c454c", "#d6dbe1", "#38414c"]
};
export function weatherWash(kind, period) {
  const colors = tints[kind === "night" ? "clear" : kind] ?? tints.unknown;
  const offset = period === "night" ? 2 : 0;
  return "light-dark(" + colors[offset] + "," + colors[offset + 1] + ")";
}
export function createWeatherPainter(ctx, kind, period) {
  const front = new Path2D("M35 78C23 78 18 73 18 66C18 59 23 54 31 53C32 46 38 42 46 42C50 29 65 25 76 32C81 35 84 40 86 46C93 43 102 45 107 52C117 51 125 57 125 65C125 74 118 79 107 79Z");
  const rear = new Path2D("M54 56C44 56 41 51 43 45C45 38 50 36 58 37C61 24 72 19 83 22C91 23 98 31 99 36C108 31 117 37 119 44C129 44 133 49 131 55C127 60 111 59 97 59Z");
  function gradient(x, y, endX, endY, stops) {
    const gradientFill = ctx.createLinearGradient(x, y, endX, endY);
    stops.forEach(([at, color]) => gradientFill.addColorStop(at, color));
    return gradientFill;
  }
  function glow(x, y, radius, color) {
    const gradientFill = ctx.createRadialGradient(x, y, 0, x, y, radius);
    gradientFill.addColorStop(0, color);
    gradientFill.addColorStop(1, "transparent");
    ctx.fillStyle = gradientFill;
    ctx.fillRect(x - radius, y - radius, 2 * radius, 2 * radius);
  }
  function disc(x, y, radius, fill) {
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, 2 * Math.PI);
    ctx.fillStyle = fill;
    ctx.fill();
  }
  const night = period === "night";
  const rearColors = night ? ["#919fb9", "#6b7e9d", "#516887"] : ["#c1d0dc", "#9db0c2", "#7c94aa"];
  const frontColors = kind === "storm" ? ["#c7ccd6", "#9aa7bc", "#78889f"] :
    night ? ["#cbd5e3", "#a9b9d0", "#7b91ac"] : ["#eff4f7", "#d0dee7", "#9bb2c4"];
  function cloud(shape, back, seconds) {
    const phase = seconds * (back ? 0.52 : 0.78) + (back ? 1.7 : 0);
    ctx.save();
    ctx.translate((back ? 4 : 7) * Math.sin(phase), (back ? 0.9 : 1.35) * Math.sin(phase * 0.83));
    const colors = back ? rearColors : frontColors;
    ctx.fillStyle = gradient(65, 28, 75, 81, [[0, colors[0]], [0.52, colors[1]], [1, colors[2]]]);
    ctx.shadowColor = back ? "#455a6f15" : "#425d7620";
    ctx.shadowBlur = 5;
    ctx.shadowOffsetY = 2;
    ctx.fill(shape);
    ctx.shadowColor = "transparent";
    ctx.clip(shape);
    glow(52, 33, 33, "#ffffff48");
    glow(97, 45, 29, back ? "#aec4d623" : "#e9f4ff38");
    ctx.restore();
  }
  function drawSunOrMoon(seconds) {
    const clear = kind === "clear" || kind === "night";
    const x = clear ? 80 : 102;
    const y = clear ? 55 : 37;
    const radius = clear ? 19 : 16;
    if (period === "day") {
      glow(x, y, radius + 12, "#f6cc7547");
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(0.07 * Math.sin(seconds * 0.58));
      for (let i = 0; i < 8; i++) {
        ctx.rotate(Math.PI / 4);
        ctx.strokeStyle = "#d4ad6d";
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(0, -radius - 5);
        ctx.lineTo(0, -radius - 9);
        ctx.stroke();
      }
      ctx.restore();
      disc(x, y, radius, gradient(x, y - radius, x, y + radius, [[0, "#fae4b3"], [0.6, "#efca82"], [1, "#dcb070"]]));
    } else {
      ctx.save();
      ctx.translate(x, y);
      ctx.scale(radius / 19, radius / 19);
      ctx.fillStyle = gradient(-12, -17, 11, 19, [[0, "#e5dfda"], [1, "#a5acc6"]]);
      ctx.fill(new Path2D("M3 -21C-13 -22 -23 -6 -18 8C-12 24 10 27 20 9C4 14 -9 -5 3 -21Z"));
      ctx.restore();
      ctx.globalAlpha = 0.6 + 0.25 * Math.sin(seconds * 0.82);
      disc(x + 26, y - 12, 1.5, "#b1adc2");
      ctx.globalAlpha = 1;
    }
  }
  function precipitation(seconds) {
    const snow = kind === "snow";
    const storm = kind === "storm";
    const cycle = snow ? 4.9 : storm ? 1.9 : 3.3;
    const drops = [[42, 0.04], [69, 0.38], [95, 0.57], [55, 0.83]];
    drops.forEach(([x, start], i) => {
      const fraction = (seconds / cycle + 1 - start) % 1;
      const active = snow ? 0.88 : 0.4;
      if (fraction > active) return;
      const progress = fraction / active;
      const dx = 7 * Math.sin((seconds - progress * active * cycle) * 0.78);
      const endX = x + dx + (snow ? 3 * Math.sin(5 * progress + i) : -progress * (storm ? 7 : 2));
      const endY = 73 + (snow ? progress : Math.pow(progress, 1.45)) * 28;
      ctx.globalAlpha = Math.min(1, progress * 5, (1 - progress) * 4) * 0.84;
      if (snow) {
        disc(endX, endY, 1.6 + i % 2 * 0.55, "#93adbd");
      } else {
        ctx.strokeStyle = period === "night" ? "#8faaca" : "#759cb7";
        ctx.lineWidth = 1.7;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(endX + 0.9, endY - 4);
        ctx.lineTo(endX, endY + 1);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    });
  }
  function wind(seconds, fog) {
    for (let i = 0; i < (fog ? 3 : 2); i++) {
      const progress = (seconds / (fog ? 10 : 6) + i * 0.48) % 1;
      const x = (fog ? 28 : 35) + progress * (fog ? 14 : 27) + i * 8;
      const y = (fog ? 72 : 88) + i * 5;
      ctx.strokeStyle = gradient(x, y, x + 52, y, [[0, "transparent"], [0.36, fog ? "#9cabb1" : "#a6b8c5"], [1, "transparent"]]);
      ctx.lineWidth = fog ? 3.5 : 1.3;
      ctx.globalAlpha = (fog ? 0.75 : 0.56) * Math.sin(progress * Math.PI);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(x, y + 1);
      ctx.bezierCurveTo(x + 15, y - 2, x + 30, y + 2, x + 52, y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  return function paint(seconds) {
    ctx.setTransform(ctx.canvas.width / 160, 0, 0, ctx.canvas.height / 116, 0, 0);
    ctx.clearRect(0, 0, 160, 116);
    if (kind === "unknown") {
      ctx.strokeStyle = "#a7b1b9";
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(68, 57);
      ctx.lineTo(88, 57);
      ctx.stroke();
      return;
    }
    glow(80, 57, 52, period === "night" ? "#bec2e22b" : "#a4c4d129");
    if (["clear", "night", "partly_cloudy"].includes(kind)) drawSunOrMoon(seconds);
    if (kind === "clear" || kind === "night") return;
    ctx.save();
    if (kind === "partly_cloudy") {
      ctx.translate(6, 15);
      ctx.scale(0.83, 0.83);
    }
    if (["rain", "storm", "snow"].includes(kind)) precipitation(seconds);
    if (kind !== "partly_cloudy") cloud(rear, true, seconds);
    cloud(front, false, seconds);
    ctx.restore();
    if (kind === "cloudy" || kind === "fog") wind(seconds, kind === "fog");
    if (kind === "storm") {
      ctx.save();
      ctx.translate(79, 74);
      ctx.globalAlpha = 0.75 + 0.18 * Math.pow(Math.max(0, Math.cos(seconds * 1.1)), 6);
      ctx.fillStyle = "#c6a16e";
      ctx.fill(new Path2D("M0 0H8L2 9H7L-7 25L-3 12H-8Z"));
      ctx.restore();
    }
  };
}
