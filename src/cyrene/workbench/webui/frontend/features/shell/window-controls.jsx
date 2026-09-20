export function WorkbenchWindowControls({ t }) {
  const host = window.cyrene;
  const platform = host && host.platform;
  const bridge = host && host.windowControls;
  const enabled = !!bridge && (platform === "win32" || platform === "linux");
  const [state, setState] = React.useState({ maximized: false, fullscreen: false });
  React.useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const receive = next => { if (alive && next && next.ok) setState(next); };
    const unsubscribe = bridge.onState(receive);
    bridge.invoke({ action: "state" }).then(receive).catch(() => {});
    return () => { alive = false; unsubscribe(); };
  }, [enabled, bridge]);

  React.useEffect(() => {
    if (!enabled || platform !== "win32") return;
    const topbar = document.querySelector(".workbench-topbar, .wb-ob-topbar");
    if (!topbar) return;
    let frame = 0;
    let previous = "";
    // Normalize computed CSS colors (including modern color spaces) for IPC.
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    function hex(color) {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return "#" + Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3)
        .map(value => value.toString(16).padStart(2, "0")).join("");
    }
    function sync() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const style = getComputedStyle(topbar);
        const payload = { action: "overlay", color: hex(style.backgroundColor),
          symbolColor: hex(style.color), height: Math.round(topbar.getBoundingClientRect().height) };
        const key = JSON.stringify(payload);
        if (key !== previous) {
          previous = key;
          bridge.invoke(payload).catch(() => {});
        }
      });
    }
    const observer = new MutationObserver(sync);
    // Theme and custom background tokens live on the root/body/shell.
    for (let node = topbar; node; node = node.parentElement) {
      observer.observe(node, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
    }
    const resize = new ResizeObserver(sync);
    resize.observe(topbar);
    sync();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); resize.disconnect(); };
  }, [enabled, platform, bridge]);

  if (!enabled || platform !== "linux") return null;
  function invoke(action) {
    bridge.invoke({ action }).then(next => { if (next && next.ok && action !== "close") setState(next); }).catch(() => {});
  }
  const restore = state.maximized || state.fullscreen;
  const maximizeLabel = t(restore ? "common.restoreWindow" : "common.maximizeWindow");
  return <div className="workbench-window-controls">
    <button type="button" title={t("common.minimize")} aria-label={t("common.minimize")} onClick={() => invoke("minimize")}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10" /></svg>
    </button>
    <button type="button" title={maximizeLabel} aria-label={maximizeLabel} onClick={() => invoke("toggle-maximize")}>
      <svg viewBox="0 0 16 16" aria-hidden="true">{restore
        ? <><path d="M5 5V3h8v8h-2" /><rect x="3" y="5" width="8" height="8" /></>
        : <rect x="3" y="3" width="10" height="10" />}</svg>
    </button>
    <button type="button" className="workbench-window-close" title={t("common.close")} aria-label={t("common.close")} onClick={() => invoke("close")}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3 3 10 10M13 3 3 13" /></svg>
    </button>
  </div>;
}
