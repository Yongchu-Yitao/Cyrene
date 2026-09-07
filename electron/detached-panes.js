'use strict';
const path = require('path');
const crypto = require('crypto');

class DetachedPanes {
  constructor({ getMainWindow, BrowserWindow, screen, waitForPort, normalizeBrowserSessionId, browserSessions, installLocalNavigationGuards, AUTH_TOKEN }) {
    this.detachedPaneWindows = new Map();
    this.detachedBrowserSurfaceWindows = new Map();
    this.detachedPaneDragSessions = new Map();
    this.getMainWindow = getMainWindow;
    this.BrowserWindow = BrowserWindow;
    this.screen = screen;
    this.waitForPort = waitForPort;
    this.normalizeBrowserSessionId = normalizeBrowserSessionId;
    this.browserSessions = browserSessions;
    this.installLocalNavigationGuards = installLocalNavigationGuards;
    this.AUTH_TOKEN = AUTH_TOKEN;
  }

  debugDetachedPane(stage, details) {
    if (process.env.ELECTRON_DEV !== '1') return;
    try { console.log(`[detached-pane] ${stage}`, details || ''); } catch (_) {}
  }

  detachedPaneContextForSender(sender) {
    for (const record of this.detachedPaneWindows.values()) {
      if (
        record.window && !record.window.isDestroyed()
        && record.window.webContents === sender
      ) return record;
    }
    return null;
  }

  normalizeDetachedPaneDescriptor(value) {
    const source = value && typeof value === 'object' ? value : {};
    const kind = String(source.kind || '').trim();
    const sourceMeta = source.meta && typeof source.meta === 'object' ? source.meta : null;
    const meta = sourceMeta ? {
      origin: sourceMeta.origin === 'agent' ? 'agent' : 'user',
      claimedByUser: sourceMeta.claimedByUser === true,
      pinned: sourceMeta.pinned === true,
      autoClosePolicy: ['run-end', 'idle', 'never'].includes(String(sourceMeta.autoClosePolicy || ''))
        ? String(sourceMeta.autoClosePolicy) : 'never',
      createdAt: Math.max(0, Number(sourceMeta.createdAt) || 0),
      lastIntentAt: Math.max(0, Number(sourceMeta.lastIntentAt) || 0),
    } : null;
    // Pane transport is intentionally kind-agnostic. Built-ins and future
    // plugin cards share the same structured-clone boundary; the renderer owns
    // whether a registered card kind has UI for this window.
    if (!/^[a-z][a-z0-9._-]{0,79}$/i.test(kind)) throw new Error('Invalid detached pane kind.');
    const serialized = JSON.stringify({
      kind,
      payload: source.payload == null ? null : source.payload,
      meta,
      ownerChatId: String(source.ownerChatId || ''),
      project: source.project && typeof source.project === 'object' ? source.project : null,
      title: String(source.title || '').slice(0, 300),
      items: Array.isArray(source.items) ? source.items : [],
      agent: source.agent && typeof source.agent === 'object' ? source.agent : null,
      agents: Array.isArray(source.agents) ? source.agents : [],
      draft: source.draft && typeof source.draft === 'object' ? source.draft : null,
    });
    if (Buffer.byteLength(serialized, 'utf8') > 2 * 1024 * 1024) {
      throw new Error('Detached pane context is too large.');
    }
    return JSON.parse(serialized);
  }

