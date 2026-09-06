import { useWbcEffect, useWbcState, wbcT } from "../../workbench-chat.jsx"

function wbcConversationResizeActive(thread) {
  var classes = document.body.classList;
  return !!(thread && thread.wbcResizeActive)
    || classes.contains("wbc-resizing-side-agent")
    || classes.contains("wbc-resizing-pane-column")
    || classes.contains("wbc-resizing-pane-row");
}

function wbcConversationMarkersEqual(left, right) {
  if (left === right) return true;
  if (!Array.isArray(left) || left.length !== right.length) return false;
  for (var index = 0; index < right.length; index += 1) {
    if (
      left[index].index !== right[index].index
      || left[index].role !== right[index].role
      || left[index].label !== right[index].label
      || left[index].text !== right[index].text
    ) return false;
  }
  return true;
}

class WbcConversationNavigatorObserver {
  constructor(thread, setSnapshot) {
    this.thread = thread;
    this.setSnapshot = setSnapshot;
    this.raf = 0;
    this.items = [];
    this.centers = [];
    this.markers = [];
    this.itemsDirty = true;
    this.dirtyFrom = 0;
    this.clientHeight = Math.max(1, thread.clientHeight);
    this.observedItems = new Set();
    this.measure = this.measure.bind(this);
    this.scheduleMeasure = this.scheduleMeasure.bind(this);
    this.invalidateAll = this.invalidateAll.bind(this);
    this.invalidateItems = this.invalidateItems.bind(this);
    this.itemObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(this.onItemResize.bind(this))
      : null;
    this.threadObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(this.invalidateAll)
      : null;
    this.mutationObserver = typeof MutationObserver === "function"
      ? new MutationObserver(this.invalidateItems)
      : null;
  }

  onItemResize(entries) {
    if (wbcConversationResizeActive(this.thread)) { this.dirtyFrom = 0; return; }
    entries.forEach(function (entry) {
      var index = this.items.indexOf(entry.target);
      if (index >= 0) this.dirtyFrom = Math.min(this.dirtyFrom, index);
    }, this);
    this.scheduleMeasure();
  }

  refreshItems() {
    var nextItems = Array.prototype.slice.call(this.thread.querySelectorAll(":scope > [data-wbc-nav-item='true']"));
    if (this.itemObserver) {
      this.observedItems.forEach(function (item) {
        if (nextItems.indexOf(item) >= 0) return;
        this.itemObserver.unobserve(item);
        this.observedItems.delete(item);
      }, this);
      nextItems.forEach(function (item) {
        if (this.observedItems.has(item)) return;
        this.observedItems.add(item);
        this.itemObserver.observe(item);
      }, this);
    }
    this.items = nextItems;
    this.centers.length = nextItems.length;
    this.markers = nextItems.map(function (item, index) {
      var role = String(item.dataset.wbcNavRole || "assistant");
      return {
        index: index,
        role: role,
        label: String(item.dataset.wbcNavLabel || ""),
        text: String(item.dataset.wbcNavText || item.dataset.wbcNavLabel || ""),
      };
    });
    this.itemsDirty = false;
    this.dirtyFrom = 0;
  }

  activeMarker() {
    var viewportCenter = this.thread.scrollTop + this.clientHeight * 0.42;
    var low = 0;
    var high = this.centers.length;
    while (low < high) {
      var middle = (low + high) >> 1;
      if (this.centers[middle] < viewportCenter) low = middle + 1;
      else high = middle;
    }
    var after = Math.min(low, this.centers.length - 1);
    var before = Math.max(0, after - 1);
    return this.centers.length === 0
      ? -1
      : (Math.abs(this.centers[before] - viewportCenter) <= Math.abs(this.centers[after] - viewportCenter) ? before : after);
  }

