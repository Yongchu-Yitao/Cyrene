// One request refreshes every home card. Keep the existing three-second card
// freshness, including immediate reconciliation after local actions or reveal.
export function observeHomeCards({ request, onCards, onError, target = window }) {
  const doc = target.document;
  let disposed = false, pending = false, again = false, unavailable = false, timer;
  const controller = new AbortController();
  async function refresh() {
    target.clearTimeout(timer);
    if (disposed || doc.hidden || unavailable) return;
    if (pending) { again = true; return; }
    pending = true;
    try {
      const result = await request('/api/assistant/cards/home', { signal: controller.signal });
      if (!disposed) {
        const order = ['overview', 'resume', 'results'];
        onCards(result.cards.filter(card => card.status === 'active').sort((a, b) => order.indexOf(a.spec.home_kind) - order.indexOf(b.spec.home_kind)));
        onError('');
      }
    } catch (error) {
      unavailable = error.status === 404;
      if (!disposed && !unavailable) onError('欢迎卡片暂时无法更新。');
    } finally {
      pending = false;
      if (!disposed && !doc.hidden && !unavailable) timer = target.setTimeout(refresh, again ? 0 : 3000);
      again = false;
    }
  }
  const visibility = () => { target.clearTimeout(timer); if (!doc.hidden) refresh(); };
  target.addEventListener('cyrene:card-updated', refresh);
  doc.addEventListener('visibilitychange', visibility);
  refresh();
  return () => {
    disposed = true; controller.abort(); target.clearTimeout(timer);
    target.removeEventListener('cyrene:card-updated', refresh);
    doc.removeEventListener('visibilitychange', visibility);
  };
}

// Preserve the visible animation and its phase. Only stop painting when the
// entire card is offscreen or its document is hidden; blur alone stays animated.
export function observeCardVisibility(element) {
  const doc = element.ownerDocument;
  let intersecting = true;
  const update = () => element.toggleAttribute('data-animations-paused', doc.hidden || !intersecting);
  const Observer = doc.defaultView?.IntersectionObserver;
  const observer = Observer ? new Observer(entries => {
    intersecting = entries[entries.length - 1].isIntersecting;
    update();
  }) : null;
  observer?.observe(element);
  doc.addEventListener('visibilitychange', update);
  update();
  return () => {
    observer?.disconnect(); doc.removeEventListener('visibilitychange', update);
    element.removeAttribute('data-animations-paused');
  };
}
