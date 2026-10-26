// Keep conditional reads and polling independent of React so cancellation and
// visibility transitions can be exercised without mounting the graph canvas.
export function createGraphReader(fetcher = globalThis.fetch) {
  let active, cachedUrl, etag = '', cached;
  return {
    async read(url) {
      active?.abort();
      const controller = new AbortController();
      active = controller;
      if (cachedUrl !== url) { cachedUrl = url; etag = ''; cached = undefined; }
      const response = await fetcher(url, {
        signal: controller.signal, cache: 'no-store',
        headers: etag ? { 'If-None-Match': etag } : {},
      });
      const data = response.status === 304 ? cached : await response.json();
      if (controller.signal.aborted || active !== controller) {
        throw new DOMException('Graph read superseded', 'AbortError');
      }
      if (response.status === 304 && cached) return cached;
      if (!response.ok || !data || data.error) {
        throw new Error(data?.detail || data?.error?.message || data?.error || 'Unable to read context graph');
      }
      etag = response.headers.get('ETag') || '';
      cached = data;
      return data;
    },
    abort() { active?.abort(); active = undefined; },
  };
}

export function pollVisibleGraph(load, {
  document = globalThis.document,
  schedule = globalThis.setTimeout,
  cancel = globalThis.clearTimeout,
  interval = 5000,
} = {}) {
  let stopped = false, running = false, timer;
  async function poll() {
    if (stopped || running || document.hidden) return;
    running = true;
    try { await load(); }
    finally {
      running = false;
      if (!stopped && !document.hidden) timer = schedule(poll, interval);
    }
  }
  function visibility() {
    cancel(timer);
    if (!document.hidden) void poll();
  }
  document.addEventListener('visibilitychange', visibility);
  void poll();
  return () => {
    stopped = true;
    cancel(timer);
    document.removeEventListener('visibilitychange', visibility);
  };
}
