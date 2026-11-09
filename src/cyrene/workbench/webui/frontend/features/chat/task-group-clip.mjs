// Keep the group's side strokes inside the moving sticky header's round cap.
export function trackTaskGroupClip(group) {
  if (!group) return () => {};
  const header = group.querySelector('.wbc-chat-group-head');
  const win = group.ownerDocument.defaultView;
  let frame = 0;
  let previous = -1;
  function update() {
    frame = 0;
    const top = Math.max(0, header.getBoundingClientRect().top - group.getBoundingClientRect().top - 4);
    if (top !== previous) {
      previous = top;
      group.style.setProperty('--task-group-clip-top', top + 'px');
    }
  }
  function schedule() {
    if (!frame) frame = win.requestAnimationFrame(update);
  }
  group.ownerDocument.addEventListener('scroll', schedule, { capture: true, passive: true });
  win.addEventListener('resize', schedule);
  const observer = new win.ResizeObserver(schedule);
  observer.observe(group);
  update();
  return () => {
    group.ownerDocument.removeEventListener('scroll', schedule, true);
    win.removeEventListener('resize', schedule);
    observer.disconnect();
    if (frame) win.cancelAnimationFrame(frame);
    group.style.removeProperty('--task-group-clip-top');
  };
}
