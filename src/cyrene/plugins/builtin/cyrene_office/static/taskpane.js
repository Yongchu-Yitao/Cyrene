/* global Office, PowerPoint */
"use strict";
{

  const token = document.querySelector('meta[name="cyrene-office-token"]').content;
  const agentKit = {
    protocolVersion: Number("__CYRENE_OFFICE_PROTOCOL_VERSION__"),
    kitVersion: "__CYRENE_OFFICE_KIT_VERSION__",
    schemaHash: "__CYRENE_OFFICE_SCHEMA_HASH__",
    buildHash: "__CYRENE_OFFICE_BUILD_HASH__",
  };
  const appearanceMedia = window.matchMedia("(prefers-color-scheme: dark)");
  let appearanceSignature = "";
  let configuredLanguage = "";
  let activeLanguage = "zh";
  let currentStatus = {
    kind: "pending",
    titleKey: "statusConnecting",
    detailKey: "statusWaitingForPowerPoint",
    rawDetail: "",
  };
  const translations = {
    zh: {
      pageTitle: "Cyrene PowerPoint 实时连接",
      liveBadge: "实时",
      liveConnection: "实时连接",
      currentPresentation: "当前演示文稿",
      untitledPresentation: "未命名演示文稿",
      revision: "修订",
      detecting: "检测中",
      unsupported: "不支持",
      readyTitle: "现在可以开始了",
      stepKeepOpen: "保持当前演示文稿和此任务窗格打开",
      stepAskCyrene: "回到 Cyrene，直接描述你想新增或修改的内容",
      stepWatchChanges: "Agent 的每批编辑都会立即显示在幻灯片上",
      reconnect: "重新连接",
      localOnly: "连接仅在此设备本地运行",
      statusConnecting: "正在连接…",
      statusWaitingForPowerPoint: "等待 PowerPoint 初始化",
      statusConnectingGateway: "正在连接本机 Cyrene Gateway",
      statusConnected: "已连接",
      statusConnectedDetail: "Cyrene 可以实时编辑此演示文稿",
      statusConnectionFailed: "连接失败",
      statusConnectionFailedDetail: "请确认 Cyrene 正在运行且本地证书已信任",
      statusDisconnected: "连接已断开",
      statusDisconnectedDetail: "Cyrene Gateway 不可用，正在重试…",
      statusUnsupportedHost: "宿主不支持",
      statusUnsupportedHostDetail: "请在 Microsoft PowerPoint 中打开此加载项",
      statusInitializationFailed: "PowerPoint 初始化失败",
      statusAddinOutdated: "加载项需要刷新",
    },
    en: {
      pageTitle: "Cyrene Live PowerPoint",
      liveBadge: "LIVE",
      liveConnection: "Live connection",
      currentPresentation: "Current presentation",
      untitledPresentation: "Untitled presentation",
      revision: "Revision",
      detecting: "Detecting",
      unsupported: "Unsupported",
      readyTitle: "You're ready to begin",
      stepKeepOpen: "Keep this presentation and task pane open",
      stepAskCyrene: "Return to Cyrene and describe what you want to add or change",
      stepWatchChanges: "Each Agent edit appears on the slide as soon as it is applied",
      reconnect: "Reconnect",
      localOnly: "The connection runs locally on this device",
      statusConnecting: "Connecting…",
      statusWaitingForPowerPoint: "Waiting for PowerPoint to initialize",
      statusConnectingGateway: "Connecting to the local Cyrene Gateway",
      statusConnected: "Connected",
      statusConnectedDetail: "Cyrene can edit this presentation in real time",
      statusConnectionFailed: "Connection failed",
      statusConnectionFailedDetail: "Make sure Cyrene is running and the local certificate is trusted",
      statusDisconnected: "Disconnected",
      statusDisconnectedDetail: "Cyrene Gateway is unavailable. Retrying…",
      statusUnsupportedHost: "Unsupported host",
      statusUnsupportedHostDetail: "Open this add-in in Microsoft PowerPoint",
      statusInitializationFailed: "PowerPoint initialization failed",
      statusAddinOutdated: "Add-in refresh required",
    },
  };
  const state = {
    socket: null,
    reconnectTimer: null,
    reconnectDelay: 500,
    revision: Number(sessionStorage.getItem("cyrene.ppt.revision") || 0),
    sessionId: sessionStorage.getItem("cyrene.ppt.session") || "",
    idempotency: new Map(),
    undo: new Map(),
    capabilities: {},
    capabilitiesReady: false,
    document: {},
    shapeRefs: new Map(),
    selectedSlideId: "",
    slideIds: [],
    slideRenders: new Map(),
    slideSignatures: new Map(),
    mutationInFlight: false,
    mutationQueue: Promise.resolve(),
  };
  try { state.idempotency = new Map(JSON.parse(sessionStorage.getItem("cyrene.ppt.idempotency") || "[]")); }
  catch (_) { state.idempotency = new Map(); }

  const $ = (id) => document.getElementById(id);
  function validColor(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || ""));
  }
  function resolveTheme(mode) {
    return mode === "light" || mode === "dark" ? mode : (appearanceMedia.matches ? "dark" : "light");
  }
  function normalizeLanguage(value) {
    return String(value || "").toLowerCase().startsWith("zh") ? "zh" : "en";
  }
  function t(key) {
    return translations[activeLanguage][key] || translations.en[key] || key;
  }
  function renderStatus() {
    $("status-dot").className = "status-indicator " + currentStatus.kind;
    $("status-title").textContent = t(currentStatus.titleKey);
    $("status-detail").textContent = currentStatus.detailKey
      ? t(currentStatus.detailKey)
      : currentStatus.rawDetail;
  }
  function translateStatic() {
    document.documentElement.lang = activeLanguage === "zh" ? "zh-CN" : "en";
    document.title = t("pageTitle");
    document.querySelectorAll("[data-i18n]").forEach(function (element) {
      element.textContent = t(element.dataset.i18n);
    });
    renderStatus();
    updateMetrics();
  }
  function applyLanguage(value) {
    const nextLanguage = normalizeLanguage(value);
    if (nextLanguage === activeLanguage && document.documentElement.dataset.languageReady === "1") return;
    activeLanguage = nextLanguage;
    document.documentElement.dataset.languageReady = "1";
    translateStatic();
  }
  function applyAppearance(values) {
    const mode = String(values.theme || "system");
    const theme = resolveTheme(mode);
    const accent = String(values.accent || "");
    const background = String(values[theme === "dark" ? "background_dark" : "background_light"] || "");
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.themeMode = mode;
    document.documentElement.style.setProperty("--wb-ui-font-scale", values.text_size === "large" ? "1.08" : "1");
    if (validColor(accent)) document.documentElement.style.setProperty("--accent", accent);
    else document.documentElement.style.removeProperty("--accent");
    if (validColor(background)) document.documentElement.style.setProperty("--cyrene-office-bg", background);
    else document.documentElement.style.removeProperty("--cyrene-office-bg");
    configuredLanguage = String(values.language || "");
    const officeLanguage = typeof Office !== "undefined" && Office.context
      ? Office.context.displayLanguage
      : "";
    applyLanguage(configuredLanguage || officeLanguage || navigator.language);
  }
  async function syncAppearance() {
    try {
      const response = await fetch("/appearance?token=" + encodeURIComponent(token), { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const values = payload.values || {};
      const signature = JSON.stringify(values);
      if (signature === appearanceSignature) return;
      appearanceSignature = signature;
      applyAppearance(values);
    } catch (_) { /* Keep the shared CSS defaults when appearance sync is unavailable. */ }
  }
  function setStatus(kind, titleKey, detailKey, rawDetail) {
    currentStatus = { kind: kind, titleKey: titleKey, detailKey: detailKey || "", rawDetail: rawDetail || "" };
    renderStatus();
  }
  function updateMetrics() {
    $("revision").textContent = String(state.revision);
    $("document-name").textContent = state.document.name || t("untitledPresentation");
    $("api-version").textContent = !state.capabilitiesReady
      ? t("detecting")
      : (state.capabilities.powerPointApi18 ? "1.8+" : (state.capabilities.powerPointApi14 ? "1.4+" : t("unsupported")));
  }
  function documentName(title, url) {
    const location = String(url || "").trim();
    if (location) {
      const rawName = location.split(/[\\/]/).pop() || "";
      try { return decodeURIComponent(rawName) || t("untitledPresentation"); }
      catch (_) { return rawName || t("untitledPresentation"); }
    }
    const candidate = String(title || "").trim();
    if (candidate && !/^title property in /i.test(candidate)) return candidate;
    return t("untitledPresentation");
  }
  function hasApi(version) {
    try { return Office.context.requirements.isSetSupported("PowerPointApi", version); }
    catch (_) { return false; }
  }
  function requireApi(version, feature) {
    if (!hasApi(version)) {
      const error = new Error(feature + " requires PowerPointApi " + version + ".");
      error.code = "unsupported_requirement_set";
      throw error;
    }
  }
  function asError(error) {
    const debug = error && error.debugInfo;
    return {
      code: String((error && error.code) || (debug && debug.code) || "office_error"),
      message: String((error && error.message) || error || "Unknown PowerPoint error"),
      details: (error && error.details) || debug || undefined,
    };
  }
  function makeId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }
  function socketUrl() {
    return "wss://" + window.location.host + "/ws?token=" + encodeURIComponent(token);
  }
  function persistRevision() {
    sessionStorage.setItem("cyrene.ppt.revision", String(state.revision));
    updateMetrics();
  }
  function rememberIdempotent(key, result) {
    if (!key) return;
    state.idempotency.delete(key);
    state.idempotency.set(key, result);
    while (state.idempotency.size > 60) state.idempotency.delete(state.idempotency.keys().next().value);
    sessionStorage.setItem("cyrene.ppt.idempotency", JSON.stringify(Array.from(state.idempotency.entries())));
  }
  function forgetSlideIdempotency(slideId) {
    const target = String(slideId || "");
    if (!target) return;
    let changed = false;
    for (const [key, result] of state.idempotency.entries()) {
      const createdSlideIds = ((result && result.created) || [])
        .map((item) => String((item && item.slideId) || ""));
      if (String((result && result.slideId) || "") === target || createdSlideIds.includes(target)) {
        state.idempotency.delete(key);
        changed = true;
      }
    }
    if (changed) {
      sessionStorage.setItem("cyrene.ppt.idempotency", JSON.stringify(Array.from(state.idempotency.entries())));
    }
  }
  function changed(result) {
    state.revision += 1;
    result.status = "applied";
    result.revision = state.revision;
    result.changed = result.changed || [];
    result.created = result.created || [];
    result.deleted = result.deleted || [];
    result.warnings = result.warnings || [];
    result.undoToken = result.undoToken || null;
    result.renderId = result.renderId || null;
    persistRevision();
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
      state.socket.send(JSON.stringify({ type: "event", event: "revision_changed", revision: state.revision, document: state.document }));
    }
    return result;
  }
  function checkMutation(params) {
    const expected = params.expectedRevision;
    const idempotencyKey = params.idempotencyKey;
    if (!idempotencyKey) {
      const error = new Error("idempotencyKey is required for mutations.");
      error.code = "idempotency_required";
      throw error;
    }
    const replay = state.idempotency.get(idempotencyKey) || null;
    if (replay) return replay;
    if (!Number.isInteger(expected)) {
      const error = new Error("expectedRevision is required for mutations.");
      error.code = "revision_required";
      throw error;
    }
    if (expected !== state.revision) {
      const error = new Error("Presentation revision changed: expected " + expected + ", current " + state.revision + ". Inspect again before editing.");
      error.code = "revision_conflict";
      throw error;
    }
    return null;
  }

  function invalidateMutationSignatures(result) {
    const slideIds = new Set();
    if (state.selectedSlideId) slideIds.add(String(state.selectedSlideId));
    if (result && result.slideId) slideIds.add(String(result.slideId));
    for (const item of (result && result.created) || []) {
      if (item && item.slideId) slideIds.add(String(item.slideId));
    }
    for (const slideId of (result && result.deleted) || []) {
      const deletedSlideId = String(slideId);
      slideIds.add(deletedSlideId);
      state.shapeRefs.delete(deletedSlideId);
    }
    for (const slideId of slideIds) {
      state.slideSignatures.delete(slideId);
    }
  }

  async function readDocumentInfo() {
    return PowerPoint.run(async (context) => {
      const presentation = context.presentation;
      presentation.load("title");
      await context.sync();
      const url = Office.context.document.url || "";
      state.document = {
        name: documentName(presentation.title, url),
        url: url,
      };
      updateMetrics();
      return state.document;
    });
  }

  function connect() {
    clearTimeout(state.reconnectTimer);
    if (state.socket) {
      state.socket.onclose = null;
      try { state.socket.close(); } catch (_) { /* noop */ }
    }
    setStatus("pending", "statusConnecting", "statusConnectingGateway");
    const socket = new WebSocket(socketUrl());
    state.socket = socket;
    socket.onopen = function () {
      state.reconnectDelay = 500;
      socket.send(JSON.stringify({
        type: "hello",
        host: "powerpoint",
        resumeSessionId: state.sessionId,
        revision: state.revision,
        document: state.document,
        capabilities: state.capabilities,
        protocolVersion: agentKit.protocolVersion,
        kitVersion: agentKit.kitVersion,
        schemaHash: agentKit.schemaHash,
        buildHash: agentKit.buildHash,
      }));
    };
    socket.onmessage = async function (event) {
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === "hello_ack") {
        state.sessionId = message.sessionId;
        sessionStorage.setItem("cyrene.ppt.session", state.sessionId);
        if (message.compatible === false) {
          const expected = message.agentKit && message.agentKit.expected;
          const expectedBuild = expected && expected.buildHash;
          const reloadKey = "cyrene.ppt.reload." + String(expectedBuild || "unknown");
          if (sessionStorage.getItem(reloadKey) !== "1") {
            sessionStorage.setItem(reloadKey, "1");
            const url = new URL(window.location.href);
            url.searchParams.set("build", String(expectedBuild || Date.now()));
            window.location.replace(url.toString());
            return;
          }
          setStatus("error", "statusAddinOutdated", "", (message.error && message.error.message) || "Reinstall the PowerPoint add-in from Cyrene settings.");
          socket.onclose = null;
          socket.close();
          return;
        }
        setStatus("connected", "statusConnected", "statusConnectedDetail");
        return;
      }
      if (message.type !== "request") return;
      const handler = handlers[message.method];
      const isMutation = MUTATION_METHODS.has(message.method);
      const executeRequest = async function () {
        if (isMutation) state.mutationInFlight = true;
        try {
          if (!handler) {
            const error = new Error("Unknown method: " + message.method);
            error.code = "unknown_method";
            throw error;
          }
          const result = await handler(message.params || {});
          if (isMutation) invalidateMutationSignatures(result);
          socket.send(JSON.stringify({ type: "response", id: message.id, ok: true, result: result }));
        } catch (error) {
          if (isMutation) invalidateMutationSignatures({});
          socket.send(JSON.stringify({ type: "response", id: message.id, ok: false, error: asError(error) }));
        } finally {
          if (isMutation) state.mutationInFlight = false;
        }
      };
      if (!isMutation) {
        await executeRequest();
        return;
      }
      const queuedMutation = state.mutationQueue.then(executeRequest, executeRequest);
      state.mutationQueue = queuedMutation.catch(function () { /* Keep later mutations runnable. */ });
      await queuedMutation;
    };
    socket.onerror = function () { setStatus("error", "statusConnectionFailed", "statusConnectionFailedDetail"); };
    socket.onclose = function () {
      setStatus("pending", "statusDisconnected", "statusDisconnectedDetail");
      state.reconnectTimer = setTimeout(connect, state.reconnectDelay);
      state.reconnectDelay = Math.min(10000, state.reconnectDelay * 1.8);
    };
  }

  function getSlide(context, params) {
    const slideId = params.slideId;
    const slideIndex = params.slideIndex;
    if (slideId) return context.presentation.slides.getItem(slideId);
    if (Number.isInteger(slideIndex)) return context.presentation.slides.getItemAt(slideIndex);
    return context.presentation.getSelectedSlides().getItemAt(0);
  }
  async function focusSlide(context, slideId) {
    const target = String(slideId || "");
    if (!target) return;
    if (state.selectedSlideId === target) return;
    context.presentation.setSelectedSlides([target]);
    await context.sync();
    state.selectedSlideId = target;
  }
  async function livePreviewTick() {
    await new Promise((resolve) => window.setTimeout(resolve, 16));
  }
  function mayHaveText(type) {
    return ["textbox", "geometricshape", "placeholder", "callout"].includes(String(type || "").toLowerCase());
  }
  function shapeData(shape, text) {
    return {
      id: shape.id,
      ref: String(shape.name || "").startsWith("cyrene:") ? String(shape.name).slice(7) : undefined,
      name: shape.name,
      type: shape.type,
      x: shape.left,
      y: shape.top,
      width: shape.width,
      height: shape.height,
      rotation: shape.rotation,
      z_order: hasApi("1.8") ? shape.zOrderPosition : undefined,
      text: text,
    };
  }
  function slideRefMap(slideId, reset) {
    const key = String(slideId || "");
    if (!key) return new Map();
    if (reset || !state.shapeRefs.has(key)) state.shapeRefs.set(key, new Map());
    return state.shapeRefs.get(key);
  }
  function rememberShapeRef(refs, shapeId, name, ref) {
    refs.set(shapeId, shapeId);
    if (name) refs.set(name, shapeId);
    if (ref) refs.set(ref, shapeId);
  }
  function forgetShapeRef(refs, target) {
    const shapeId = refs.get(target) || target;
    Array.from(refs.entries()).forEach(([key, value]) => {
      if (key === target || value === shapeId) refs.delete(key);
    });
  }
  function requestedSlideId(params) {
    const index = params.slideIndex;
    return params.slideId || (Number.isInteger(index) ? state.slideIds[index] : "") || state.selectedSlideId || "";
  }
  async function loadShapes(context, slide, includeText) {
    const shapes = slide.shapes;
    let properties = "items/id,items/name,items/type,items/left,items/top,items/width,items/height,items/rotation";
    if (hasApi("1.8")) properties += ",items/zOrderPosition";
    shapes.load(properties);
    await context.sync();
    const textItems = [];
    if (includeText) {
      for (const shape of shapes.items) {
        if (mayHaveText(shape.type)) {
          shape.textFrame.load("hasText,textRange/text");
          textItems.push(shape);
        }
      }
      if (textItems.length) await context.sync();
    }
    const texts = new Map(textItems.map((shape) => [shape.id, shape.textFrame.hasText ? shape.textFrame.textRange.text : ""]));
    const data = shapes.items.map((shape) => shapeData(shape, texts.get(shape.id)));
    const refs = slideRefMap(slide.id, true);
    data.forEach((item) => {
      rememberShapeRef(refs, item.id, item.name, item.ref);
    });
    return { collection: shapes, items: shapes.items, data: data };
  }
  function targetMap(items) {
    const map = new Map();
    items.forEach((shape) => {
      map.set(shape.id, shape);
      map.set(shape.name, shape);
      if (String(shape.name || "").startsWith("cyrene:")) map.set(String(shape.name).slice(7), shape);
    });
    return map;
  }
  function requireTarget(map, target) {
    const shape = map.get(target);
    if (!shape) {
      const error = new Error("Shape target not found: " + target);
      error.code = "shape_not_found";
      throw error;
    }
    return shape;
  }
  function bounds(op) {
    const result = {};
    if (Number.isFinite(op.x)) result.left = op.x;
    if (Number.isFinite(op.y)) result.top = op.y;
    if (Number.isFinite(op.width)) result.width = op.width;
    if (Number.isFinite(op.height)) result.height = op.height;
    return result;
  }

  function insertSelectedImage(imageBase64, box) {
    const payload = stripDataUrl(imageBase64);
    if (!payload) throw Object.assign(new Error("Image payload is empty."), { code: "invalid_image" });
    if (!Office.context.requirements.isSetSupported("ImageCoercion", "1.1")) {
      throw Object.assign(new Error("This PowerPoint host does not support ImageCoercion 1.1."), { code: "capability_unavailable" });
    }
    return new Promise((resolve, reject) => {
      Office.context.document.setSelectedDataAsync(payload, {
        coercionType: Office.CoercionType.Image,
        imageLeft: box.left,
        imageTop: box.top,
        imageWidth: box.width,
        imageHeight: box.height,
      }, (result) => {
        if (result.status === Office.AsyncResultStatus.Failed) {
          reject(Object.assign(new Error(result.error && result.error.message || "PowerPoint could not insert the image."), { code: "image_insert_failed" }));
          return;
        }
        resolve();
      });
    });
  }
  async function addImageElement(context, slide, element, created) {
    slide.load("id");
    const shapes = slide.shapes;
    shapes.load("items/id");
    await context.sync();
    context.presentation.setSelectedSlides([slide.id]);
    await context.sync();
    const knownIds = new Set(shapes.items.map((shape) => shape.id));
    const box = Array.isArray(element.box) ? {
      left: element.box[0], top: element.box[1], width: element.box[2], height: element.box[3],
    } : bounds(element);
    await insertSelectedImage(element.imageBase64, box);
    shapes.load("items/id,items/name,items/type");
    await context.sync();
    const inserted = shapes.items.filter((item) => !knownIds.has(item.id));
    const shape = inserted[inserted.length - 1];
    if (!shape) throw Object.assign(new Error("PowerPoint inserted the image but did not return its shape."), { code: "image_shape_not_found" });
    const ref = element.ref || "image-" + (created.length + 1);
    shape.name = "cyrene:" + ref;
    positionShape(shape, element);
    styleShape(shape, element.style);
    shape.load("id,name,type");
    created.push({ proxy: shape, ref: ref });
    return shape;
  }
  function styleShape(shape, style) {
    if (!style) return;
    const fillColor = style.fillColor;
    const fillTransparency = style.fillTransparency;
    const lineColor = style.lineColor;
    const lineWeight = style.lineWeight;
    const lineTransparency = style.lineTransparency;
    const fontName = style.fontName;
    const fontSize = style.fontSize;
    const fontColor = style.fontColor;
    if (fillColor) shape.fill.setSolidColor(fillColor);
    if (Number.isFinite(fillTransparency)) shape.fill.transparency = fillTransparency;
    if (lineColor) shape.lineFormat.color = lineColor;
    if (Number.isFinite(lineWeight)) shape.lineFormat.weight = lineWeight;
    if (Number.isFinite(lineTransparency)) shape.lineFormat.transparency = lineTransparency;
    if (fontName) shape.textFrame.textRange.font.name = fontName;
    if (Number.isFinite(fontSize)) shape.textFrame.textRange.font.size = fontSize;
    if (fontColor) shape.textFrame.textRange.font.color = fontColor;
    if (typeof style.bold === "boolean") shape.textFrame.textRange.font.bold = style.bold;
    if (typeof style.italic === "boolean") shape.textFrame.textRange.font.italic = style.italic;
    if (style.horizontalAlignment) shape.textFrame.textRange.paragraphFormat.horizontalAlignment = style.horizontalAlignment;
    if (style.verticalAlignment) shape.textFrame.verticalAlignment = style.verticalAlignment;
    if (typeof style.wordWrap === "boolean") shape.textFrame.wordWrap = style.wordWrap;
  }
  function positionShape(shape, op) {
    if (Number.isFinite(op.x)) shape.left = op.x;
    if (Number.isFinite(op.y)) shape.top = op.y;
    if (Number.isFinite(op.width)) shape.width = op.width;
    if (Number.isFinite(op.height)) shape.height = op.height;
    if (Number.isFinite(op.rotation)) shape.rotation = op.rotation;
  }
  function stripDataUrl(value) {
    return String(value || "").replace(/^data:[^;]+;base64,/, "");
  }
  function newName(op) {
    return op.ref ? "cyrene:" + op.ref : (op.name || "");
  }

  async function getContext() {
    return PowerPoint.run(async (context) => {
      const slides = context.presentation.slides;
      const selectedSlides = context.presentation.getSelectedSlides();
      const selectedShapes = context.presentation.getSelectedShapes();
      slides.load("items/id");
      selectedSlides.load("items/id");
      selectedShapes.load("items/id,items/name,items/type");
      const pageSetup = hasApi("1.10") ? context.presentation.pageSetup : null;
      if (pageSetup) pageSetup.load("slideWidth,slideHeight");
      await context.sync();
      const selectedSlideIds = selectedSlides.items.map((slide) => slide.id);
      state.slideIds = slides.items.map((slide) => slide.id);
      state.selectedSlideId = selectedSlideIds[0] || "";
      const selectedShapeItems = selectedShapes.items.map((shape) => ({ id: shape.id, name: shape.name, type: shape.type }));
      const selectedRefs = slideRefMap(state.selectedSlideId, false);
      selectedShapeItems.forEach((shape) => {
        rememberShapeRef(selectedRefs, shape.id, shape.name, String(shape.name || "").startsWith("cyrene:") ? String(shape.name).slice(7) : "");
      });
      return {
        status: "success",
        mode: "live_office",
        sessionId: state.sessionId,
        documentId: state.document.url || state.document.name,
        revision: state.revision,
        document: state.document,
        slideCount: slides.items.length,
        selectedSlides: selectedSlideIds,
        selectedShapes: selectedShapeItems.map((shape) => shape.id),
        selection: { slideIds: selectedSlideIds, shapes: selectedShapeItems },
        pageSize: pageSetup ? { width: pageSetup.slideWidth, height: pageSetup.slideHeight } : null,
        capabilities: state.capabilities,
      };
    });
  }
  async function listSlides() {
    return PowerPoint.run(async (context) => {
      const slides = context.presentation.slides;
      const selected = context.presentation.getSelectedSlides();
      slides.load("items/id");
      selected.load("items/id");
      await context.sync();
      state.slideIds = slides.items.map((slide) => slide.id);
      const selectedIds = new Set(selected.items.map((item) => item.id));
      return { status: "success", revision: state.revision, slides: slides.items.map((slide, index) => ({ id: slide.id, index: index, selected: selectedIds.has(slide.id) })) };
    });
  }
  async function listShapes(params) {
    return PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const loaded = await loadShapes(context, slide, params.includeText !== false);
      const requested = params.shapeRef;
      const shapes = requested ? loaded.data.filter((shape) => [shape.id, shape.name, shape.ref].includes(requested)) : loaded.data;
      return { status: "success", revision: state.revision, slideId: slide.id, shapes: shapes, shape: requested ? (shapes[0] || null) : undefined };
    });
  }

  async function slideContentSignature(slideId) {
    if (!slideId) return "";
    const result = await listShapes({ slideId: slideId, includeText: true });
    return JSON.stringify(result.shapes);
  }

  async function rememberSlideSignature(slideId) {
    if (slideId) state.slideSignatures.set(slideId, await slideContentSignature(slideId));
  }
  async function getSlideStructure(params) {
    const result = await listShapes(Object.assign({}, params, { includeText: params.includeText !== false }));
    return {
      status: "success",
      revision: result.revision,
      slide: { id: result.slideId, shapes: result.shapes },
    };
  }
  async function getShape(params) {
    const requested = params.shapeRef;
    if (!requested) throw Object.assign(new Error("shapeRef is required."), { code: "shape_ref_required" });
    const result = await listShapes(Object.assign({}, params, { shapeRef: requested, includeText: params.includeText !== false }));
    if (!result.shape) throw Object.assign(new Error("Shape target not found: " + requested), { code: "shape_not_found" });
    return { status: "success", revision: result.revision, slideId: result.slideId, shape: result.shape };
  }
  async function readText(params) {
    params.includeText = true;
    const result = await listShapes(params);
    result.text = result.shapes.filter((shape) => shape.text).map((shape) => ({ id: shape.id, ref: shape.ref, name: shape.name, text: shape.text }));
    delete result.shapes;
    return result;
  }

  async function inspect(params) {
    const scope = params.scope || "selection";
    if (scope === "presentation") {
      const contextResult = await getContext();
      const slideResult = await listSlides();
      return Object.assign(contextResult, { slides: slideResult.slides });
    }
    if (scope === "selection") return getContext();
    return listShapes(Object.assign({}, params, { includeText: params.includeText !== false }));
  }

  async function applyBatchOperation(context, slide, op, batch) {
    const operationType = op.op;
    const target = op.shapeRef || op.target;
    let shape;
    if (operationType === "set_background") {
      requireApi("1.10", "Slide background editing");
      slide.background.fill.setSolidFill({ color: String(op.color || op.background || "#FFFFFF"), transparency: 0 });
      batch.changed.push("slide-background");
      return;
    } else if (operationType === "add_textbox") {
      shape = slide.shapes.addTextBox(String(op.text || ""), bounds(op));
    } else if (operationType === "add_shape") {
      shape = slide.shapes.addGeometricShape(op.geometry || "Rectangle", bounds(op));
      if (op.text !== undefined) shape.textFrame.textRange.text = String(op.text);
    } else if (operationType === "insert_image") {
      shape = await addImageElement(context, slide, op, batch.created);
      if (op.ref) batch.shapes.set(op.ref, shape);
      return;
    } else if (operationType === "add_line") {
      shape = slide.shapes.addLine(op.connector || "Straight", bounds(op));
    } else if (operationType === "insert_table") {
      if (typeof slide.shapes.addTable !== "function") throw Object.assign(new Error("Tables are unavailable in this PowerPoint host."), { code: "capability_unavailable" });
      const values = op.values || [[""]];
      shape = slide.shapes.addTable(values.length, Math.max(1, values[0].length), Object.assign({}, bounds(op), { values: values }));
    } else if (["update_shape", "move_shape", "resize_shape", "update_text", "apply_style"].includes(operationType)) {
      shape = requireTarget(batch.shapes, target);
      if (op.text !== undefined) shape.textFrame.textRange.text = String(op.text);
    } else if (operationType === "delete_shape") {
      shape = requireTarget(batch.shapes, target);
      batch.deleted.push(target);
      shape.delete();
      batch.shapes.delete(target);
      forgetShapeRef(batch.refs, target);
      return;
    } else if (operationType === "set_z_order") {
      requireApi("1.8", "Changing z-order");
      shape = requireTarget(batch.shapes, target);
      shape.setZOrder(op.position);
    } else if (operationType === "group_shapes") {
      requireApi("1.8", "Grouping shapes");
      shape = slide.shapes.addGroup((op.shapeRefs || op.targets || []).map((item) => requireTarget(batch.shapes, item)));
    } else if (operationType === "ungroup_shapes") {
      requireApi("1.8", "Ungrouping shapes");
      shape = requireTarget(batch.shapes, target);
      shape.group.ungroup();
      batch.changed.push(target);
      return;
    } else {
      throw Object.assign(new Error("Unsupported operation type: " + operationType), { code: "unsupported_operation" });
    }
    const assignedName = newName(op);
    if (assignedName) {
      shape.name = assignedName;
      const persistentId = batch.refs.get(target) || target;
      batch.refs.set(assignedName, persistentId);
      if (assignedName.startsWith("cyrene:")) batch.refs.set(assignedName.slice(7), persistentId);
    }
    positionShape(shape, op);
    styleShape(shape, op.style);
    if (["add_textbox", "add_shape", "add_line", "insert_table", "group_shapes"].includes(operationType)) {
      shape.load("id,name,type");
      batch.created.push({ proxy: shape, ref: op.ref || undefined });
      if (op.ref) batch.shapes.set(op.ref, shape);
    } else {
      batch.changed.push(target);
    }
  }

  function operationGroups(operations, granularity) {
    if (granularity === "element") {
      return operations.map((operation, index) => [{ operation: operation, index: index }]);
    }
    const groups = [];
    let current = [];
    const flush = function () {
      if (current.length) groups.push(current);
      current = [];
    };
    operations.forEach((operation, index) => {
      // Common API image insertion has its own host round trips and selection
      // dependency, so keep it isolated from the dependency-safe JS batch.
      if (operation.op === "insert_image") {
        flush();
        groups.push([{ operation: operation, index: index }]);
        return;
      }
      current.push({ operation: operation, index: index });
      if (current.length >= 32) flush();
    });
    flush();
    return groups;
  }

  async function applyBatch(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    if (!Array.isArray(params.operations) || !params.operations.length) throw Object.assign(new Error("operations must contain at least one operation."), { code: "invalid_batch" });
    requireApi("1.8", "Snapshot-backed automatic rollback for batch edits");
    let automaticRollback = null;
    let result;
    try {
      result = await PowerPoint.run(async (context) => {
        const slide = getSlide(context, params);
        slide.load("id");
        const refs = slideRefMap(requestedSlideId(params), false);
        const batch = { refs: refs, shapes: new Map(), created: [], changed: [], deleted: [] };
        refs.forEach((shapeId, ref) => batch.shapes.set(ref, slide.shapes.getItem(shapeId)));
        const exported = slide.exportAsBase64();
        await context.sync();
        automaticRollback = { base64: exported.value, slideId: slide.id };
        await focusSlide(context, slide.id);
        const groups = operationGroups(params.operations, params.progressiveGranularity || "stage");
        for (const group of groups) {
          for (const entry of group) {
            const op = entry.operation;
            const index = entry.index;
            try {
              await applyBatchOperation(context, slide, op, batch);
            } catch (error) {
              error.message = "Operation " + index + " (" + op.op + ") failed: " + error.message;
              error.details = Object.assign({}, error.debugInfo || error.details || {}, {
                phase: "component_stage",
                operationIndex: index,
                operation: { op: op.op, target: op.shapeRef || op.target || op.ref || null },
              });
              throw error;
            }
          }
          try {
            await context.sync();
          } catch (error) {
            error.details = Object.assign({}, error.debugInfo || error.details || {}, {
              phase: "stage_sync",
              operationIndexes: group.map((entry) => entry.index),
              operations: group.map((entry) => entry.operation.op),
            });
            throw error;
          }
          await livePreviewTick();
        }
        batch.created.forEach((item) => {
          rememberShapeRef(slideRefMap(slide.id, false), item.proxy.id, item.proxy.name, item.ref);
        });
        const snapshot = { kind: "restoreSlide", base64: exported.value, slideId: slide.id, previousSlideId: "", replaceExisting: true };
        const undoToken = makeId();
        state.undo.set(undoToken, Object.assign(snapshot, { revisionAfter: state.revision + 1 }));
        return changed({
          slideId: slide.id,
          changed: batch.changed,
          created: batch.created.map((item) => ({ id: item.proxy.id, name: item.proxy.name, type: item.proxy.type, ref: item.ref })),
          deleted: batch.deleted,
          warnings: [],
          undoToken: undoToken,
          audit: {
            operationCount: params.operations.length,
            syncStageCount: operationGroups(params.operations, params.progressiveGranularity || "stage").length,
            progressiveGranularity: params.progressiveGranularity || "stage",
            summary: params.operations.map((op) => ({ op: op.op, target: op.shapeRef || op.target || op.ref || null })),
          },
        });
      });
    } catch (error) {
      if (automaticRollback) {
        try {
          const restoredSlideIds = await restoreSlideSnapshot(automaticRollback);
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: true, restoredSlideIds: restoredSlideIds },
            currentRevision: state.revision,
          });
        } catch (rollbackError) {
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: false, error: asError(rollbackError) },
            currentRevision: state.revision,
          });
        }
      }
      throw error;
    }
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function renderSlide(params) {
    requireApi("1.8", "Slide rendering");
    return PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const image = slide.getImageAsBase64({ width: params.width || 960 });
      await context.sync();
      return { status: "success", revision: state.revision, slideId: slide.id, mimeType: "image/png", imageBase64: image.value };
    });
  }
  async function exportSlide(params) {
    requireApi("1.8", "Slide export");
    return PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const exported = slide.exportAsBase64();
      await context.sync();
      return { status: "success", revision: state.revision, slideId: slide.id, presentationBase64: exported.value };
    });
  }
  function overlapRatio(a, b) {
    const width = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
    const height = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
    const intersection = width * height;
    return intersection / Math.max(1, Math.min(a.width * a.height, b.width * b.height));
  }
  async function verifySlide(params) {
    return PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const loaded = await loadShapes(context, slide, true);
      let slideWidth = 720;
      let slideHeight = 405;
      const warnings = [];
      if (hasApi("1.10")) {
        const setup = context.presentation.pageSetup;
        setup.load("slideWidth,slideHeight");
        await context.sync();
        slideWidth = setup.slideWidth;
        slideHeight = setup.slideHeight;
      } else {
        warnings.push({ code: "assumed_slide_size", message: "PowerPointApi 1.10 is unavailable; bounds checks assume a 16:9 720×405 point slide." });
      }
      const shapes = loaded.data;
      shapes.forEach((shape) => {
        if (
          String(shape.type || "").toLowerCase() === "placeholder"
          && !String(shape.text || "").trim()
        ) {
          warnings.push({
            code: "unresolved_placeholder",
            shapeId: shape.id,
            message: "Empty inherited placeholder remains visible in PowerPoint edit mode.",
          });
        }
        if (shape.x < 0 || shape.y < 0 || shape.x + shape.width > slideWidth || shape.y + shape.height > slideHeight) {
          warnings.push({ code: "out_of_bounds", shapeId: shape.id, message: "Shape extends outside the slide." });
        }
        if (shape.text) {
          const roughCapacity = Math.max(8, Math.floor((shape.width / 7) * (shape.height / 16)));
          if (shape.text.length > roughCapacity * 1.7) warnings.push({ code: "possible_text_overflow", shapeId: shape.id, message: "Text may overflow this shape; render and inspect it." });
        }
      });
      for (let i = 0; i < shapes.length; i += 1) {
        const a = shapes[i];
        if (a.width * a.height > slideWidth * slideHeight * 0.75) continue;
        for (let j = i + 1; j < shapes.length; j += 1) {
          const b = shapes[j];
          if (b.width * b.height > slideWidth * slideHeight * 0.75) continue;
          const ratio = overlapRatio(a, b);
          if (ratio > (params.overlapThreshold || 0.2)) warnings.push({ code: "shape_overlap", shapeIds: [a.id, b.id], ratio: Number(ratio.toFixed(3)), message: "Shapes overlap materially." });
        }
      }
      return { status: warnings.length ? "warning" : "success", revision: state.revision, slideId: slide.id, slideSize: { width: slideWidth, height: slideHeight }, warnings: warnings };
    });
  }

  async function filterVerification(params, acceptedCodes, check) {
    const result = await verifySlide(params);
    result.check = check;
    result.warnings = result.warnings.filter((item) => acceptedCodes.includes(item.code));
    result.status = result.warnings.length ? "warning" : "success";
    return result;
  }

  function parseHexColor(value) {
    const normalized = String(value || "").replace(/^#/, "");
    if (!/^[0-9a-f]{6}$/i.test(normalized)) return null;
    return [0, 2, 4].map((index) => parseInt(normalized.slice(index, index + 2), 16));
  }

  function colorContrast(first, second) {
    const colors = [parseHexColor(first), parseHexColor(second)];
    if (colors.some((color) => !color)) return null;
    const luminances = colors.map((color) => {
      const channels = color.map((item) => item / 255).map((item) => item <= 0.04045 ? item / 12.92 : Math.pow((item + 0.055) / 1.055, 2.4));
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    });
    return (Math.max(...luminances) + 0.05) / (Math.min(...luminances) + 0.05);
  }

  async function checkContrast(params) {
    return PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const shapes = slide.shapes;
      shapes.load("items/id,items/name,items/type");
      await context.sync();
      const candidates = shapes.items.filter((shape) => mayHaveText(shape.type));
      candidates.forEach((shape) => {
        shape.textFrame.load("hasText");
        shape.fill.load("foreColor,type");
        shape.textFrame.textRange.font.load("color");
      });
      if (candidates.length) await context.sync();
      const minimum = Number(params.minimumRatio || 4.5);
      const warnings = [];
      const unverifiable = [];
      candidates.forEach((shape) => {
        if (!shape.textFrame.hasText) return;
        const ratio = colorContrast(shape.textFrame.textRange.font.color, shape.fill.foreColor);
        if (ratio === null) unverifiable.push(shape.id);
        else if (ratio < minimum) warnings.push({ code: "low_contrast", shapeId: shape.id, ratio: Number(ratio.toFixed(3)), minimumRatio: minimum });
      });
      return { status: warnings.length ? "warning" : "success", revision: state.revision, slideId: slide.id, check: "contrast", warnings: warnings, unverifiableShapeIds: unverifiable };
    });
  }

  async function undoBatch(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    const undoToken = params.undoToken;
    const entry = state.undo.get(undoToken);
    if (!entry) {
      const error = new Error("Undo token is missing or expired from this add-in runtime.");
      error.code = "undo_not_found";
      throw error;
    }
    if (entry.revisionAfter !== state.revision) {
      const error = new Error("The presentation changed after this batch; refusing to overwrite newer edits.");
      error.code = "undo_revision_conflict";
      throw error;
    }
    const result = await PowerPoint.run(async (context) => {
      if (entry.kind === "restoreSlide") {
        const options = { formatting: "KeepSourceFormatting" };
        if (entry.replaceExisting && entry.slideId) options.targetSlideId = entry.slideId;
        else if (entry.previousSlideId) options.targetSlideId = entry.previousSlideId;
        context.presentation.insertSlidesFromBase64(entry.base64, options);
        if (entry.replaceExisting) context.presentation.slides.getItem(entry.slideId).delete();
      } else if (entry.kind === "deleteSlides") {
        entry.slideIds.forEach((id) => context.presentation.slides.getItem(id).delete());
      } else if (entry.kind === "replaceImported") {
        const options = { formatting: "KeepSourceFormatting" };
        if (entry.newSlideIds.length) options.targetSlideId = entry.newSlideIds[entry.newSlideIds.length - 1];
        else if (entry.previousSlideId) options.targetSlideId = entry.previousSlideId;
        context.presentation.insertSlidesFromBase64(entry.oldBase64, options);
        entry.newSlideIds.forEach((id) => context.presentation.slides.getItem(id).delete());
      }
      await context.sync();
      state.shapeRefs.clear();
      state.slideIds = [];
      return changed({ undone: undoToken });
    });
    state.undo.delete(undoToken);
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function insertSlides(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    const result = await PowerPoint.run(async (context) => {
      const slides = context.presentation.slides;
      let before;
      if (state.slideIds.length) {
        before = new Set(state.slideIds);
      } else {
        slides.load("items/id");
        await context.sync();
        before = new Set(slides.items.map((slide) => slide.id));
      }
      const options = { formatting: params.formatting || "KeepSourceFormatting" };
      const targetSlideId = params.targetSlideId;
      const sourceSlideIds = params.sourceSlideIds;
      if (targetSlideId) options.targetSlideId = targetSlideId;
      if (sourceSlideIds) options.sourceSlideIds = sourceSlideIds;
      context.presentation.insertSlidesFromBase64(stripDataUrl(params.presentationBase64), options);
      await context.sync();
      slides.load("items/id");
      await context.sync();
      const inserted = slides.items.map((slide) => slide.id).filter((id) => !before.has(id));
      state.slideIds = slides.items.map((slide) => slide.id);
      if (inserted.length) await focusSlide(context, inserted[inserted.length - 1]);
      const undoToken = makeId();
      state.undo.set(undoToken, { kind: "deleteSlides", slideIds: inserted, revisionAfter: state.revision + 1 });
      return changed({ insertedSlideIds: inserted, undoToken: undoToken });
    });
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function replaceSlideFromBase64(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    const result = await PowerPoint.run(async (context) => {
      const slides = context.presentation.slides;
      slides.load("items/id");
      const target = getSlide(context, params);
      target.load("id");
      await context.sync();
      const before = new Set(slides.items.map((item) => item.id));
      const targetIndex = slides.items.findIndex((item) => item.id === target.id);
      const previousSlideId = targetIndex > 0 ? slides.items[targetIndex - 1].id : "";
      await focusSlide(context, target.id);
      context.presentation.insertSlidesFromBase64(stripDataUrl(params.presentationBase64), { targetSlideId: target.id, formatting: params.formatting || "KeepSourceFormatting" });
      target.delete();
      await context.sync();
      slides.load("items/id");
      await context.sync();
      const inserted = slides.items.map((item) => item.id).filter((id) => !before.has(id));
      state.shapeRefs.delete(target.id);
      state.slideIds = slides.items.map((item) => item.id);
      if (inserted.length) await focusSlide(context, inserted[inserted.length - 1]);
      const undoToken = makeId();
      state.undo.set(undoToken, { kind: "replaceImported", newSlideIds: inserted, oldBase64: params.undoBase64, previousSlideId: previousSlideId, revisionAfter: state.revision + 1 });
      return changed({ changed: [], created: inserted.map((id) => ({ slideId: id })), deleted: [target.id], undoToken: undoToken, nativeEditable: true, chartMode: params.chartMode || undefined, audit: { action: "replace_slide_from_ooxml" } });
    });
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  function addElement(slide, element, created) {
    const box = Array.isArray(element.box) ? {
      left: element.box[0], top: element.box[1], width: element.box[2], height: element.box[3],
    } : bounds(element);
    let shape;
    if (element.type === "text") {
      shape = slide.shapes.addTextBox(String(element.text || ""), box);
    } else if (element.type === "image") {
      throw Object.assign(new Error("Image elements must be inserted through the ImageCoercion stage."), { code: "image_stage_required" });
    } else if (element.type === "line") {
      shape = slide.shapes.addLine(element.connector || "Straight", box);
    } else if (element.type === "chart") {
      throw Object.assign(new Error("Chart elements must be compiled before the Office.js batch starts."), { code: "chart_not_compiled" });
    } else if (element.type === "table") {
      if (typeof slide.shapes.addTable !== "function") throw Object.assign(new Error("Tables are unavailable in this PowerPoint host."), { code: "capability_unavailable" });
      const values = element.values || [[""]];
      shape = slide.shapes.addTable(values.length, Math.max(1, values[0].length), Object.assign({}, box, { values: values }));
    } else {
      shape = slide.shapes.addGeometricShape(element.geometry || "Rectangle", box);
      if (element.text !== undefined) shape.textFrame.textRange.text = String(element.text);
    }
    const ref = element.ref || "element-" + (created.length + 1);
    shape.name = "cyrene:" + ref;
    positionShape(shape, element);
    styleShape(shape, element.style);
    shape.load("id,name,type");
    created.push({ proxy: shape, ref: ref });
    return shape;
  }

  async function addSlideAndGetId(context, options) {
    const slides = context.presentation.slides;
    let before;
    if (state.slideIds.length) {
      before = new Set(state.slideIds);
    } else {
      slides.load("items/id");
      await context.sync();
      before = new Set(slides.items.map((item) => item.id));
    }
    slides.add(options || {});
    await context.sync();
    slides.load("items/id");
    await context.sync();
    const orderedIds = slides.items.map((item) => item.id);
    const createdIds = orderedIds.filter((id) => !before.has(id));
    if (!createdIds.length) {
      throw Object.assign(new Error("PowerPoint added a slide but did not expose its ID."), { code: "slide_create_not_observed" });
    }
    state.slideIds = orderedIds;
    const createdSlideId = createdIds[createdIds.length - 1];
    await focusSlide(context, createdSlideId);
    return createdSlideId;
  }

  async function removeInheritedPlaceholders(context, slide) {
    // Fresh generic pages receive Cyrene-owned content shapes, so inherited
    // prompt boxes must not remain underneath them. Template pages use the
    // duplication path instead, which deliberately preserves and binds shapes.
    const shapes = slide.shapes;
    shapes.load("items/id,items/name,items/type");
    await context.sync();
    const removed = [];
    for (const shape of shapes.items) {
      if (String(shape.type || "").toLowerCase() !== "placeholder") continue;
      removed.push({ id: shape.id, name: shape.name, type: shape.type });
      shape.delete();
    }
    if (removed.length) await context.sync();
    return removed;
  }

  async function rollbackCreatedSlide(slideId) {
    if (!slideId) return;
    await PowerPoint.run(async (context) => {
      context.presentation.slides.getItem(slideId).delete();
      await context.sync();
    });
    state.slideIds = state.slideIds.filter((id) => id !== slideId);
    state.slideSignatures.delete(slideId);
    state.shapeRefs.delete(slideId);
    forgetSlideIdempotency(slideId);
  }

  async function restoreSlideSnapshot(snapshot) {
    if (!snapshot || !snapshot.base64 || !snapshot.slideId) {
      throw Object.assign(new Error("No PowerPoint snapshot is available for rollback."), { code: "rollback_snapshot_unavailable" });
    }
    const restoredSlideIds = await PowerPoint.run(async (context) => {
      const slides = context.presentation.slides;
      slides.load("items/id");
      await context.sync();
      const before = new Set(slides.items.map((item) => item.id));
      context.presentation.insertSlidesFromBase64(snapshot.base64, {
        targetSlideId: snapshot.slideId,
        formatting: "KeepSourceFormatting",
      });
      slides.getItem(snapshot.slideId).delete();
      await context.sync();
      slides.load("items/id");
      await context.sync();
      state.slideIds = slides.items.map((item) => item.id);
      const restored = state.slideIds.filter((id) => !before.has(id));
      if (restored.length) await focusSlide(context, restored[restored.length - 1]);
      return restored;
    });
    state.shapeRefs.delete(snapshot.slideId);
    state.slideSignatures.delete(snapshot.slideId);
    forgetSlideIdempotency(snapshot.slideId);
    for (const slideId of restoredSlideIds) await rememberSlideSignature(slideId);
    return restoredSlideIds;
  }

  async function createSlide(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    const spec = params.slideSpec || {};
    const preparedElements = await Promise.all((spec.elements || []).map(prepareSlideElement));
    if (params.commitMode === "progressive") {
      const progressive = await createSlideProgressive(params, spec, preparedElements);
      rememberIdempotent(params.idempotencyKey, progressive);
      return progressive;
    }
    let createdSlideId = "";
    let result;
    try {
      result = await PowerPoint.run(async (context) => {
        const slides = context.presentation.slides;
        const addOptions = {};
        if (spec.slideMasterId) addOptions.slideMasterId = spec.slideMasterId;
        if (spec.layoutId) addOptions.layoutId = spec.layoutId;
        createdSlideId = await addSlideAndGetId(context, addOptions);
        const slide = slides.getItem(createdSlideId);
        slide.load("id");
        const removedPlaceholders = await removeInheritedPlaceholders(context, slide);
        if (spec.background) {
          requireApi("1.10", "Slide background editing");
          slide.background.fill.setSolidFill({ color: String(spec.background), transparency: 0 });
        }
        const created = [];
        for (const element of preparedElements) {
          if (element.type === "image") await addImageElement(context, slide, element, created);
          else addElement(slide, element, created);
        }
        await context.sync();
        created.forEach((item) => {
          rememberShapeRef(slideRefMap(slide.id, false), item.proxy.id, item.proxy.name, item.ref);
        });
        const undoToken = makeId();
        state.undo.set(undoToken, { kind: "deleteSlides", slideIds: [slide.id], revisionAfter: state.revision + 1 });
        return changed({
          slideId: slide.id,
          changed: [],
          created: created.map((item) => ({ id: item.proxy.id, ref: item.ref, name: item.proxy.name, type: item.proxy.type })),
          deleted: [],
          warnings: [],
          undoToken: undoToken,
          audit: {
            action: "create_slide",
            layout: spec.layout || "blank",
            elementCount: created.length,
            removedPlaceholderCount: removedPlaceholders.length,
          },
        });
      });
    } catch (error) {
      if (createdSlideId) {
        try {
          await rollbackCreatedSlide(createdSlideId);
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: true, deletedSlideId: createdSlideId },
            currentRevision: state.revision,
          });
        } catch (rollbackError) {
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: false, deletedSlideId: createdSlideId, error: asError(rollbackError) },
            currentRevision: state.revision,
          });
        }
      }
      throw error;
    }
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function applySlideSpec(params) {
    const spec = params.slideSpec || {};
    const preparedElements = await Promise.all((spec.elements || []).map(prepareSlideElement));
    if (params.commitMode === "progressive") {
      const replay = checkMutation(params);
      if (replay) return Object.assign({}, replay, { replayed: true });
      const progressive = await applySlideSpecProgressive(params, spec, preparedElements);
      rememberIdempotent(params.idempotencyKey, progressive);
      return progressive;
    }
    const operations = [];
    if (params.replaceExisting) {
      const inspected = await listShapes(Object.assign({}, params, { includeText: false }));
      inspected.shapes.forEach((shape) => operations.push({ op: "delete_shape", shapeRef: shape.id }));
    }
    if (spec.background) operations.push({ op: "set_background", color: spec.background });
    preparedElements.forEach((element) => {
      const base = Object.assign({}, element, {
        x: Array.isArray(element.box) ? element.box[0] : element.x,
        y: Array.isArray(element.box) ? element.box[1] : element.y,
        width: Array.isArray(element.box) ? element.box[2] : element.width,
        height: Array.isArray(element.box) ? element.box[3] : element.height,
      });
      if (element.type === "text") operations.push(Object.assign(base, { op: "add_textbox" }));
      else if (element.type === "image") operations.push(Object.assign(base, { op: "insert_image" }));
      else if (element.type === "line") operations.push(Object.assign(base, { op: "add_line" }));
      else if (element.type === "table") operations.push(Object.assign(base, { op: "insert_table" }));
      else operations.push(Object.assign(base, { op: "add_shape" }));
    });
    return applyBatch(Object.assign({}, params, { operations: operations }));
  }

  function progressiveElementGroups(elements, granularity) {
    if (granularity === "element") return elements.map((element, index) => ({ name: "element-" + (index + 1), elements: [element] }));
    const groups = [
      { name: "structure", elements: elements.filter((item) => ["shape", "line"].includes(item.type)) },
      { name: "title", elements: elements.filter((item) => item.type === "text" && /(^|[-_])title($|[-_])/i.test(String(item.ref || ""))) },
      { name: "content", elements: elements.filter((item) => item.type === "text" && !/(^|[-_])title($|[-_])/i.test(String(item.ref || ""))) },
      { name: "media", elements: elements.filter((item) => ["image", "table"].includes(item.type)) },
    ];
    return groups.filter((group) => group.elements.length);
  }

  async function addProgressiveElements(slideId, elements) {
    return PowerPoint.run(async (context) => {
      const slide = context.presentation.slides.getItem(slideId);
      await focusSlide(context, slideId);
      const created = [];
      for (const element of elements) {
        if (element.type === "image") await addImageElement(context, slide, element, created);
        else addElement(slide, element, created);
      }
      await context.sync();
      await livePreviewTick();
      created.forEach((item) => {
        rememberShapeRef(slideRefMap(slideId, false), item.proxy.id, item.proxy.name, item.ref);
      });
      return created.map((item) => ({ id: item.proxy.id, ref: item.ref, name: item.proxy.name, type: item.proxy.type }));
    });
  }

  async function setProgressiveBackground(slideId, color) {
    requireApi("1.10", "Slide background editing");
    return PowerPoint.run(async (context) => {
      const slide = context.presentation.slides.getItem(slideId);
      await focusSlide(context, slideId);
      slide.background.fill.setSolidFill({ color: String(color), transparency: 0 });
      await context.sync();
      await livePreviewTick();
    });
  }

  function progressiveStageWarning(group, error) {
    const parsed = asError(error);
    return {
      code: group.elements.some((item) => item.type === "image") ? "media_stage_failed" : "progressive_stage_failed",
      stage: group.name,
      message: parsed.message,
      details: parsed.details,
      recoverable: true,
    };
  }

  async function createSlideProgressive(params, spec, preparedElements) {
    let slideId = "";
    const stages = [];
    const warnings = [];
    const allCreated = [];
    try {
      slideId = await PowerPoint.run(async (context) => {
        const options = {};
        if (spec.slideMasterId) options.slideMasterId = spec.slideMasterId;
        if (spec.layoutId) options.layoutId = spec.layoutId;
        const createdSlideId = await addSlideAndGetId(context, options);
        const slide = context.presentation.slides.getItem(createdSlideId);
        await removeInheritedPlaceholders(context, slide);
        return createdSlideId;
      });
      changed({ created: [{ slideId: slideId }], audit: { action: "progressive_create_slide", stage: "slide" } });
      stages.push({ name: "slide", status: "applied", revision: state.revision, created: [{ slideId: slideId }] });
      if (spec.background) {
        await setProgressiveBackground(slideId, spec.background);
        changed({ changed: ["slide-background"], audit: { action: "progressive_create_slide", stage: "background" } });
        stages.push({ name: "background", status: "applied", revision: state.revision, changed: ["slide-background"] });
      }
      const groups = progressiveElementGroups(preparedElements, params.progressiveGranularity || "stage");
      for (const group of groups) {
        const records = await addProgressiveElements(slideId, group.elements);
        allCreated.push(...records);
        changed({ created: records, audit: { action: "progressive_create_slide", stage: group.name } });
        stages.push({ name: group.name, status: "applied", revision: state.revision, created: records });
      }
    } catch (error) {
      if (slideId) {
        try {
          await rollbackCreatedSlide(slideId);
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: true, deletedSlideId: slideId },
            currentRevision: state.revision,
          });
        } catch (rollbackError) {
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: false, deletedSlideId: slideId, error: asError(rollbackError) },
            currentRevision: state.revision,
          });
        }
      }
      throw error;
    }
    const undoToken = makeId();
    state.undo.set(undoToken, { kind: "deleteSlides", slideIds: [slideId], revisionAfter: state.revision });
    return {
      status: warnings.length ? "warning" : "applied", revision: state.revision, slideId: slideId,
      changed: [], created: [{ slideId: slideId }, ...allCreated], deleted: [], warnings: warnings,
      undoToken: undoToken, renderId: null, commitMode: "progressive", stages: stages,
      audit: { action: "create_slide", commitMode: "progressive", stageCount: stages.length },
    };
  }

  async function applySlideSpecProgressive(params, spec, preparedElements) {
    requireApi("1.8", "Snapshot-backed automatic rollback for progressive edits");
    const initial = await PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const exported = slide.exportAsBase64();
      await context.sync();
      await focusSlide(context, slide.id);
      return { slideId: slide.id, exported: exported ? exported.value : null };
    });
    const stages = [];
    const allCreated = [];
    const allDeleted = [];
    const warnings = [];
    try {
      if (params.replaceExisting) {
        const deleted = await PowerPoint.run(async (context) => {
          const slide = context.presentation.slides.getItem(initial.slideId);
          const shapes = slide.shapes;
          shapes.load("items/id");
          await context.sync();
          const deleted = shapes.items.map((shape) => shape.id);
          await focusSlide(context, initial.slideId);
          for (const shape of shapes.items) {
            shape.delete();
          }
          if (shapes.items.length) {
            await context.sync();
            await livePreviewTick();
          }
          return deleted;
        });
        allDeleted.push(...deleted);
        slideRefMap(initial.slideId, true);
        changed({ deleted: deleted, audit: { action: "progressive_apply_slide_spec", stage: "clear" } });
        stages.push({ name: "clear", status: "applied", revision: state.revision, deleted: deleted });
      }
      if (spec.background) {
        await setProgressiveBackground(initial.slideId, spec.background);
        changed({ changed: ["slide-background"], audit: { action: "progressive_apply_slide_spec", stage: "background" } });
        stages.push({ name: "background", status: "applied", revision: state.revision, changed: ["slide-background"] });
      }
      const groups = progressiveElementGroups(preparedElements, params.progressiveGranularity || "stage");
      for (const group of groups) {
        try {
          const records = await addProgressiveElements(initial.slideId, group.elements);
          allCreated.push(...records);
          changed({ created: records, audit: { action: "progressive_apply_slide_spec", stage: group.name } });
          stages.push({ name: group.name, status: "applied", revision: state.revision, created: records });
        } catch (error) {
          const parsed = progressiveStageWarning(group, error);
          error.details = Object.assign({}, error.details || {}, { stage: parsed });
          throw error;
        }
      }
      if (!stages.length) throw Object.assign(new Error("Progressive SlideSpec contains no visible changes."), { code: "empty_slide_spec" });
      const undoToken = initial.exported ? makeId() : null;
      if (initial.exported) state.undo.set(undoToken, { kind: "restoreSlide", base64: initial.exported, slideId: initial.slideId, previousSlideId: "", replaceExisting: true, revisionAfter: state.revision });
      return {
        status: warnings.length ? "warning" : "applied", revision: state.revision, slideId: initial.slideId,
        changed: [], created: allCreated, deleted: allDeleted, warnings: warnings,
        undoToken: undoToken, renderId: null, commitMode: "progressive", stages: stages,
        audit: { action: "apply_slide_spec", commitMode: "progressive", stageCount: stages.length },
      };
    } catch (error) {
      if (initial.exported) {
        try {
          const restoredSlideIds = await restoreSlideSnapshot({ base64: initial.exported, slideId: initial.slideId });
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: true, restoredSlideIds: restoredSlideIds },
            currentRevision: state.revision,
          });
        } catch (rollbackError) {
          error.details = Object.assign({}, error.details || {}, {
            rollback: { completed: false, error: asError(rollbackError) },
            currentRevision: state.revision,
          });
        }
      }
      throw error;
    }
  }

  async function duplicateSlide(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    requireApi("1.8", "Slide duplication");
    const result = await PowerPoint.run(async (context) => {
      const source = getSlide(context, params);
      source.load("id");
      const slides = context.presentation.slides;
      slides.load("items/id");
      const exported = source.exportAsBase64();
      await context.sync();
      const before = new Set(slides.items.map((item) => item.id));
      context.presentation.insertSlidesFromBase64(exported.value, { targetSlideId: source.id, formatting: "KeepSourceFormatting" });
      await context.sync();
      slides.load("items/id");
      await context.sync();
      const createdIds = slides.items.map((item) => item.id).filter((id) => !before.has(id));
      state.slideIds = slides.items.map((item) => item.id);
      const slideId = createdIds[createdIds.length - 1];
      await focusSlide(context, slideId);
      const undoToken = makeId();
      state.undo.set(undoToken, { kind: "deleteSlides", slideIds: [slideId], revisionAfter: state.revision + 1 });
      return changed({ created: [{ slideId: slideId }], undoToken: undoToken, audit: { action: "duplicate_slide", sourceSlideId: source.id } });
    });
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function applyTemplateBindings(slideId, bindings) {
    return PowerPoint.run(async (context) => {
      const slide = context.presentation.slides.getItem(slideId);
      slide.load("id");
      const shapes = slide.shapes;
      shapes.load("items/id,items/name,items/type");
      await context.sync();
      const targets = targetMap(shapes.items);
      const changedTargets = [];
      const deletedTargets = [];
      for (let index = 0; index < bindings.length; index += 1) {
        const binding = bindings[index] || {};
        const target = String(binding.shapeRef || "");
        if (!target) throw Object.assign(new Error("Template binding " + index + " requires shapeRef."), { code: "shape_ref_required" });
        const shape = requireTarget(targets, target);
        if (binding.delete === true) {
          shape.delete();
          deletedTargets.push(target);
          continue;
        }
        if (!Object.prototype.hasOwnProperty.call(binding, "text")) {
          throw Object.assign(new Error("Template binding " + index + " must provide text or delete=true."), { code: "empty_template_binding" });
        }
        if (!mayHaveText(shape.type)) {
          throw Object.assign(new Error("Template target does not support text replacement: " + target), { code: "shape_has_no_text" });
        }
        shape.textFrame.textRange.text = String(binding.text);
        changedTargets.push(target);
      }
      if (bindings.length) await context.sync();
      return changed({
        slideId: slide.id,
        changed: changedTargets,
        created: [],
        deleted: deletedTargets,
        warnings: [],
        audit: { action: "apply_template_bindings", bindingCount: bindings.length },
      });
    });
  }

  async function createFromTemplate(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    const templateSlideId = params.templateSlideId;
    if (!templateSlideId) throw Object.assign(new Error("create_from_template requires templateSlideId."), { code: "template_slide_required" });
    const idempotencyKey = params.idempotencyKey;
    const duplicate = await duplicateSlide(Object.assign({}, params, {
      slideId: templateSlideId,
      idempotencyKey: idempotencyKey + ":duplicate",
    }));
    const slideId = duplicate.created[0] && duplicate.created[0].slideId;
    const spec = params.slideSpec || {};
    const bindings = Array.isArray(spec.templateBindings) ? spec.templateBindings : [];
    if (!(bindings.length || spec.background || (spec.elements || []).length)) {
      const result = Object.assign({}, duplicate, { audit: { action: "create_from_template", templateSlideId: templateSlideId } });
      rememberIdempotent(idempotencyKey, result);
      return result;
    }
    let revision = duplicate.revision;
    const appliedResults = [];
    try {
      if (bindings.length) {
        const bindingResult = await applyTemplateBindings(slideId, bindings);
        revision = bindingResult.revision;
        appliedResults.push(bindingResult);
      }
      if (spec.background || (spec.elements || []).length) {
        const specResult = await applySlideSpec(Object.assign({}, params, {
          slideId: slideId,
          expectedRevision: revision,
          idempotencyKey: idempotencyKey + ":spec",
          replaceExisting: false,
        }));
        revision = specResult.revision;
        appliedResults.push(specResult);
      }
    } catch (error) {
      try {
        await rollbackCreatedSlide(slideId);
        state.undo.delete(duplicate.undoToken);
        error.details = Object.assign({}, error.details || {}, {
          templateRollback: { completed: true, deletedSlideId: slideId },
          currentRevision: state.revision,
        });
      } catch (rollbackError) {
        error.details = Object.assign({}, error.details || {}, {
          templateRollback: { completed: false, deletedSlideId: slideId, error: asError(rollbackError) },
          currentRevision: state.revision,
        });
      }
      throw error;
    }
    const duplicateUndo = state.undo.get(duplicate.undoToken);
    if (duplicateUndo) duplicateUndo.revisionAfter = state.revision;
    appliedResults.forEach((item) => {
      if (item.undoToken && item.undoToken !== duplicate.undoToken) state.undo.delete(item.undoToken);
    });
    const result = {
      status: appliedResults.some((item) => item.status === "warning") ? "warning" : "applied",
      revision: revision,
      slideId: slideId,
      changed: appliedResults.flatMap((item) => item.changed || []),
      created: (duplicate.created || []).concat(appliedResults.flatMap((item) => item.created || [])),
      deleted: appliedResults.flatMap((item) => item.deleted || []),
      warnings: appliedResults.flatMap((item) => item.warnings || []),
      undoToken: duplicate.undoToken,
      renderId: null,
      commitMode: params.commitMode || "progressive",
      stages: appliedResults.flatMap((item) => item.stages || []),
      audit: { action: "create_from_template", templateSlideId: templateSlideId, bindingCount: bindings.length },
    };
    rememberIdempotent(idempotencyKey, result);
    return result;
  }

  async function deleteSlide(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    requireApi("1.8", "Undoable slide deletion");
    const result = await PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const exported = slide.exportAsBase64();
      await context.sync();
      await focusSlide(context, slide.id);
      slide.delete();
      await context.sync();
      state.shapeRefs.delete(slide.id);
      state.slideIds = state.slideIds.filter((id) => id !== slide.id);
      forgetSlideIdempotency(slide.id);
      const undoToken = makeId();
      state.undo.set(undoToken, { kind: "restoreSlide", base64: exported.value, slideId: slide.id, previousSlideId: "", replaceExisting: false, revisionAfter: state.revision + 1 });
      return changed({ deleted: [slide.id], undoToken: undoToken, audit: { action: "delete_slide" } });
    });
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function moveSlide(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    const result = await PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      if (typeof slide.moveTo !== "function") throw Object.assign(new Error("This PowerPoint host does not expose slide.moveTo."), { code: "capability_unavailable" });
      slide.load("id");
      const targetIndex = params.targetIndex;
      await context.sync();
      await focusSlide(context, slide.id);
      slide.moveTo(targetIndex);
      await context.sync();
      const orderedIds = state.slideIds.filter((id) => id !== slide.id);
      orderedIds.splice(targetIndex, 0, slide.id);
      state.slideIds = orderedIds;
      return changed({ changed: [slide.id], audit: { action: "move_slide", targetIndex: targetIndex } });
    });
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function getSelection() {
    return getContext();
  }

  async function getTheme() {
    return PowerPoint.run(async (context) => {
      requireApi("1.10", "Theme color inspection");
      const slide = getSlide(context, {});
      const scheme = slide.themeColorScheme;
      const colorNames = ["accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "dark1", "dark2", "light1", "light2", "hyperlink", "followedHyperlink"];
      const colors = {};
      const results = colorNames.map((name) => scheme.getThemeColor(PowerPoint.ThemeColor[name]));
      await context.sync();
      colorNames.forEach((name, index) => { colors[name] = results[index].value; });
      return { status: "success", revision: state.revision, theme: { source: "office_host", colors: colors }, warnings: [] };
    });
  }

  async function getMaster() {
    requireApi("1.3", "Slide master inspection");
    return PowerPoint.run(async (context) => {
      const masters = context.presentation.slideMasters;
      masters.load("items/id,items/name,items/layouts/items/id,items/layouts/items/name,items/layouts/items/type");
      await context.sync();
      return {
        status: "success", revision: state.revision,
        masters: masters.items.map((master) => ({
          id: master.id, name: master.name,
          layouts: master.layouts.items.map((layout) => ({ id: layout.id, name: layout.name, type: layout.type })),
        })),
        capabilities: { read: true, editShapes: false, applyLayout: true, deleteMaster: false },
        warnings: ["This Office.js host exposes master/layout inspection and layout application, but not slide-master shape mutation. Use the file backend for master edits."],
      };
    });
  }

  async function compareBeforeAfter(params) {
    const render = await renderSlide(params);
    const key = render.slideId;
    const previous = state.slideRenders.get(key) || null;
    state.slideRenders.set(key, render.imageBase64);
    const includeImages = params.includeImages;
    return { status: "success", revision: state.revision, slideId: key, beforeAvailable: previous !== null, changed: previous !== null && previous !== render.imageBase64, beforeBase64: includeImages ? previous : undefined, afterBase64: includeImages ? render.imageBase64 : undefined };
  }

  async function executeOfficeCommand(params) {
    if (!params.confirmed) throw Object.assign(new Error("Explicit confirmation is required for escape operations."), { code: "confirmation_required" });
    const allowed = { get_context: getContext, render_slide: renderSlide, inspect_slide: listShapes };
    const handler = allowed[params.command];
    if (!handler) throw Object.assign(new Error("Only the audited Office.js command allowlist is accepted."), { code: "unsafe_command_rejected" });
    return handler(params.arguments || {});
  }

  async function editTable(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    requireApi("1.8", "Table editing");
    const result = await PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const exported = slide.exportAsBase64();
      const values = params.values || [[""]];
      const target = params.shapeRef;
      let shape;
      let created = [];
      let changedIds = [];
      await context.sync();
      await focusSlide(context, slide.id);
      if (target) {
        const refs = slideRefMap(requestedSlideId(params), false);
        shape = slide.shapes.getItem(refs.get(target) || target);
        const table = shape.getTable();
        values.forEach((row, rowIndex) => row.forEach((value, columnIndex) => {
          table.getCellOrNullObject(rowIndex, columnIndex).text = String(value ?? "");
        }));
        changedIds = [target];
      } else {
        const options = Object.assign({}, bounds(params), { values: values });
        shape = slide.shapes.addTable(values.length, Math.max(1, values[0].length), options);
        shape.name = "cyrene:" + (params.ref || "table");
        shape.load("id,name,type");
        created = [{ proxy: shape, ref: params.ref || "table" }];
      }
      await context.sync();
      created.forEach((item) => rememberShapeRef(slideRefMap(slide.id, false), item.proxy.id, item.proxy.name, item.ref));
      const undoToken = makeId();
      state.undo.set(undoToken, { kind: "restoreSlide", base64: exported.value, slideId: slide.id, previousSlideId: "", replaceExisting: true, revisionAfter: state.revision + 1 });
      return changed({ changed: changedIds, created: created.map((item) => ({ id: item.proxy.id, ref: item.ref, type: item.proxy.type })), undoToken: undoToken, audit: { action: target ? "edit_table" : "create_table" } });
    });
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  function chartSvg(spec, width, height) {
    const categories = spec.categories || [];
    const series = spec.series || [];
    const values = series.flatMap((item) => item.values || []).map(Number).filter(Number.isFinite);
    const maximum = Math.max(1, ...values);
    const palette = ["#2563EB", "#7C3AED", "#059669", "#EA580C", "#DC2626"];
    const margin = { left: 46, right: 18, top: 24, bottom: 42 };
    const plotWidth = Math.max(10, width - margin.left - margin.right);
    const plotHeight = Math.max(10, height - margin.top - margin.bottom);
    const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char]);
    const elements = [`<rect width="100%" height="100%" fill="${esc(spec.background || "#FFFFFF")}"/>`, `<line x1="${margin.left}" y1="${margin.top + plotHeight}" x2="${margin.left + plotWidth}" y2="${margin.top + plotHeight}" stroke="#94A3B8"/>`];
    const type = spec.type || "column";
    if (type === "line") {
      series.forEach((item, seriesIndex) => {
        const points = (item.values || []).map((value, index) => {
          const x = margin.left + (categories.length <= 1 ? plotWidth / 2 : index * plotWidth / (categories.length - 1));
          const y = margin.top + plotHeight - Number(value || 0) / maximum * plotHeight;
          return `${x},${y}`;
        }).join(" ");
        elements.push(`<polyline points="${points}" fill="none" stroke="${esc(item.color || palette[seriesIndex % palette.length])}" stroke-width="3"/>`);
      });
    } else {
      const groupWidth = plotWidth / Math.max(1, categories.length);
      const barWidth = Math.max(2, groupWidth * 0.72 / Math.max(1, series.length));
      series.forEach((item, seriesIndex) => (item.values || []).forEach((value, index) => {
        const barHeight = Number(value || 0) / maximum * plotHeight;
        const x = margin.left + index * groupWidth + groupWidth * 0.14 + seriesIndex * barWidth;
        const y = margin.top + plotHeight - barHeight;
        elements.push(`<rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="2" fill="${esc(item.color || palette[seriesIndex % palette.length])}"/>`);
      }));
    }
    categories.forEach((label, index) => {
      const x = margin.left + (index + 0.5) * plotWidth / Math.max(1, categories.length);
      elements.push(`<text x="${x}" y="${height - 14}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#475569">${esc(label)}</text>`);
    });
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${elements.join("")}</svg>`;
  }

  function svgToPngBase64(svg, width, height) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = width;
          canvas.height = height;
          const context = canvas.getContext("2d");
          context.drawImage(image, 0, 0, width, height);
          resolve(stripDataUrl(canvas.toDataURL("image/png")));
        } catch (error) {
          reject(error);
        } finally {
          URL.revokeObjectURL(image.src);
        }
      };
      image.onerror = () => {
        URL.revokeObjectURL(image.src);
        reject(Object.assign(new Error("The visual chart could not be rasterized."), { code: "chart_render_failed" }));
      };
      image.src = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    });
  }

  async function prepareSlideElement(element) {
    if (element.type !== "chart") return element;
    const box = Array.isArray(element.box) ? element.box : [element.x, element.y, element.width, element.height];
    const width = Math.max(64, Number(box[2] || 420) * 2);
    const height = Math.max(64, Number(box[3] || 260) * 2);
    const spec = element.chartSpec || element.data || { type: element.chartType || "column", categories: [], series: [] };
    const imageBase64 = await svgToPngBase64(chartSvg(spec, width, height), width, height);
    return Object.assign({}, element, { type: "image", imageBase64: imageBase64, chartMode: "visual" });
  }

  async function editChart(params) {
    if (params.chartMode === "native") {
      if (!params.presentationBase64) throw Object.assign(new Error("Native chart mode requires a generated chart-slide presentation payload."), { code: "native_chart_payload_required" });
      const result = await insertSlides(params);
      result.nativeEditable = true;
      result.chartMode = "native";
      return result;
    }
    const width = Number(params.width || 420);
    const height = Number(params.height || 260);
    const svg = chartSvg(params.chartSpec || {}, Math.max(64, width * 2), Math.max(64, height * 2));
    const imageBase64 = await svgToPngBase64(svg, Math.max(64, width * 2), Math.max(64, height * 2));
    const operations = [];
    if (params.shapeRef) operations.push({ op: "delete_shape", shapeRef: params.shapeRef });
    operations.push({ op: "insert_image", ref: params.ref || params.shapeRef || "chart", x: params.x, y: params.y, width: width, height: height, imageBase64: imageBase64 });
    const result = await applyBatch(Object.assign({}, params, { operations: operations }));
    result.nativeEditable = false;
    result.chartMode = "visual";
    return result;
  }

  async function editLayout(params) {
    const replay = checkMutation(params);
    if (replay) return Object.assign({}, replay, { replayed: true });
    const result = await PowerPoint.run(async (context) => {
      const slide = getSlide(context, params);
      slide.load("id");
      const exported = hasApi("1.8") ? slide.exportAsBase64() : null;
      const master = params.slideMasterId ? context.presentation.slideMasters.getItem(params.slideMasterId) : slide.slideMaster;
      const layout = master.layouts.getItem(params.layoutId);
      if (typeof slide.applyLayout !== "function") throw Object.assign(new Error("This PowerPoint host cannot apply layouts programmatically."), { code: "capability_unavailable" });
      await context.sync();
      await focusSlide(context, slide.id);
      slide.applyLayout(layout);
      await context.sync();
      const undoToken = exported ? makeId() : null;
      if (exported) state.undo.set(undoToken, { kind: "restoreSlide", base64: exported.value, slideId: slide.id, previousSlideId: "", replaceExisting: true, revisionAfter: state.revision + 1 });
      return changed({ changed: [slide.id], undoToken: undoToken, audit: { action: "apply_layout", layoutId: params.layoutId } });
    });
    rememberIdempotent(params.idempotencyKey, result);
    return result;
  }

  async function unsupportedAdvanced(name) {
    throw Object.assign(new Error(name + " is not exposed by the production PowerPoint Office.js API on this host. Use the typed OOXML file backend or import a prepared slide."), { code: "capability_unavailable" });
  }

  const handlers = {
    "ppt.get_context": getContext,
    "ppt.inspect": inspect,
    "ppt.list_slides": listSlides,
    "ppt.get_slide": getSlideStructure,
    "ppt.list_shapes": listShapes,
    "ppt.get_shape": getShape,
    "ppt.read_text": readText,
    "ppt.get_selection": getSelection,
    "ppt.get_master": getMaster,
    "ppt.get_theme": getTheme,
    "ppt.apply_batch": applyBatch,
    "ppt.create_slide": createSlide,
    "ppt.duplicate_slide": duplicateSlide,
    "ppt.apply_slide_spec": applySlideSpec,
    "ppt.relayout_slide": applySlideSpec,
    "ppt.create_from_template": createFromTemplate,
    "ppt.replace_slide": applySlideSpec,
    "ppt.move_slide": moveSlide,
    "ppt.delete_slide": deleteSlide,
    "ppt.render_slide": renderSlide,
    "ppt.export_slide": exportSlide,
    "ppt.verify_slide": verifySlide,
    "ppt.check_overflow": (params) => filterVerification(params, ["out_of_bounds", "possible_text_overflow"], "overflow"),
    "ppt.check_overlap": (params) => filterVerification(params, ["shape_overlap"], "overlap"),
    "ppt.check_contrast": checkContrast,
    "ppt.compare_before_after": compareBeforeAfter,
    "ppt.undo_batch": undoBatch,
    "ppt.insert_slides": insertSlides,
    "ppt.import_slides": insertSlides,
    "ppt.edit_chart": editChart,
    "ppt.edit_table": editTable,
    "ppt.edit_master": () => unsupportedAdvanced("Slide master mutation"),
    "ppt.edit_layout": editLayout,
    "ppt.edit_notes": () => unsupportedAdvanced("Speaker notes editing"),
    "ppt.bind_shape": applyBatch,
    "ppt.apply_ooxml_patch": () => unsupportedAdvanced("Direct OOXML patching; Cyrene must compile the patch through its typed server round-trip"),
    "ppt.execute_officejs": executeOfficeCommand,
    "ppt.replace_slide_ooxml": replaceSlideFromBase64,
  };
  const MUTATION_METHODS = new Set([
    "ppt.apply_batch", "ppt.create_slide", "ppt.duplicate_slide", "ppt.apply_slide_spec",
    "ppt.relayout_slide", "ppt.create_from_template", "ppt.replace_slide", "ppt.move_slide",
    "ppt.delete_slide", "ppt.undo_batch", "ppt.insert_slides", "ppt.import_slides",
    "ppt.edit_chart", "ppt.edit_table", "ppt.edit_layout", "ppt.bind_shape",
    "ppt.replace_slide_ooxml",
  ]);

  Office.onReady(async function (info) {
    if (!configuredLanguage) applyLanguage(Office.context.displayLanguage || navigator.language);
    if (info.host !== Office.HostType.PowerPoint) {
      setStatus("error", "statusUnsupportedHost", "statusUnsupportedHostDetail");
      return;
    }
    state.capabilities = {
      shapes: true,
      charts: hasApi("1.8"),
      tables: hasApi("1.8"),
      slideMaster: hasApi("1.3"),
      ooxml: hasApi("1.8"),
      notes: false,
      importSlides: hasApi("1.2"),
      progressiveCommit: true,
      chartModes: {
        visual: Office.context.requirements.isSetSupported("ImageCoercion", "1.1"),
        nativeEditable: hasApi("1.8"),
        directOfficeJs: false,
      },
      masterOperations: { inspect: hasApi("1.3"), applyLayout: hasApi("1.3"), editShapes: false },
      notesOperations: { read: false, edit: false },
      escapeOfficeJs: true,
      liveRevisionEvents: true,
      liveSelectionEvents: true,
      agentKit: agentKit,
      imageInsertion: { input: "png-base64", api: "Document.setSelectedDataAsync", requirementSet: "ImageCoercion 1.1", available: Office.context.requirements.isSetSupported("ImageCoercion", "1.1") },
      powerPointApi13: hasApi("1.3"),
      powerPointApi14: hasApi("1.4"),
      powerPointApi18: hasApi("1.8"),
      powerPointApi110: hasApi("1.10"),
      sharedRuntime11: Office.context.requirements.isSetSupported("SharedRuntime", "1.1"),
      methods: Object.keys(handlers),
    };
    state.capabilitiesReady = true;
    updateMetrics();
    try {
      await readDocumentInfo();
      await getSelection();
      await rememberSlideSignature(state.selectedSlideId);
      if (Office.context.document && Office.context.document.addHandlerAsync) {
        Office.context.document.addHandlerAsync(Office.EventType.DocumentSelectionChanged, async function () {
          if (state.mutationInFlight) return;
          const previousSlideId = state.selectedSlideId;
          const previousSignature = state.slideSignatures.get(previousSlideId) || "";
          const currentSignature = await slideContentSignature(previousSlideId);
          if (previousSignature && currentSignature !== previousSignature) {
            state.revision += 1;
            persistRevision();
          }
          if (previousSlideId) state.slideSignatures.set(previousSlideId, currentSignature);
          const context = await getSelection();
          await rememberSlideSignature(state.selectedSlideId);
          if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
          state.socket.send(JSON.stringify({
            type: "event",
            event: "selection_changed",
            revision: state.revision,
            document: state.document,
            selection: context.selection,
            source: "office_selection_event",
          }));
        });
      }
      connect();
    } catch (error) {
      setStatus("error", "statusInitializationFailed", "", asError(error).message);
    }
  });
  applyLanguage(navigator.language);
  syncAppearance();
  window.setInterval(syncAppearance, 5000);
  appearanceMedia.addEventListener("change", function () {
    if (document.documentElement.dataset.themeMode === "system") {
      appearanceSignature = "";
      syncAppearance();
    }
  });
  $("reconnect").addEventListener("click", connect);
}