  detachedPaneBounds(info = {}) {
    const requestedPoint = info.dropPoint && typeof info.dropPoint === 'object'
      ? info.dropPoint
      : null;
    const liveCursor = this.screen.getCursorScreenPoint();
    const cursor = {
      x: Number.isFinite(Number(requestedPoint && requestedPoint.x))
        ? Math.round(Number(requestedPoint.x))
        : liveCursor.x,
      y: Number.isFinite(Number(requestedPoint && requestedPoint.y))
        ? Math.round(Number(requestedPoint.y))
        : liveCursor.y,
    };
    const sourceBounds = info.sourceBounds && typeof info.sourceBounds === 'object'
      ? info.sourceBounds
      : {};
    const width = Math.max(420, Math.min(1400, Math.round(Number(sourceBounds.width) || 720)));
    const height = Math.max(320, Math.min(1200, Math.round(Number(sourceBounds.height) || 720)));
    const grab = info.grabOffset && typeof info.grabOffset === 'object' ? info.grabOffset : {};
    const display = this.screen.getDisplayNearestPoint(cursor);
    const workArea = display.workArea;
    const x = Math.max(
      workArea.x,
      Math.min(
        Math.round(cursor.x - Math.max(0, Math.min(width, Number(grab.x) || width / 2))),
        workArea.x + workArea.width - width,
      ),
    );
    const y = Math.max(
      workArea.y,
      Math.min(
        Math.round(cursor.y - Math.max(0, Math.min(height, Number(grab.y) || 28))),
        workArea.y + workArea.height - height,
      ),
    );
    return { x, y, width: Math.min(width, workArea.width), height: Math.min(height, workArea.height) };
  }

  pointInsideBounds(bounds, point) {
    return !!(bounds && point
      && point.x >= bounds.x
      && point.x <= bounds.x + bounds.width
      && point.y >= bounds.y
      && point.y <= bounds.y + bounds.height);
  }

  pointAtBlockedDisplayEdge(sourceBounds, point) {
    if (!sourceBounds || !point) return false;
    const display = this.screen.getDisplayNearestPoint(point);
    const displayBounds = display && display.bounds;
    if (!displayBounds) return false;
    const seam = 10;
    const aligned = 2;
    const sourceRight = sourceBounds.x + sourceBounds.width;
    const sourceBottom = sourceBounds.y + sourceBounds.height;
    const displayRight = displayBounds.x + displayBounds.width;
    const displayBottom = displayBounds.y + displayBounds.height;
    return (
      (sourceBounds.x <= displayBounds.x + aligned && point.x <= displayBounds.x + seam)
      || (sourceRight >= displayRight - aligned && point.x >= displayRight - seam)
      || (sourceBounds.y <= displayBounds.y + aligned && point.y <= displayBounds.y + seam)
      || (sourceBottom >= displayBottom - aligned && point.y >= displayBottom - seam)
    );
  }

  updateDetachedPaneDrag(sender, rawPoint) {
    const session = this.detachedPaneDragSessions.get(sender && sender.id);
    if (!session) return;
    const point = rawPoint && typeof rawPoint === 'object' ? rawPoint : {};
    const screenX = Number(point.screenX != null ? point.screenX : point.x);
    const screenY = Number(point.screenY != null ? point.screenY : point.y);
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;
    const screenPoint = { x: Math.round(screenX), y: Math.round(screenY) };
    const clientX = Number(point.clientX);
    const clientY = Number(point.clientY);
    const viewportWidth = Number(point.viewportWidth);
    const viewportHeight = Number(point.viewportHeight);
    const previous = session.lastRendererPoint;
    const next = {
      clientX,
      clientY,
      screenX: screenPoint.x,
      screenY: screenPoint.y,
      at: Date.now(),
    };
    session.lastRendererPoint = next;
    session.lastCursorPoint = screenPoint;
    if (!session.loggedFirstMove) {
      session.loggedFirstMove = true;
      this.debugDetachedPane('first pointer move', { senderId: session.senderId, screenPoint });
    }
    if (previous) {
      const dx = next.screenX - previous.screenX;
      const dy = next.screenY - previous.screenY;
      if (Math.abs(dx) >= 0.5 || Math.abs(dy) >= 0.5) session.lastRendererVector = { dx, dy };
    }
    if (
      Number.isFinite(clientX) && Number.isFinite(clientY)
      && Number.isFinite(viewportWidth) && Number.isFinite(viewportHeight)
    ) {
      const vector = session.lastRendererVector || { dx: 0, dy: 0 };
      const seam = 8;
      session.boundaryExitIntent = (
        (clientX <= seam && vector.dx < 0)
        || (clientX >= viewportWidth - seam && vector.dx > 0)
        || (clientY <= seam && vector.dy < 0)
        || (clientY >= viewportHeight - seam && vector.dy > 0)
      );
    }
    if (session.detachedWindow && !session.detachedWindow.isDestroyed()) {
      session.detachedWindow.setBounds(this.detachedPaneBounds({
        ...session.info,
        dropPoint: screenPoint,
      }), false);
    }
    // Pointer capture keeps delivering real screen coordinates beyond the
    // renderer. Create on the first outside point, exactly like the proven
    // side demo; the cursor poll below is now only a safety fallback.
    const crossedSourceBounds = !this.pointInsideBounds(session.sourceWindowBounds, screenPoint);
    // A maximized source window can occupy the complete display work area. In
    // that case macOS clamps the pointer to the physical screen edge, so there
    // is no coordinate that can ever be outside owner.getBounds(). Treat a
    // renderer pointer that reaches the outer seam while still moving outward
    // as the equivalent boundary crossing. This is the only behavioural
    // difference between the small working demo window and Cyrene at full size.
    if (!session.creating && (crossedSourceBounds || session.boundaryExitIntent)) {
      this.debugDetachedPane(crossedSourceBounds ? 'pointer crossed source bounds' : 'pointer pushed past display edge', {
        senderId: session.senderId,
        screenPoint,
        sourceWindowBounds: session.sourceWindowBounds,
        boundaryExitIntent: session.boundaryExitIntent,
      });
      this.startDetachedPaneCreation(session, screenPoint);
    }
  }

