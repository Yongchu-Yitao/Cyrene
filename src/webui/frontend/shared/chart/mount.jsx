// Mounts declarative `:::chart` blocks into live ECharts widgets: parses the
// spec payload embedded by the markdown renderer, renders the chart plus any
// declared slider controls, and recomputes `binds` data when a slider moves.
// Instances are tracked per element so a container can be mounted/unmounted
// idempotently. Environments without echarts keep the raw spec text (the
// renderer embeds it inside each chart block as the default content).
(function (root) {
  "use strict";

  var instances = typeof WeakMap === "function" ? new WeakMap() : null;

  function isDarkMode() {
    var doc = root.document && root.document.documentElement;
    return !!(doc && doc.getAttribute && doc.getAttribute("data-theme") === "dark");
  }

  // Compute one y value per x from `binds` (arithmetic over the control
  // params plus `x`) or from the static `y` array.
  function computeSeries(payload, values) {
    var chartSpec = root.CyreneUI && root.CyreneUI.chartSpec;
    var compiled = null;
    if (payload["y-binds"]) {
      if (!chartSpec || typeof chartSpec.compileExpr !== "function") return null;
      compiled = chartSpec.compileExpr(payload["y-binds"]);
    }
    var x = Array.isArray(payload.x) ? payload.x : [];
    var staticY = Array.isArray(payload.y) ? payload.y : [];
    var series = [];
    for (var i = 0; i < x.length; i++) {
      if (compiled) {
        var env = { x: Number(x[i]) };
        var controls = Array.isArray(payload.controls) ? payload.controls : [];
        for (var c = 0; c < controls.length; c++) {
          var param = controls[c].param;
          env[param] = values && values[param] !== undefined ? values[param] : controls[c].default;
        }
        series.push(compiled.evaluate(env));
      } else {
        series.push(staticY[i] !== undefined ? staticY[i] : null);
      }
    }
    return series;
  }

  function buildOption(payload, values) {
    var options = payload.options || {};
    var seriesData = computeSeries(payload, values);
    var dark = isDarkMode();
    var textColor = dark ? "rgba(226,232,240,0.92)" : "rgba(51,65,85,0.9)";
    var lineColor = dark ? "rgba(148,163,184,0.45)" : "rgba(148,163,184,0.6)";
    var chartType = payload.type === "scatter" ? "scatter" : payload.type;
    var data = chartType === "scatter"
      ? (payload.x || []).map(function (xi, i) { return [xi, seriesData[i]]; })
      : seriesData;
    var option = {
      backgroundColor: "transparent",
      animation: false,
      grid: {
        show: options.grid !== false,
        left: 46,
        right: 16,
        top: options.title ? 46 : 20,
        bottom: 30,
        borderColor: lineColor,
      },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: payload.x || [],
        axisLine: { lineStyle: { color: lineColor } },
        axisLabel: { color: textColor },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: options["y-min"] !== undefined ? options["y-min"] : null,
        max: options["y-max"] !== undefined ? options["y-max"] : null,
        axisLine: { show: true, lineStyle: { color: lineColor } },
        axisLabel: { color: textColor },
        splitLine: { lineStyle: { color: lineColor } },
      },
      series: [{
        type: chartType,
        data: data,
        symbolSize: chartType === "scatter" ? 8 : 6,
        lineStyle: { width: 2 },
        itemStyle: { color: options.color || (dark ? "#7dd3fc" : "#4f7cff") },
      }],
    };
    if (options.title) {
      option.title = { text: String(options.title), left: "center", textStyle: { fontSize: 13, color: textColor } };
    }
    return option;
  }

  function mountOne(element) {
    if (!root.echarts || typeof root.echarts.init !== "function" || !instances) return null;
    var raw = element.getAttribute("data-wbc-chart");
    if (!raw) return null;
    var payload;
    try {
      payload = JSON.parse(raw);
    } catch (e) {
      element.classList.add("wbc-chart-error");
      element.setAttribute("data-wbc-chart-error", "invalid chart payload");
      return null;
    }
    if (!payload || (payload.type !== "line" && payload.type !== "scatter" && payload.type !== "bar")) {
      element.classList.add("wbc-chart-error");
      return null;
    }

    var wrap = document.createElement("div");
    wrap.className = "wbc-chart-wrap";
    var canvas = document.createElement("div");
    canvas.className = "wbc-chart-canvas";
    wrap.appendChild(canvas);

    // Hide the raw spec text once the live widget is in place; keep it in the
    // DOM so the fallback is one class flip away.
    var spec = element.querySelector(".wbc-chart-spec");
    if (spec) spec.classList.add("wbc-chart-spec-hidden");

    element.innerHTML = "";
    element.appendChild(wrap);

    var chart = root.echarts.init(canvas, isDarkMode() ? "dark" : null);
    chart.setOption(buildOption(payload, {}), true);

    var controlsRow = null;
    if (Array.isArray(payload.controls) && payload.controls.length) {
      controlsRow = document.createElement("div");
      controlsRow.className = "wbc-chart-controls";
      for (var i = 0; i < payload.controls.length; i++) {
        (function (control) {
          var min = Number(control.range[0]);
          var max = Number(control.range[1]);
          var step = Number(control.step);
          var label = document.createElement("label");
          label.className = "wbc-chart-control";
          var name = document.createElement("span");
          name.className = "wbc-chart-control-name";
          name.textContent = String(control.param);
          var input = document.createElement("input");
          input.type = "range";
          input.min = String(min);
          input.max = String(max);
          input.step = String(step);
          input.value = String(Number(control.default));
          input.setAttribute("aria-label", String(control.param));
          var value = document.createElement("span");
          value.className = "wbc-chart-control-value";
          label.appendChild(name);
          label.appendChild(input);
          label.appendChild(value);
          controlsRow.appendChild(label);
          function refreshValues() {
            value.textContent = input.value;
            var values = {};
            var inputs = controlsRow.querySelectorAll("input[type=range]");
            var controlsList = payload.controls || [];
            for (var j = 0; j < controlsList.length; j++) {
              var inputEl = inputs[j];
              values[controlsList[j].param] = Number(inputEl ? inputEl.value : controlsList[j].default);
            }
            chart.setOption(buildOption(payload, values), true);
          }
          input.addEventListener("input", refreshValues);
          refreshValues();
        })(payload.controls[i]);
      }
      wrap.appendChild(controlsRow);
    }

    var observer = null;
    function resizeChart() {
      if (root.document && root.document.body.classList.contains("wbc-resizing-side-agent")) return;
      chart.resize();
    }
    if (typeof ResizeObserver === "function") {
      observer = new ResizeObserver(resizeChart);
      observer.observe(wrap);
    }
    root.addEventListener("workbench:split-resize-end", resizeChart);

    instances.set(element, {
      chart: chart,
      observer: observer,
      element: element,
      dispose: function () {
        if (observer) observer.disconnect();
        root.removeEventListener("workbench:split-resize-end", resizeChart);
        chart.dispose();
      },
    });
    return instances.get(element);
  }

  // ---- :::button blocks ---------------------------------------------------
  // Click events flow through one protocol shape (Slack block_actions-style):
  // { type, action_id, block_id, value, mode, message_id, event_id, user_id,
  // timestamp }. `mode: "local"` resolves in the frontend (the event is
  // dispatched as a bubbling `wbc:block-action` CustomEvent for hosts to
  // subscribe to); `mode: "model"` uses the same payload so a host/runtime
  // can forward it to the model later. Every click gets a unique event_id,
  // and a triggered button is locked briefly so one physical click produces
  // exactly one event.

  var eventSeq = 0;
  function eventId() {
    eventSeq += 1;
    return Date.now().toString(36) + "-" + eventSeq.toString(36)
      + "-" + Math.random().toString(36).slice(2, 8);
  }

  function hashString(text) {
    var hash = 5381;
    for (var i = 0; i < text.length; i++) {
      hash = ((hash << 5) + hash + text.charCodeAt(i)) | 0;
    }
    return (hash >>> 0).toString(36);
  }

  function mountButton(element, context) {
    var raw = element.getAttribute("data-wbc-button");
    if (!raw) return null;
    var spec;
    try {
      spec = JSON.parse(raw);
    } catch (e) {
      element.classList.add("wbc-button-error");
      return null;
    }
    if (!spec || typeof spec.label !== "string" || typeof spec.action_id !== "string") {
      element.classList.add("wbc-button-error");
      return null;
    }
    var blockId = element.getAttribute("data-wbc-block-id");
    if (!blockId) {
      blockId = "btn-" + hashString(raw);
      element.setAttribute("data-wbc-block-id", blockId);
    }

    var button = document.createElement("button");
    button.type = "button";
    button.className = "wbc-button-btn wbc-button-btn--" + (spec.style || "default");
    button.textContent = spec.label;
    button.setAttribute("data-action-id", spec.action_id);
    if (spec.disabled) button.disabled = true;

    var fired = false;
    function release() {
      button.disabled = false;
      button.classList.remove("wbc-button-triggered");
      fired = false;
    }
    function dispatch(payload) {
      if (typeof root.CustomEvent === "function") {
        element.dispatchEvent(new root.CustomEvent("wbc:block-action", {
          bubbles: true,
          detail: payload,
        }));
      }
    }
    button.addEventListener("click", function () {
      if (fired || button.disabled) return;
      fired = true;
      button.disabled = true;
      button.classList.add("wbc-button-triggered");
      var payload = {
        type: "block_actions",
        action_id: spec.action_id,
        block_id: blockId,
        value: spec.value || "",
        mode: spec.mode || "local",
        message_id: (context && context.messageId) || "",
        event_id: eventId(),
        user_id: (context && context.userId) || "",
        timestamp: Date.now(),
      };
      if (spec.mode === "model") {
        // Forward to the runtime: the endpoint flips this block to disabled
        // in the stored message (chat.update semantics) and routes the event
        // through the send pipeline. On success the button stays disabled —
        // the refreshed message re-renders it inert. On failure it re-arms.
        var chatId = (context && context.chatId) || "";
        if (chatId && typeof root.fetch === "function") {
          var settled = false;
          setTimeout(function () { if (!settled) release(); }, 600);
          root.fetch("/api/workbench/chats/" + encodeURIComponent(chatId) + "/actions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              actionId: spec.action_id,
              value: spec.value || "",
              messageId: payload.message_id,
              eventId: payload.event_id,
            }),
          }).then(function (response) {
            settled = true;
            if (response.ok) {
              dispatch(payload);
            } else {
              release();
              dispatch(payload);
            }
          }).catch(function () {
            settled = true;
            release();
            dispatch(payload);
          });
          return;
        }
        // No runtime context (read-only surface): degrade to the local event.
        dispatch(payload);
        setTimeout(release, 600);
        return;
      }
      dispatch(payload);
      setTimeout(release, 600);
    });

    var specEl = element.querySelector(".wbc-button-spec");
    if (specEl) specEl.classList.add("wbc-button-spec-hidden");
    element.innerHTML = "";
    element.appendChild(button);

    instances.set(element, { element: element, dispose: function () {} });
    return instances.get(element);
  }

  function mountElement(element, context) {
    if (element.hasAttribute("data-wbc-chart")) return mountOne(element);
    if (element.hasAttribute("data-wbc-button")) return mountButton(element, context);
    return null;
  }

  function mount(container, context) {
    if (!container || !instances) return 0;
    var elements = container.querySelectorAll("[data-wbc-chart], [data-wbc-button]");
    var count = 0;
    for (var i = 0; i < elements.length; i++) {
      if (instances.has(elements[i])) continue;
      if (mountElement(elements[i], context)) count += 1;
    }
    return count;
  }

  function dispose(container) {
    if (!container || !instances) return;
    var elements = container.querySelectorAll("[data-wbc-chart], [data-wbc-button]");
    for (var i = 0; i < elements.length; i++) {
      var entry = instances.get(elements[i]);
      if (entry) {
        entry.dispose();
        instances.delete(elements[i]);
      }
    }
  }

  var service = {
    mount: mount,
    dispose: dispose,
    buildOption: buildOption,
    computeSeries: computeSeries,
    eventId: eventId,
  };
  root.CyreneUI.chart = root.CyreneUI.register("chart", service);
})(window);
