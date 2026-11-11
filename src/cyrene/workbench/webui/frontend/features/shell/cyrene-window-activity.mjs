export const CYRENE_SLEEP_DELAY_MS = 120000;
const windows = new WeakMap();

function createWindowActivity(doc) {
  const win = doc.defaultView, subscribers = new Set(), removers = [];
  const focused = () => !doc.hidden && (typeof doc.hasFocus !== 'function' || doc.hasFocus());
  let sleeping = !focused(), lastActivity = win.performance.now(), timer = null;
  function listen(target, event, callback) {
    target.addEventListener(event, callback, { capture: true, passive: true });
    removers.push(() => target.removeEventListener(event, callback, true));
  }
  function stopTimer() { if (timer !== null) win.clearTimeout(timer); timer = null; }
  function publish(immediate = false) {
    subscribers.forEach(callback => callback(sleeping, immediate));
  }
  function sleep(immediate = false) {
    stopTimer();
    if (sleeping && !immediate) return;
    sleeping = true;
    publish(immediate);
  }
  function checkDeadline() {
    timer = null;
    const remaining = CYRENE_SLEEP_DELAY_MS - (win.performance.now() - lastActivity);
    if (!focused()) sleep(doc.hidden);
    else if (remaining <= 0) sleep();
    else timer = win.setTimeout(checkDeadline, remaining);
  }
  function activity(event) {
    if (event.isTrusted === false || !focused()) return;
    lastActivity = win.performance.now();
    if (sleeping) { sleeping = false; publish(); }
    // Moving the pointer updates a timestamp, not a timer on every event.
    if (timer === null) timer = win.setTimeout(checkDeadline, CYRENE_SLEEP_DELAY_MS);
  }
  for (const event of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'input']) listen(doc, event, activity);
  listen(win, 'focus', event => { if (!event.target || event.target === win) activity(event); });
  // Moving focus into an iframe is not leaving the application window.
  listen(win, 'blur', () => { if (!focused()) sleep(doc.hidden); });
  listen(doc, 'visibilitychange', () => { if (doc.hidden) sleep(true); });
  if (!sleeping) timer = win.setTimeout(checkDeadline, CYRENE_SLEEP_DELAY_MS);
  return {
    subscribe(callback) { subscribers.add(callback); callback(sleeping, sleeping); },
    unsubscribe(callback) {
      subscribers.delete(callback);
      if (subscribers.size) return;
      stopTimer(); removers.forEach(remove => remove()); windows.delete(doc);
    },
  };
}

export function observeCyreneWindowActivity(doc, callback) {
  let activity = windows.get(doc);
  if (!activity) { activity = createWindowActivity(doc); windows.set(doc, activity); }
  activity.subscribe(callback);
  return () => activity.unsubscribe(callback);
}