  updateDetachedPaneCursor(session) {
    const point = this.screen.getCursorScreenPoint();
    const previous = session && session.lastCursorPoint;
    if (session && previous) {
      const dx = point.x - previous.x;
      const dy = point.y - previous.y;
      if (Math.abs(dx) >= 0.5 || Math.abs(dy) >= 0.5) session.lastCursorVector = { dx, dy };
    }
    if (session) session.lastCursorPoint = point;
    return point;
  }

  clearDetachedPaneDragSession(senderOrId) {
    const senderId = typeof senderOrId === 'number'
      ? senderOrId
      : senderOrId && senderOrId.id;
    const session = this.detachedPaneDragSessions.get(senderId);
    if (!session) return null;
    this.detachedPaneDragSessions.delete(senderId);
    if (session.timer) clearInterval(session.timer);
    session.timer = null;
    if (session.releaseTimer) clearTimeout(session.releaseTimer);
    session.releaseTimer = null;
    return session;
  }

  notifyDetachedPaneCreated(session, result) {
    const sender = session && session.sender;
    if (!sender || sender.isDestroyed()) return;
    try {
      sender.send('detached-pane:created', {
        ...(result || {}),
        cardId: String(session.info.cardId || ''),
        layoutOwnerChatId: String(session.info.layoutOwnerChatId || ''),
      });
    } catch (_) {}
  }

  startDetachedPaneCreation(session, dropPoint) {
    if (!session || session.creating) return;
    session.creating = true;
    this.debugDetachedPane('creating native window', { senderId: session.senderId, dropPoint });
    session.lastCursorPoint = dropPoint || session.lastCursorPoint || this.screen.getCursorScreenPoint();
    const createInfo = {
      ...session.info,
      dropPoint: session.lastCursorPoint,
      dragSession: session,
      sourceWindow: session.owner,
      sourceSenderId: session.senderId,
    };
    this.createDetachedPaneWindow(session.descriptor, createInfo).then((result) => {
      session.creationResult = result;
      if (session.released) {
        this.notifyDetachedPaneCreated(session, result);
        session.createdNotified = true;
        this.finishDetachedPaneDragSession(session, session.releasePoint);
      }
    }).catch((error) => {
      this.clearDetachedPaneDragSession(session.senderId);
      this.notifyDetachedPaneCreated(session, {
        ok: false,
        detached: false,
        error: String(error && error.message || error),
      });
    });
  }