  measure() {
    this.raf = 0;
    if (wbcConversationResizeActive(this.thread)) return;
    if (this.itemsDirty) this.refreshItems();
    if (this.dirtyFrom < this.items.length) {
      for (var index = this.dirtyFrom; index < this.items.length; index += 1) {
        this.centers[index] = this.items[index].offsetTop + this.items[index].offsetHeight / 2;
      }
    }
    this.dirtyFrom = Infinity;
    var active = this.activeMarker();
    var markers = this.markers;
    this.setSnapshot(function (current) {
      var visible = markers.length > 5;
      if (current.visible === visible && current.active === active && wbcConversationMarkersEqual(current.markers, markers)) return current;
      return { visible: visible, active: active, markers: markers };
    });
  }

  scheduleMeasure() {
    if (wbcConversationResizeActive(this.thread) || this.raf) return;
    this.raf = requestAnimationFrame(this.measure);
  }

  invalidateAll() {
    this.clientHeight = Math.max(1, this.thread.clientHeight);
    this.dirtyFrom = 0;
    this.scheduleMeasure();
  }

  invalidateItems() {
    this.itemsDirty = true;
    this.dirtyFrom = 0;
    this.scheduleMeasure();
  }

  start() {
    if (this.threadObserver) this.threadObserver.observe(this.thread);
    if (this.mutationObserver) this.mutationObserver.observe(this.thread, { childList: true });
    this.thread.addEventListener("workbench:transcript-resize-end", this.invalidateAll);
    this.thread.addEventListener("scroll", this.scheduleMeasure, { passive: true });
    window.addEventListener("resize", this.invalidateAll);
    window.addEventListener("workbench:split-resize-end", this.invalidateAll);
    this.scheduleMeasure();
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    if (this.threadObserver) this.threadObserver.disconnect();
    if (this.itemObserver) this.itemObserver.disconnect();
    if (this.mutationObserver) this.mutationObserver.disconnect();
    this.thread.removeEventListener("workbench:transcript-resize-end", this.invalidateAll);
    this.thread.removeEventListener("scroll", this.scheduleMeasure);
    window.removeEventListener("resize", this.invalidateAll);
    window.removeEventListener("workbench:split-resize-end", this.invalidateAll);
  }
}

function WbcConversationNavigator({ threadRef, chatId, messagesRevision }) {
  var [snapshot, setSnapshot] = useWbcState({ visible: false, active: -1, markers: [] });

  useWbcEffect(function () {
    var thread = threadRef.current;
    if (!thread) return undefined;
    var observer = new WbcConversationNavigatorObserver(thread, setSnapshot);
    observer.start();
    return function () { observer.stop(); };
  }, [threadRef, chatId, messagesRevision]);

  function jumpToMarker(index) {
    var thread = threadRef.current;
    if (!thread) return;
    var target = thread.querySelectorAll(":scope > [data-wbc-nav-item='true']")[index];
    if (!target) return;
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    thread.scrollTo({
      top: Math.max(0, target.offsetTop - 18),
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }

  if (!snapshot.visible) return null;
  return (
    <nav className="wbc-conversation-nav" data-cyrene-revision-volatile="true" aria-label={wbcT("workbenchChat.navigation.label", "Conversation navigation")}>
      <button type="button" className="wbc-conversation-nav-trigger" aria-label={wbcT("workbenchChat.navigation.label", "Conversation navigation")}>
        <span /><span /><span /><span /><span />
      </button>
      <div className="wbc-conversation-nav-panel">
        <div className="wbc-conversation-nav-heading">
          <span>{wbcT("workbenchChat.navigation.messages", "Your messages")}</span>
          <span>{snapshot.markers.length}</span>
        </div>
        <div className="wbc-conversation-nav-list">
          {snapshot.markers.map(function (marker) {
            return (
              <button
                type="button"
                key={marker.index}
                className={"wbc-conversation-marker " + marker.role + (marker.index === snapshot.active ? " active" : "")}
                aria-label={wbcT("workbenchChat.navigation.jump", "Jump to: {label}", { label: marker.label })}
                aria-current={marker.index === snapshot.active ? "location" : undefined}
                onClick={function () { jumpToMarker(marker.index); }}
              >
                <span className="wbc-conversation-marker-index">{marker.index + 1}</span>
                <span className="wbc-conversation-marker-text">{marker.text}</span>
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
}

export { WbcConversationNavigator }