  finishDetachedPaneDragSession(session, rawPoint) {
    if (!session) return { ok: true, detached: false };
    const fallback = session.lastCursorPoint || this.screen.getCursorScreenPoint();
    const point = rawPoint && Number.isFinite(Number(rawPoint.x)) && Number.isFinite(Number(rawPoint.y))
      ? { x: Math.round(Number(rawPoint.x)), y: Math.round(Number(rawPoint.y)) }
      : fallback;
    session.released = true;
    session.releasePoint = point;
    if (
      !session.creating
      && (!this.pointInsideBounds(session.sourceWindowBounds, point)
        || session.boundaryExitIntent
        || this.pointAtBlockedDisplayEdge(session.sourceWindowBounds, point))
    ) {
      this.startDetachedPaneCreation(session, point);
    }
    const win = session.detachedWindow;
    if (!win || win.isDestroyed()) {
      if (session.creating) return { ok: true, detached: false, pending: true };
      this.clearDetachedPaneDragSession(session.senderId);
      this.notifyDetachedPaneCreated(session, { ok: true, detached: false, cancelled: true });
      return { ok: true, detached: false };
    }
    win.setBounds(this.detachedPaneBounds({ ...session.info, dropPoint: point }), false);
    try { win.setIgnoreMouseEvents(false); } catch (_) {}
    try { win.setAlwaysOnTop(false); } catch (_) {}
    if (session.windowReady) {
      win.show();
      win.focus();
    }
    if (session.creating && !session.creationResult) {
      return { ok: true, detached: false, pending: true };
    }
    if (session.creationResult && !session.createdNotified) {
      this.notifyDetachedPaneCreated(session, session.creationResult);
      session.createdNotified = true;
    }
    this.clearDetachedPaneDragSession(session.senderId);
    return { ok: true, detached: true, id: session.detachedRecord && session.detachedRecord.id };
  }

  beginDetachedPaneDrag(sender, rawInfo) {
    const owner = this.BrowserWindow.fromWebContents(sender);
    if (!owner || owner.isDestroyed()) return { ok: false, error: 'source_window_not_found' };
    let descriptor;
    try {
      descriptor = this.normalizeDetachedPaneDescriptor(rawInfo && rawInfo.descriptor);
    } catch (error) {
      return { ok: false, error: String(error && error.message || error) };
    }
    this.clearDetachedPaneDragSession(sender);
    const info = rawInfo && typeof rawInfo === 'object' ? rawInfo : {};
    const session = {
      sender,
      senderId: sender.id,
      owner,
      sourceWindowBounds: owner.getBounds(),
      descriptor,
      info,
      startedAt: Date.now(),
      lastRendererPoint: null,
      lastRendererVector: null,
      lastCursorPoint: this.screen.getCursorScreenPoint(),
      lastCursorVector: null,
      boundaryExitIntent: false,
      creating: false,
      released: false,
      releasePoint: null,
      detachedWindow: null,
      detachedRecord: null,
      windowReady: false,
      timer: null,
    };
    session.timer = setInterval(() => {
      if (sender.isDestroyed() || owner.isDestroyed()) {
        this.clearDetachedPaneDragSession(session.senderId);
        return;
      }
      if (Date.now() - session.startedAt > 30000) {
        this.clearDetachedPaneDragSession(session.senderId);
        return;
      }
      const cursorPoint = this.updateDetachedPaneCursor(session);
      if (
        this.pointInsideBounds(session.sourceWindowBounds, cursorPoint)
        && !session.boundaryExitIntent
        && !this.pointAtBlockedDisplayEdge(session.sourceWindowBounds, cursorPoint)
      ) {
        return;
      }
      // Renderer pointer capture is authoritative; this cursor check is only a
      // fallback for a dropped move event. A latched edge push also counts when
      // the source fills the display and no outside cursor coordinate exists.
      this.startDetachedPaneCreation(session, cursorPoint);
    }, 32);
    if (session.timer && typeof session.timer.unref === 'function') session.timer.unref();
    this.detachedPaneDragSessions.set(sender.id, session);
    this.debugDetachedPane('pointer capture session began', {
      senderId: sender.id,
      cardId: String(info.cardId || ''),
      sourceWindowBounds: session.sourceWindowBounds,
    });
    return { ok: true };
  }

  detachBrowserSurface(record) {
    const descriptor = record && record.descriptor;
    if (!descriptor || descriptor.kind !== 'browser') return;
    const sessionId = this.normalizeBrowserSessionId(descriptor.ownerChatId);
    if (!sessionId) return;
    this.detachedBrowserSurfaceWindows.set(sessionId, record.window);
    const manager = this.browserSessions.browserTabManagers.get(sessionId);
    if (manager) {
      manager.syncAttachedView();
      manager.emitState();
    }
  }

  restoreBrowserSurface(record) {
    const descriptor = record && record.descriptor;
    if (!descriptor || descriptor.kind !== 'browser') return;
    const sessionId = this.normalizeBrowserSessionId(descriptor.ownerChatId);
    if (this.detachedBrowserSurfaceWindows.get(sessionId) !== record.window) return;
    this.detachedBrowserSurfaceWindows.delete(sessionId);
    const manager = this.browserSessions.browserTabManagers.get(sessionId);
    if (manager) {
      manager.setBounds({ visible: false });
      manager.syncAttachedView();
      manager.emitState();
    }
  }

  async createDetachedPaneWindow(rawDescriptor, info = {}) {
    const descriptor = this.normalizeDetachedPaneDescriptor(rawDescriptor);
    const port = await this.waitForPort();
    if (!port) throw new Error('Cyrene backend is unavailable.');
    const id = crypto.randomUUID();
    const bounds = this.detachedPaneBounds(info);
    const win = new this.BrowserWindow({
      ...bounds,
      minWidth: 420,
      minHeight: 320,
      title: descriptor.title || 'Cyrene',
      show: false,
      frame: false,
      resizable: true,
      maximizable: true,
      fullscreenable: true,
      backgroundColor: '#111418',
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false,
      },
    });
    this.debugDetachedPane('BrowserWindow constructed', { id, bounds, dragging: !!info.dragSession });
    const dragSession = info.dragSession && typeof info.dragSession === 'object'
      ? info.dragSession
      : null;
    let resolveReady;
    const readyPromise = new Promise((resolve) => { resolveReady = resolve; });
    const record = {
      id,
      window: win,
      descriptor,
      resolveReady,
      sourceWindow: info.sourceWindow || null,
      sourceSenderId: Number(info.sourceSenderId) || 0,
      sourceInfo: {
        cardId: String(info.cardId || ''),
        layoutOwnerChatId: String(info.layoutOwnerChatId || ''),
        sourceSide: info.sourceSide === 'right' ? 'right' : 'left',
        sourceIndex: Math.max(0, Number(info.sourceIndex) || 0),
      },
      returnDrag: null,
      returning: false,
    };
    this.detachedPaneWindows.set(id, record);
    if (dragSession) {
      dragSession.detachedWindow = win;
      dragSession.detachedRecord = record;
      try { win.setAlwaysOnTop(true, 'floating'); } catch (_) {}
      try { win.setIgnoreMouseEvents(true); } catch (_) {}
    }
    win.on('closed', () => {
      this.restoreBrowserSurface(record);
      this.detachedPaneWindows.delete(id);
      if (this.getMainWindow() && !this.getMainWindow().isDestroyed()) {
        try { this.getMainWindow().webContents.send('detached-pane:closed', { id, descriptor }); } catch (_) {}
      }
    });
    this.installLocalNavigationGuards(win, port);
    await win.loadURL(
      `http://127.0.0.1:${port}/?surface=detached-pane&paneWindowId=${encodeURIComponent(id)}`,
      { extraHeaders: `X-Cyrene-Token: ${this.AUTH_TOKEN}\n` },
    );
    const ready = await Promise.race([
      readyPromise.then(() => true),
      new Promise((resolve) => setTimeout(() => resolve(false), 8000)),
    ]);
    if (!ready || win.isDestroyed()) {
      if (!win.isDestroyed()) win.destroy();
      throw new Error('The detached pane did not become ready.');
    }
    this.detachBrowserSurface(record);
    if (dragSession) {
      dragSession.windowReady = true;
      if (dragSession.lastCursorPoint) {
        win.setBounds(this.detachedPaneBounds({
          ...info,
          dropPoint: dragSession.lastCursorPoint,
        }), false);
      }
      if (dragSession.released) {
        try { win.setIgnoreMouseEvents(false); } catch (_) {}
        try { win.setAlwaysOnTop(false); } catch (_) {}
        win.show();
        win.focus();
      } else {
        win.showInactive();
        // A lost pointerup must never leave the child permanently click-through.
        dragSession.releaseTimer = setTimeout(() => {
          if (win.isDestroyed()) return;
          this.finishDetachedPaneDragSession(dragSession, dragSession.lastCursorPoint);
        }, 220);
      }
    } else {
      win.show();
      win.focus();
    }
    return { ok: true, detached: true, id, bounds: win.getBounds() };
  }

  closeDetachedPanesForChat(chatId) {
    const normalized = String(chatId || '');
    let closed = 0;
    for (const record of Array.from(this.detachedPaneWindows.values())) {
      const descriptor = record.descriptor || {};
      const payloadChatId = descriptor.kind === 'chat' ? String(descriptor.payload || '') : '';
      if (String(descriptor.ownerChatId || '') !== normalized && payloadChatId !== normalized) continue;
      if (record.window && !record.window.isDestroyed()) {
        record.window.close();
        closed += 1;
      }
    }
    return { ok: true, closed };
  }

  detachedPaneReturnBounds(record, point) {
    const win = record && record.window;
    if (!win || win.isDestroyed()) return null;
    const current = win.getBounds();
    const grab = record.returnDrag && record.returnDrag.grab || {
      x: current.width / 2,
      y: 24,
    };
    return {
      x: Math.round(point.x - Math.max(0, Math.min(current.width, Number(grab.x) || 0))),
      y: Math.round(point.y - Math.max(0, Math.min(current.height, Number(grab.y) || 0))),
      width: current.width,
      height: current.height,
    };
  }

  beginDetachedPaneReturnDrag(sender, info = {}) {
    const record = this.detachedPaneContextForSender(sender);
    if (!record || !record.window || record.window.isDestroyed()) return { ok: false };
    record.returnDrag = {
      grab: info.grab && typeof info.grab === 'object' ? info.grab : { x: 190, y: 24 },
      merge: false,
    };
    try { record.window.setAlwaysOnTop(true, 'floating'); } catch (_) {}
    record.window.moveTop();
    return { ok: true };
  }

  updateDetachedPaneReturnDrag(sender, rawPoint) {
    const record = this.detachedPaneContextForSender(sender);
    if (!record || !record.returnDrag || !record.window || record.window.isDestroyed()) return;
    const point = rawPoint && typeof rawPoint === 'object' ? rawPoint : {};
    const x = Number(point.screenX != null ? point.screenX : point.x);
    const y = Number(point.screenY != null ? point.screenY : point.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    const screenPoint = { x: Math.round(x), y: Math.round(y) };
    const bounds = this.detachedPaneReturnBounds(record, screenPoint);
    if (bounds) record.window.setBounds(bounds, false);
    const source = record.sourceWindow;
    const merge = !!(source && !source.isDestroyed() && this.pointInsideBounds(source.getBounds(), screenPoint));
    if (merge === record.returnDrag.merge) return;
    record.returnDrag.merge = merge;
    try { record.window.webContents.send('detached-pane:return-hover', { active: merge }); } catch (_) {}
    if (source && !source.isDestroyed()) {
      try { source.webContents.send('detached-pane:return-hover', { active: merge }); } catch (_) {}
    }
  }

  finishDetachedPaneReturnDrag(sender, rawPoint) {
    const record = this.detachedPaneContextForSender(sender);
    if (!record || !record.returnDrag || !record.window || record.window.isDestroyed()) {
      return { ok: false };
    }
    this.updateDetachedPaneReturnDrag(sender, rawPoint || this.screen.getCursorScreenPoint());
    const merge = !!record.returnDrag.merge;
    record.returnDrag = null;
    const source = record.sourceWindow;
    try { record.window.webContents.send('detached-pane:return-hover', { active: false }); } catch (_) {}
    if (source && !source.isDestroyed()) {
      try { source.webContents.send('detached-pane:return-hover', { active: false }); } catch (_) {}
    }
    if (!merge || !source || source.isDestroyed()) {
      try { record.window.setAlwaysOnTop(false); } catch (_) {}
      return { ok: true, merged: false };
    }
    record.returning = true;
    try {
      source.webContents.send('detached-pane:returned', {
        id: record.id,
        descriptor: record.descriptor,
        ...record.sourceInfo,
      });
    } catch (_) {}
    record.window.destroy();
    source.show();
    source.focus();
    return { ok: true, merged: true };
  }
}

module.exports = { DetachedPanes };
