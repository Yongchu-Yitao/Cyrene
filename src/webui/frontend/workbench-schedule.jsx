// Workbench Schedule / Calendar page.
//
// Fully independent from the removed classic scheduled-tasks UI
// in tasks.jsx / tasks-calendar.jsx): its own model, components and styles. Talks
// ONLY to the Workbench-scoped `/api/workbench/schedule/*` backend.
//
// What it integrates (the "backend agent 定时任务 + 日程" the calendar surfaces):
//   • scheduled_tasks (agent 定时任务) — cron / interval / once, expanded into
//     concrete dated events by the backend `/occurrences` endpoint.
//   • entity deadlines (任务截止) — entities carrying a `due_date`, shown all-day.
//
// Timezone: next_run is stored and returned as UTC ISO, while cron wall-clock
// fields are evaluated in the persisted IANA timezone chosen by this desktop.
(function () {
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useMemo = React.useMemo;
  var useRef = React.useRef;

  var schedulePageCache = { ranges: {}, pending: {} };

  function fetchScheduleRange(key, API, startISO, endISO) {
    if (schedulePageCache.pending[key]) return schedulePageCache.pending[key];
    var request = API.occurrences(startISO, endISO).then(function (events) {
      schedulePageCache.ranges[key] = { value: events, updatedAt: Date.now() };
      return events;
    });
    schedulePageCache.pending[key] = request;
    request.then(function () {
      if (schedulePageCache.pending[key] === request) delete schedulePageCache.pending[key];
    }, function () {
      if (schedulePageCache.pending[key] === request) delete schedulePageCache.pending[key];
    });
    return request;
  }

  var HOUR_PX = 52;            // height of one hour row on the timeline
  var DAY_PX = HOUR_PX * 24;
  var WEEKDAY_CN = ["日", "一", "二", "三", "四", "五", "六"];
  var WEEKDAY_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function T(key, params, fallback) {
    return window.CyreneUI.require("i18n").t(key, params, fallback);
  }
  function currentLang() {
    return window.CyreneUI.require("i18n").getLang();
  }
  function weekdayName(d, short) {
    return currentLang() === "zh" ? WEEKDAY_CN[d.getDay()] : WEEKDAY_EN[d.getDay()];
  }

  // ── date helpers ─────────────────────────────────────────────────────

  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function startOfDay(d) { var x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
  function addDays(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function addMonths(d, n) { var x = new Date(d); x.setDate(1); x.setMonth(x.getMonth() + n); return x; }
  function startOfWeekMon(d) {
    var x = startOfDay(d);
    var dow = x.getDay();           // 0=Sun
    var back = dow === 0 ? 6 : dow - 1;
    return addDays(x, -back);
  }
  function isSameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  function clockHM(d) { return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); }
  function minutesOfDay(d) { return d.getHours() * 60 + d.getMinutes(); }

  function toLocalInputValue(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
      "T" + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function toTimezoneInputValue(d, timezoneName) {
    try {
      var parts = new Intl.DateTimeFormat("en-US", {
        timeZone: timezoneName,
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      }).formatToParts(d);
      var values = {};
      parts.forEach(function (part) { if (part.type !== "literal") values[part.type] = part.value; });
      return values.year + "-" + values.month + "-" + values.day + "T" + values.hour + ":" + values.minute;
    } catch (e) {
      return toLocalInputValue(d);
    }
  }
  function timezoneOffsetMs(d, timezoneName) {
    var parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezoneName,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(d);
    var values = {};
    parts.forEach(function (part) { if (part.type !== "literal") values[part.type] = Number(part.value); });
    return Date.UTC(values.year, values.month - 1, values.day, values.hour, values.minute, values.second) - d.getTime();
  }
  function dateFromTimezoneInput(value, timezoneName) {
    var match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!match) return new Date(NaN);
    var wallUtc = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]), 0);
    try {
      var candidate = new Date(wallUtc);
      for (var i = 0; i < 3; i++) {
        candidate = new Date(wallUtc - timezoneOffsetMs(candidate, timezoneName));
      }
      // Reject nonexistent local times during the spring DST jump instead of
      // silently scheduling a different wall-clock value.
      return toTimezoneInputValue(candidate, timezoneName) === value ? candidate : new Date(NaN);
    } catch (e) {
      return new Date(value);
    }
  }
  function toDateInputValue(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function dayHeading(d) {
    if (currentLang() !== "zh") return d.toLocaleDateString("en-US", { weekday: "short", year: "numeric", month: "short", day: "numeric" });
    return d.getFullYear() + "年" + (d.getMonth() + 1) + "月" + d.getDate() + "日 周" + WEEKDAY_CN[d.getDay()];
  }
  function monthHeading(d) {
    if (currentLang() !== "zh") return d.toLocaleDateString("en-US", { year: "numeric", month: "long" });
    return d.getFullYear() + "年" + (d.getMonth() + 1) + "月";
  }
  function weekHeading(d) {
    var s = startOfWeekMon(d), e = addDays(s, 6);
    if (currentLang() !== "zh") {
      return s.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " - " +
        e.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    }
    if (s.getMonth() === e.getMonth()) {
      return s.getFullYear() + "年" + (s.getMonth() + 1) + "月 " + s.getDate() + "–" + e.getDate() + " 日";
    }
    return (s.getMonth() + 1) + "月" + s.getDate() + "日 – " + (e.getMonth() + 1) + "月" + e.getDate() + "日";
  }

  // ── categories (driven by real backend data) ─────────────────────────

  var CATEGORIES = [
    { id: "task_recurring", labelKey: "schedule.category.taskRecurring", hintKey: "schedule.category.taskRecurringHint" },
    { id: "task_once", labelKey: "schedule.category.taskOnce", hintKey: "schedule.category.taskOnceHint" },
    { id: "entity_due", labelKey: "schedule.category.entityDue", hintKey: "schedule.category.entityDueHint" },
  ];
  function categoryLabel(id) {
    var item = CATEGORIES.find(function (c) { return c.id === id; });
    return item ? T(item.labelKey) : id;
  }

  // ── API model ────────────────────────────────────────────────────────

  async function jsonOrThrow(r) {
    var payload = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(payload.error || payload.detail || ("HTTP " + r.status));
    return payload;
  }
  function scheduleApi(workspace) {
    function qs(extra) {
      return "workspace=" + encodeURIComponent(workspace || "default") + (extra ? "&" + extra : "");
    }
    return {
      occurrences: async function (startISO, endISO) {
        var r = await fetch("/api/workbench/schedule/occurrences?" + qs("start=" + encodeURIComponent(startISO) + "&end=" + encodeURIComponent(endISO)));
        var p = await jsonOrThrow(r);
        return (p && p.events) || [];
      },
      create: async function (body) {
        var r = await fetch("/api/workbench/schedule/tasks?" + qs(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        return jsonOrThrow(r);
      },
      update: async function (id, body) {
        var r = await fetch("/api/workbench/schedule/tasks/" + encodeURIComponent(id) + "?" + qs(), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        return jsonOrThrow(r);
      },
      remove: async function (id) {
        var r = await fetch("/api/workbench/schedule/tasks/" + encodeURIComponent(id) + "?" + qs(), { method: "DELETE" });
        return jsonOrThrow(r);
      },
      runs: async function (id) {
        var r = await fetch("/api/workbench/schedule/tasks/" + encodeURIComponent(id) + "/runs?" + qs("limit=20"));
        var p = await jsonOrThrow(r);
        return (p && p.runs) || [];
      },
      getEntity: async function (id) {
        var r = await fetch("/api/entities/" + encodeURIComponent(id));
        return jsonOrThrow(r);
      },
      createEntity: async function (body) {
        var r = await fetch("/api/entities", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        return jsonOrThrow(r);
      },
      updateEntity: async function (id, body) {
        var r = await fetch("/api/entities/" + encodeURIComponent(id), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        return jsonOrThrow(r);
      },
      removeEntity: async function (id) {
        var r = await fetch("/api/entities/" + encodeURIComponent(id), { method: "DELETE" });
        return jsonOrThrow(r);
      },
    };
  }

  // ── schedule <-> form mapping ────────────────────────────────────────
  function localScheduleTimezone() {
    try {
      var configured = localStorage.getItem("cyrene-timezone") || "";
      if (configured) {
        new Intl.DateTimeFormat("en-US", { timeZone: configured }).format();
        return configured;
      }
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch (e) {
      return "UTC";
    }
  }

  // Build a backend schedule spec from the friendly form. Cron fields retain
  // the chosen local wall-clock components and carry an IANA timezone so daily,
  // weekly and monthly schedules do not drift when the UTC offset changes.
  function buildSchedule(repeat, startDate, cronText, intervalValue, intervalUnit, scheduleTimezone, wallValue) {
    scheduleTimezone = scheduleTimezone || localScheduleTimezone();
    if (repeat === "none") return { schedule_type: "once", schedule_value: startDate.toISOString(), schedule_timezone: scheduleTimezone };
    if (repeat === "cron") return { schedule_type: "cron", schedule_value: (cronText || "").trim(), schedule_timezone: scheduleTimezone };
    if (repeat === "interval") {
      var mult = intervalUnit === "h" ? 3600 : intervalUnit === "d" ? 86400 : 60;
      var secs = Math.max(1, Math.round(Number(intervalValue || 0) * mult));
      return { schedule_type: "interval", schedule_value: String(secs), schedule_timezone: scheduleTimezone };
    }
    var wallMatch = String(wallValue || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    var m = wallMatch ? Number(wallMatch[5]) : startDate.getMinutes();
    var h = wallMatch ? Number(wallMatch[4]) : startDate.getHours();
    var dayOfMonth = wallMatch ? Number(wallMatch[3]) : startDate.getDate();
    var dayOfWeek = wallMatch
      ? new Date(Date.UTC(Number(wallMatch[1]), Number(wallMatch[2]) - 1, dayOfMonth)).getUTCDay()
      : startDate.getDay();
    if (repeat === "daily") return { schedule_type: "cron", schedule_value: m + " " + h + " * * *", schedule_timezone: scheduleTimezone };
    if (repeat === "weekly") return { schedule_type: "cron", schedule_value: m + " " + h + " * * " + dayOfWeek, schedule_timezone: scheduleTimezone };
    if (repeat === "monthly") return { schedule_type: "cron", schedule_value: m + " " + h + " " + dayOfMonth + " * *", schedule_timezone: scheduleTimezone };
    return { schedule_type: "once", schedule_value: startDate.toISOString(), schedule_timezone: scheduleTimezone };
  }

  // Recover friendly form state from an existing task (best-effort).
  function parseSchedule(task) {
    var out = { repeat: "none", start: new Date(), cronText: "", intervalValue: 1, intervalUnit: "h" };
    if (!task) return out;
    var stype = task.schedule_type, sval = String(task.schedule_value || "");
    if (stype === "once") {
      out.repeat = "none";
      out.start = new Date(task.next_run || task.schedule_value || Date.now());
      return out;
    }
    if (stype === "interval") {
      out.repeat = "interval";
      out.start = new Date(task.next_run || Date.now());
      var s = Number(sval) || 3600;
      if (s % 86400 === 0) { out.intervalValue = s / 86400; out.intervalUnit = "d"; }
      else if (s % 3600 === 0) { out.intervalValue = s / 3600; out.intervalUnit = "h"; }
      else { out.intervalValue = Math.round(s / 60); out.intervalUnit = "m"; }
      return out;
    }
    if (stype === "cron") {
      var p = sval.trim().split(/\s+/);
      out.start = new Date(task.next_run || Date.now());
      if (p.length === 5 && /^\d+$/.test(p[0]) && /^\d+$/.test(p[1])) {
        var mi = parseInt(p[0], 10), ho = parseInt(p[1], 10), dom = p[2], mo = p[3], dow = p[4];
        // next_run already represents the correct instant; presenting that
        // instant locally gives the edit form the same wall-clock value.
        var d = out.start;
        if (dom === "*" && mo === "*" && dow === "*") { out.repeat = "daily"; out.start = d; return out; }
        if (dom === "*" && mo === "*" && /^\d+$/.test(dow)) {
          out.repeat = "weekly"; out.start = d; return out;
        }
        if (/^\d+$/.test(dom) && mo === "*" && dow === "*") {
          out.repeat = "monthly"; out.start = d; return out;
        }
      }
      out.repeat = "cron"; out.cronText = sval;
      return out;
    }
    return out;
  }

  // ── timeline layout (overlap → side-by-side lanes) ───────────────────
  function layoutDayEvents(list) {
    var sorted = list.slice().sort(function (a, b) { return (a._start - b._start) || (a._end - b._end); });
    var clusters = [], cur = [], curEnd = null;
    sorted.forEach(function (ev) {
      if (cur.length && ev._start >= curEnd) { clusters.push(cur); cur = []; curEnd = null; }
      cur.push(ev);
      curEnd = curEnd == null ? ev._end : new Date(Math.max(curEnd.getTime(), ev._end.getTime()));
    });
    if (cur.length) clusters.push(cur);
    clusters.forEach(function (cluster) {
      var laneEnds = [];
      cluster.forEach(function (ev) {
        var placed = false;
        for (var i = 0; i < laneEnds.length; i++) {
          if (ev._start >= laneEnds[i]) { ev._lane = i; laneEnds[i] = ev._end; placed = true; break; }
        }
        if (!placed) { ev._lane = laneEnds.length; laneEnds.push(ev._end); }
      });
      cluster.forEach(function (ev) { ev._lanes = laneEnds.length; });
    });
    return sorted;
  }

  // Decorate raw events with parsed Date objects + day-local flags.
  function decorate(events) {
    return events.map(function (ev) {
      var start = new Date(ev.start);
      var end = ev.end ? new Date(ev.end) : new Date(start.getTime() + 30 * 60000);
      return Object.assign({}, ev, { _start: start, _end: end });
    });
  }

  // ── small UI atoms ───────────────────────────────────────────────────

  function Dot(props) { return React.createElement("span", { className: "wb-sched-dot cat-" + props.cat }); }

  function svg(paths, extra) {
    var s = Object.assign({ width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" }, extra || {});
    return React.createElement.apply(null, ["svg", s].concat(paths.map(function (d, i) {
      return typeof d === "string" ? React.createElement("path", { key: i, d: d }) : d;
    })));
  }

  // ── mini month (calendar rail) ───────────────────────────────────────
  function MiniMonth(props) {
    var view = props.viewMonth;
    var first = new Date(view.getFullYear(), view.getMonth(), 1);
    var startDow = first.getDay();
    var pad = startDow === 0 ? 6 : startDow - 1;
    var gridStart = addDays(startOfDay(first), -pad);
    var today = new Date();
    var cells = [];
    for (var i = 0; i < 42; i++) cells.push(addDays(gridStart, i));
    return React.createElement(
      "div", { className: "wb-sched-mini" },
      React.createElement(
        "div", { className: "wb-sched-mini-head" },
        React.createElement("b", null, monthHeading(view)),
        React.createElement(
          "div", { className: "wb-sched-mini-nav" },
          React.createElement("button", { type: "button", onClick: function () { props.onViewMonth(addMonths(view, -1)); }, title: T("schedule.previousMonth") }, "‹"),
          React.createElement("button", { type: "button", onClick: function () { props.onViewMonth(addMonths(view, 1)); }, title: T("schedule.nextMonth") }, "›")
        )
      ),
      React.createElement(
        "div", { className: "wb-sched-mini-grid" },
        WEEKDAY_CN.map(function (w, idx) {
          // Monday-first header order
          var order = currentLang() === "zh" ? ["一", "二", "三", "四", "五", "六", "日"] : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
          return idx < 7 ? React.createElement("span", { key: "h" + idx, className: "wb-sched-mini-dow" }, order[idx]) : null;
        }),
        cells.map(function (d, i) {
          var other = d.getMonth() !== view.getMonth();
          var isToday = isSameDay(d, today);
          var isSel = isSameDay(d, props.selected);
          var has = props.markedDays && props.markedDays[d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate()];
          var cls = "wb-sched-mini-cell" + (other ? " other" : "") + (isToday ? " today" : "") + (isSel ? " sel" : "");
          return React.createElement(
            "button", { key: i, type: "button", className: cls, onClick: function () { props.onPick(startOfDay(d)); } },
            d.getDate(),
            has ? React.createElement("i", { className: "wb-sched-mini-mark" }) : null
          );
        })
      )
    );
  }

  // ── calendar rail (column 2) ─────────────────────────────────────────
  function CalendarRail(props) {
    return React.createElement(
      "aside", { className: "wb-sched-rail workbench-integrated-rail" + (props.sidebarCollapsed ? " is-collapsed" : "") },
      React.createElement(
        "div", { className: "wb-sched-rail-head workbench-integrated-rail-head" },
        props.onBack && React.createElement(
          "button", { type: "button", className: "wb-sched-rail-back", onClick: props.onBack, title: T("schedule.backToWorkbench") },
          svg(["m15 18-6-6 6-6"], { width: 17, height: 17 })
        ),
        React.createElement("span", null, T("schedule.calendar")),
        props.collapseControl
      ),
      React.createElement(
        "div", { className: "wb-sched-rail-scroll workbench-integrated-rail-body" },
        React.createElement(MiniMonth, {
          viewMonth: props.viewMonth, onViewMonth: props.onViewMonth,
          selected: props.anchorDate, onPick: props.onPickDay, markedDays: props.markedDays,
        }),
        React.createElement(
          "div", { className: "wb-sched-cal-section" },
          React.createElement("div", { className: "wb-sched-cal-section-title" }, T("schedule.myCalendar")),
          CATEGORIES.map(function (c) {
            var on = props.visible[c.id];
            return React.createElement(
              "button", {
                key: c.id, type: "button", className: "wb-sched-cal-item" + (on ? "" : " off"),
                onClick: function () { props.onToggle(c.id); }, title: T(c.hintKey),
              },
              React.createElement("span", { className: "wb-sched-cal-check cat-" + c.id + (on ? " on" : "") },
                on ? svg(["M5 12l4 4 10-10"], { width: 12, height: 12, strokeWidth: 2.6 }) : null),
              React.createElement("span", { className: "wb-sched-cal-name" }, T(c.labelKey)),
              React.createElement("span", { className: "wb-sched-cal-count" }, props.counts[c.id] || 0)
            );
          })
        )
      ),
      props.moduleDock
    );
  }

  // ── event chip / block ───────────────────────────────────────────────
  function TimedBlock(props) {
    var ev = props.ev;
    var topMin = minutesOfDay(ev._dayStart || ev._start);
    var durMin = Math.max(24, (ev._dayEnd || ev._end).getTime() / 60000 - (ev._dayStart || ev._start).getTime() / 60000);
    var lanes = ev._lanes || 1, lane = ev._lane || 0;
    var widthPct = 100 / lanes;
    var blockHeight = Math.max(22, durMin / 60 * HOUR_PX - 2);
    var compact = blockHeight < 40;
    var style = {
      top: (topMin / 60 * HOUR_PX) + "px",
      height: blockHeight + "px",
      left: "calc(" + (lane * widthPct) + "% + 2px)",
      width: "calc(" + widthPct + "% - 4px)",
    };
    return React.createElement(
      "button", {
        type: "button",
        className: "wb-sched-block cat-" + ev.category + (compact ? " compact" : "") + (ev.status === "paused" ? " paused" : "") + (props.active ? " active" : ""),
        style: style,
        onClick: function (e) { e.stopPropagation(); props.onSelect(ev); },
        onContextMenu: function (e) { props.onContextMenu(ev, e); },
        title: ev.title,
      },
      React.createElement("span", { className: "wb-sched-block-time" }, clockHM(ev._start) + " – " + clockHM(ev._end)),
      React.createElement("span", { className: "wb-sched-block-title" }, ev.title)
    );
  }

  function HourGutter() {
    var rows = [];
    for (var h = 0; h < 24; h++) {
      rows.push(React.createElement("div", { key: h, className: "wb-sched-hour-label", style: { height: HOUR_PX + "px" } },
        React.createElement("span", null, h === 0 ? "" : pad2(h) + ":00")));
    }
    return React.createElement("div", { className: "wb-sched-gutter" }, rows);
  }

  function HourLines() {
    var lines = [];
    for (var h = 0; h <= 24; h++) lines.push(React.createElement("div", { key: h, className: "wb-sched-hline", style: { top: (h * HOUR_PX) + "px" } }));
    return React.createElement("div", { className: "wb-sched-hlines" }, lines);
  }

  function NowLine(props) {
    if (!props.show) return null;
    var min = minutesOfDay(new Date());
    return React.createElement("div", { className: "wb-sched-nowline", style: { top: (min / 60 * HOUR_PX) + "px" } }, React.createElement("span"));
  }

  // ── day view ─────────────────────────────────────────────────────────
  function DayView(props) {
    var day = props.anchorDate;
    var dayStart = startOfDay(day), dayEnd = addDays(dayStart, 1);
    var timed = [], allDay = [];
    props.events.forEach(function (ev) {
      if (ev.all_day) { if (ev._start >= dayStart && ev._start < dayEnd) allDay.push(ev); return; }
      if (ev._end > dayStart && ev._start < dayEnd) timed.push(ev);
    });
    layoutDayEvents(timed);
    var scrollRef = useRef(null);
    useEffect(function () { if (scrollRef.current) scrollRef.current.scrollTop = 7 * HOUR_PX; }, []);
    return React.createElement(
      "div", { className: "wb-sched-dayview" },
      React.createElement(AllDayRow, { items: allDay, cols: 1, activeId: props.activeId, onSelect: props.onSelect, onContextMenu: props.onContextMenu }),
      React.createElement(
        "div", { className: "wb-sched-scroll", ref: scrollRef },
        React.createElement(
          "div", { className: "wb-sched-timeline", style: { height: DAY_PX + "px" } },
          React.createElement(HourGutter),
          React.createElement(
            "div", { className: "wb-sched-canvas" },
            React.createElement(HourLines),
            React.createElement(NowLine, { show: isSameDay(day, new Date()) }),
            timed.map(function (ev) {
              return React.createElement(TimedBlock, { key: ev.id, ev: ev, active: ev.id === props.activeId, onSelect: props.onSelect, onContextMenu: props.onContextMenu });
            })
          )
        )
      )
    );
  }

  function AllDayRow(props) {
    if (!props.items || !props.items.length) return null;
    return React.createElement(
      "div", { className: "wb-sched-allday" },
      React.createElement("span", { className: "wb-sched-allday-label" }, T("schedule.allDay")),
      React.createElement(
        "div", { className: "wb-sched-allday-items" },
        props.items.map(function (ev) {
          return React.createElement(
            "button", {
              key: ev.id, type: "button",
              className: "wb-sched-allday-chip cat-" + ev.category + (ev.id === props.activeId ? " active" : ""),
              onClick: function () { props.onSelect(ev); },
              onContextMenu: function (e) { props.onContextMenu(ev, e); },
              title: ev.title,
            },
            React.createElement(Dot, { cat: ev.category }),
            React.createElement("span", null, ev.title)
          );
        })
      )
    );
  }

  // ── week view ────────────────────────────────────────────────────────
  function WeekView(props) {
    var weekStart = startOfWeekMon(props.anchorDate);
    var days = [];
    for (var i = 0; i < 7; i++) days.push(addDays(weekStart, i));
    var today = new Date();
    var scrollRef = useRef(null);
    useEffect(function () { if (scrollRef.current) scrollRef.current.scrollTop = 7 * HOUR_PX; }, []);

    var allDayByCol = days.map(function () { return []; });
    var timedByCol = days.map(function () { return []; });
    props.events.forEach(function (ev) {
      var ds = startOfDay(ev._start);
      var idx = Math.round((ds - weekStart) / 86400000);
      if (idx < 0 || idx > 6) return;
      if (ev.all_day) allDayByCol[idx].push(ev); else timedByCol[idx].push(ev);
    });
    timedByCol.forEach(function (list) { layoutDayEvents(list); });

    return React.createElement(
      "div", { className: "wb-sched-weekview" },
      React.createElement(
        "div", { className: "wb-sched-week-head" },
        React.createElement("span", { className: "wb-sched-week-corner" }),
        days.map(function (d, i) {
          var isToday = isSameDay(d, today);
          return React.createElement(
            "button", {
              key: i, type: "button", className: "wb-sched-week-daycol" + (isToday ? " today" : ""),
              onClick: function () { props.onPickDay(startOfDay(d)); },
            },
            React.createElement("small", null, currentLang() === "zh" ? "周" + WEEKDAY_CN[d.getDay()] : WEEKDAY_EN[d.getDay()]),
            React.createElement("b", null, d.getDate())
          );
        })
      ),
      (allDayByCol.some(function (l) { return l.length; })) && React.createElement(
        "div", { className: "wb-sched-week-allday" },
        React.createElement("span", { className: "wb-sched-allday-label" }, T("schedule.allDay")),
        React.createElement(
          "div", { className: "wb-sched-week-allday-cols" },
          allDayByCol.map(function (list, i) {
            return React.createElement("div", { key: i, className: "wb-sched-week-allday-col" },
              list.map(function (ev) {
                return React.createElement("button", {
                  key: ev.id, type: "button",
                  className: "wb-sched-allday-chip cat-" + ev.category + (ev.id === props.activeId ? " active" : ""),
                  onClick: function () { props.onSelect(ev); },
                  onContextMenu: function (e) { props.onContextMenu(ev, e); },
                  title: ev.title,
                }, React.createElement(Dot, { cat: ev.category }), React.createElement("span", null, ev.title));
              }));
          })
        )
      ),
      React.createElement(
        "div", { className: "wb-sched-scroll", ref: scrollRef },
        React.createElement(
          "div", { className: "wb-sched-timeline week", style: { height: DAY_PX + "px" } },
          React.createElement(HourGutter),
          React.createElement(
            "div", { className: "wb-sched-week-cols" },
            days.map(function (d, i) {
              return React.createElement(
                "div", { key: i, className: "wb-sched-week-col" + (isSameDay(d, today) ? " today" : "") },
                React.createElement(HourLines),
                isSameDay(d, today) && React.createElement(NowLine, { show: true }),
                timedByCol[i].map(function (ev) {
                  return React.createElement(TimedBlock, { key: ev.id, ev: ev, active: ev.id === props.activeId, onSelect: props.onSelect, onContextMenu: props.onContextMenu });
                })
              );
            })
          )
        )
      )
    );
  }

  // ── month view ───────────────────────────────────────────────────────
  function MonthView(props) {
    var view = props.anchorDate;
    var first = new Date(view.getFullYear(), view.getMonth(), 1);
    var pad = first.getDay() === 0 ? 6 : first.getDay() - 1;
    var gridStart = addDays(startOfDay(first), -pad);
    var today = new Date();
    var cells = [];
    for (var i = 0; i < 42; i++) cells.push(addDays(gridStart, i));

    var byDay = {};
    props.events.forEach(function (ev) {
      var ds = startOfDay(ev._start);
      var key = ds.getFullYear() + "-" + ds.getMonth() + "-" + ds.getDate();
      (byDay[key] = byDay[key] || []).push(ev);
    });

    return React.createElement(
      "div", { className: "wb-sched-monthview" },
      React.createElement(
        "div", { className: "wb-sched-month-dow" },
        (currentLang() === "zh" ? ["一", "二", "三", "四", "五", "六", "日"] : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]).map(function (w, i) {
          return React.createElement("span", { key: i }, currentLang() === "zh" ? "周" + w : w);
        })
      ),
      React.createElement(
        "div", { className: "wb-sched-month-grid" },
        cells.map(function (d, i) {
          var other = d.getMonth() !== view.getMonth();
          var isToday = isSameDay(d, today);
          var key = d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate();
          var list = (byDay[key] || []).slice().sort(function (a, b) { return a._start - b._start; });
          return React.createElement(
            "div", { key: i, className: "wb-sched-month-cell" + (other ? " other" : "") + (isToday ? " today" : "") },
            React.createElement(
              "button", {
                type: "button", className: "wb-sched-month-num",
                onClick: function () { props.onPickDay(startOfDay(d)); },
              }, d.getDate()),
            React.createElement(
              "div", { className: "wb-sched-month-events" },
              list.slice(0, 3).map(function (ev) {
                return React.createElement(
                  "button", {
                    key: ev.id, type: "button",
                    className: "wb-sched-month-chip cat-" + ev.category + (ev.id === props.activeId ? " active" : ""),
                    onClick: function () { props.onSelect(ev); },
                    onContextMenu: function (e) { props.onContextMenu(ev, e); },
                    title: ev.title,
                  },
                  !ev.all_day && React.createElement("small", null, clockHM(ev._start)),
                  React.createElement("span", null, ev.title)
                );
              }),
              list.length > 3 && React.createElement(
                "button", { type: "button", className: "wb-sched-month-more", onClick: function () { props.onPickDay(startOfDay(d)); } },
                "+" + (list.length - 3))
            )
          );
        })
      )
    );
  }

  // ── detail panel (column 4) ──────────────────────────────────────────
  function DetailPanel(props) {
    var ev = props.event;
    var isTask = ev.source === "task";
    var tab = props.tab, setTab = props.setTab;
    var statusTone = ev.status === "paused" ? "amber" : ev.status === "completed" || ev.status === "done" ? "slate" : "green";
    var statusText = ev.status === "paused" ? T("status.paused") : ev.status === "completed" || ev.status === "done" ? T("status.done") : T("status.running");

    var tabs = isTask ? [{ id: "detail", label: T("schedule.detail") }, { id: "runs", label: T("schedule.runHistory") }] : [{ id: "detail", label: T("schedule.detail") }];

    var detailContent = React.createElement(
          React.Fragment, null,
          React.createElement(
            "div", { className: "wb-sched-detail-head" },
            React.createElement("span", { className: "wb-sched-detail-dot cat-" + ev.category }),
            React.createElement("b", { className: "wb-sched-detail-title", title: ev.title }, ev.title)
          ),
          React.createElement(
            "div", { className: "wb-sched-detail-meta" },
            React.createElement(MetaRow, { icon: svg(["M12 7v5l3 2", React.createElement("circle", { key: "c", cx: 12, cy: 12, r: 9 })]), value: detailTimeText(ev) }),
            isTask && React.createElement(MetaRow, { icon: svg(["M3 2v6h6", "M3 8a9 9 0 1 0 2.6-5"]), value: ev.recurrence || "—" }),
            React.createElement(MetaRow, {
              icon: svg([React.createElement("circle", { key: "c", cx: 12, cy: 12, r: 9 })]),
              value: React.createElement("span", { className: "wb-sched-badge " + statusTone }, statusText),
            }),
            React.createElement(MetaRow, {
              icon: svg(["M4 7h16M4 12h16M4 17h10"]),
              value: categoryLabel(ev.category),
            })
          ),
          isTask && React.createElement(
            "div", { className: "wb-sched-detail-sec" },
            React.createElement("div", { className: "wb-sched-detail-sec-title" }, T("schedule.taskContent")),
            React.createElement("p", { className: "wb-sched-detail-prompt" }, ev.title)
          ),
          isTask && React.createElement(
            "div", { className: "wb-sched-detail-grid" },
            React.createElement(KV, { k: T("schedule.nextRun"), v: absTimeText(ev.next_run) }),
            React.createElement(KV, { k: T("schedule.lastRun"), v: absTimeText(ev.last_run) })
          ),
          !isTask && React.createElement(
            "div", { className: "wb-sched-detail-grid" },
            React.createElement(KV, { k: T("schedule.type"), v: ev.entity_type || "—" }),
            React.createElement(KV, { k: T("create.task.priority"), v: ev.priority ? T("priority." + ev.priority, null, ev.priority) : "—" })
          ),
          !isTask && ev.content && React.createElement(
            "div", { className: "wb-sched-detail-sec" },
            React.createElement("div", { className: "wb-sched-detail-sec-title" }, T("schedule.note")),
            React.createElement("p", { className: "wb-sched-detail-prompt" }, ev.content)
          ),
          isTask && React.createElement(
            "div", { className: "wb-sched-detail-actions" },
            React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { props.onEdit(ev); } }, T("schedule.edit")),
            React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { props.onToggleStatus(ev); } },
              ev.status === "paused" ? T("schedule.enable") : T("schedule.pause")),
            React.createElement("button", { type: "button", className: "wb-btn danger", onClick: function () { props.onDelete(ev); } }, T("schedule.delete"))
          ),
          !isTask && React.createElement(
            "div", { className: "wb-sched-detail-actions" },
            React.createElement("button", { type: "button", className: "wb-btn", onClick: function () { props.onEdit(ev); } }, T("schedule.edit")),
            React.createElement("button", { type: "button", className: "wb-btn danger", onClick: function () { props.onDelete(ev); } }, T("schedule.delete"))
          )
        );
    var tabBodies = {
      detail: detailContent,
      runs: React.createElement(RunsTab, { runs: props.runs, loading: props.runsLoading }),
    };

    return React.createElement(
      "aside", { className: "wb-floating-detail-shell wb-sched-detail", "aria-label": T("schedule.detailPanel") },
      React.createElement(
        "div", { className: "wb-floating-detail-card wb-sched-detail-card" },
        React.createElement(
          "nav", { className: "wb-detail-accordion wb-sched-detail-tabs", "aria-label": T("schedule.detailPanel") },
          React.createElement(
            "div", { className: "wb-detail-accordion-head wb-sched-detail-tabs-head" },
            React.createElement("span", null, T("schedule.detailPanel")),
            React.createElement("button", { type: "button", className: "wb-sched-detail-close", onClick: props.onClose, title: T("common.close"), "aria-label": T("common.close") }, "×")
          ),
          React.createElement(
            "div", { className: "wb-detail-accordion-list" },
            tabs.map(function (it) {
              return React.createElement(
                React.Fragment, { key: it.id },
                React.createElement(
                  "button", {
                    type: "button",
                    className: "wb-detail-accordion-trigger" + (tab === it.id ? " active" : ""),
                    "aria-expanded": tab === it.id,
                    onClick: function () { setTab(tab === it.id ? "" : it.id); },
                  },
                  React.createElement("span", { className: "wb-detail-accordion-icon" }, it.id === "runs" ? svg(["M12 7v5l3 2", React.createElement("circle", { key: "c", cx: 12, cy: 12, r: 9 })]) : svg([React.createElement("rect", { key: "r", x: 5, y: 4, width: 14, height: 16, rx: 2 }), "M9 8h6M9 12h6M9 16h4"])),
                  React.createElement("span", null, it.label),
                  svg(["m9 18 6-6-6-6"], { width: 14, height: 14 })
                ),
                React.createElement(
                  "div", { className: "wb-detail-accordion-panel" + (tab === it.id ? " open" : ""), "aria-hidden": tab !== it.id },
                  React.createElement("div", { className: "wb-detail-accordion-panel-inner" },
                    React.createElement("div", { className: "wb-sched-detail-body" }, tabBodies[it.id]))
                )
              );
            })
          )
        )
      )
    );
  }

  function MetaRow(props) {
    return React.createElement("div", { className: "wb-sched-meta-row" },
      React.createElement("span", { className: "wb-sched-meta-ico" }, props.icon),
      React.createElement("span", { className: "wb-sched-meta-val" }, props.value));
  }
  function KV(props) {
    return React.createElement("div", { className: "wb-sched-kv" },
      React.createElement("small", null, props.k),
      React.createElement("span", null, props.v));
  }

  function RunsTab(props) {
    if (props.loading) return React.createElement("div", { className: "wb-sched-muted pad" }, T("schedule.runsLoading"));
    if (!props.runs || !props.runs.length) return React.createElement("div", { className: "wb-sched-muted pad" }, T("schedule.runsEmpty"));
    return React.createElement(
      "div", { className: "wb-sched-runs" },
      props.runs.map(function (run) {
        var ok = run.status === "success";
        return React.createElement(
          "div", { className: "wb-sched-run", key: run.id },
          React.createElement(
            "div", { className: "wb-sched-run-head" },
            React.createElement("span", { className: "wb-sched-badge " + (ok ? "green" : "red") }, ok ? T("schedule.success") : T("schedule.failure")),
            React.createElement("time", null, absTimeText(run.run_at)),
            React.createElement("small", null, (run.duration_ms != null ? Math.round(run.duration_ms / 100) / 10 + "s" : ""))
          ),
          (run.result || run.error) && React.createElement("p", { className: "wb-sched-run-body" }, String(run.error || run.result).slice(0, 400))
        );
      })
    );
  }

  function detailTimeText(ev) {
    if (ev.all_day) return (currentLang() === "zh"
      ? ev._start.getFullYear() + "年" + (ev._start.getMonth() + 1) + "月" + ev._start.getDate() + "日"
      : ev._start.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })) + " · " + T("schedule.allDay");
    if (currentLang() !== "zh") {
      return ev._start.toLocaleDateString("en-US", { weekday: "short", year: "numeric", month: "short", day: "numeric" }) +
        " " + clockHM(ev._start) + " - " + clockHM(ev._end);
    }
    return ev._start.getFullYear() + "年" + (ev._start.getMonth() + 1) + "月" + ev._start.getDate() + "日 周" + WEEKDAY_CN[ev._start.getDay()] +
      " " + clockHM(ev._start) + " – " + clockHM(ev._end);
  }
  function absTimeText(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + clockHM(d);
  }

  // ── new / edit schedule form (modal) ─────────────────────────────────
  function ScheduleForm(props) {
    var scheduleTimezone = (props.task && props.task.schedule_timezone) || localScheduleTimezone();
    var initial = useMemo(function () {
      if (props.task) return parseSchedule(props.task);
      var s = new Date(props.defaultDate || new Date());
      s.setMinutes(0, 0, 0); s.setHours(s.getHours() + 1);
      return { repeat: "none", start: s, cronText: "", intervalValue: 1, intervalUnit: "h" };
    }, [props.task, props.defaultDate]);

    var formKindState = useState(props.task ? "task" : props.entity ? "entity" : "entity"); var formKind = formKindState[0], setFormKind = formKindState[1];
    var promptState = useState(props.task ? props.task.prompt : props.entity ? (props.entity.title || "") : ""); var prompt = promptState[0], setPrompt = promptState[1];
    var noteState = useState(props.entity ? (props.entity.content || "") : ""); var note = noteState[0], setNote = noteState[1];
    var entityDateState = useState(toDateInputValue(props.entity && props.entity.due_date)); var entityDate = entityDateState[0], setEntityDate = entityDateState[1];
    var entityStatusState = useState(props.entity ? (props.entity.status || "active") : "active"); var entityStatus = entityStatusState[0], setEntityStatus = entityStatusState[1];
    var entityPriorityState = useState(props.entity ? (props.entity.priority || "medium") : "medium"); var entityPriority = entityPriorityState[0], setEntityPriority = entityPriorityState[1];
    var startState = useState(toTimezoneInputValue(initial.start, scheduleTimezone)); var startVal = startState[0], setStartVal = startState[1];
    var repeatState = useState(initial.repeat); var repeat = repeatState[0], setRepeat = repeatState[1];
    var cronState = useState(initial.cronText); var cronText = cronState[0], setCronText = cronState[1];
    var ivState = useState(initial.intervalValue); var ivVal = ivState[0], setIvVal = ivState[1];
    var iuState = useState(initial.intervalUnit); var ivUnit = iuState[0], setIvUnit = iuState[1];
    var savingState = useState(false); var saving = savingState[0], setSaving = savingState[1];
    var errState = useState(""); var err = errState[0], setErr = errState[1];

    var REPEATS = [
      { id: "none", labelKey: "schedule.repeat.none" }, { id: "daily", labelKey: "schedule.repeat.daily" }, { id: "weekly", labelKey: "schedule.repeat.weekly" },
      { id: "monthly", labelKey: "schedule.repeat.monthly" }, { id: "interval", labelKey: "schedule.repeat.interval" }, { id: "cron", labelKey: "schedule.repeat.cron" },
    ];

    function submit() {
      var p = prompt.trim();
      if (!p) { setErr(formKind === "task" ? T("schedule.error.taskContentRequired") : T("schedule.error.eventTitleRequired")); return; }
      if (formKind === "entity") {
        if (!entityDate) { setErr(T("schedule.error.dateRequired")); return; }
        var dueDate = new Date(entityDate + "T23:59:59");
        if (isNaN(dueDate.getTime())) { setErr(T("schedule.error.invalidDate")); return; }
        var entityBody = {
          title: p,
          content: note.trim(),
          due_date: dueDate.toISOString(),
          status: entityStatus,
          priority: entityPriority,
        };
        setSaving(true); setErr("");
        var entityOp = props.entity
          ? props.api.updateEntity(props.entity.id, entityBody)
          : props.api.createEntity(Object.assign({ type: "event", source: "user" }, entityBody));
        entityOp.then(function () { props.onSaved(); }).catch(function (e) { setErr(e.message || String(e)); }).finally(function () { setSaving(false); });
        return;
      }
      var startDate = dateFromTimezoneInput(startVal, scheduleTimezone);
      if (repeat !== "cron" && repeat !== "interval" && isNaN(startDate.getTime())) { setErr(T("schedule.error.invalidFirstTime")); return; }
      var spec = buildSchedule(repeat, startDate, cronText, ivVal, ivUnit, scheduleTimezone, startVal);
      if ((spec.schedule_type === "cron" || spec.schedule_type === "interval") && !spec.schedule_value) { setErr(T("schedule.error.repeatRequired")); return; }
      var body = { prompt: p, schedule_type: spec.schedule_type, schedule_value: spec.schedule_value, schedule_timezone: spec.schedule_timezone };
      if (spec.schedule_type === "once") body.next_run = startDate.toISOString();
      setSaving(true); setErr("");
      var op = props.task ? props.api.update(props.task.id, body) : props.api.create(body);
      op.then(function () { props.onSaved(); }).catch(function (e) { setErr(e.message || String(e)); }).finally(function () { setSaving(false); });
    }

    var showStart = formKind === "task" && repeat !== "cron" && repeat !== "interval";
    return React.createElement(
      "div", { className: "wb-create-scrim", onClick: function (e) { if (e.target === e.currentTarget) props.onClose(); } },
      React.createElement(
        "div", { className: "wb-create-modal wb-create-task wb-sched-create-modal" },
        React.createElement(
          "div", { className: "wb-create-head" },
          React.createElement("b", null, props.task ? T("schedule.editTask") : props.entity ? T("schedule.editEvent") : T("schedule.addContent")),
          React.createElement("button", { type: "button", className: "wb-create-x", onClick: props.onClose, title: T("common.close") }, "×")
        ),
        React.createElement(
          "div", { className: "wb-create-body wb-sched-create-body" },
          !props.task && !props.entity && React.createElement(
            "div", { className: "wb-sched-create-kind" },
            React.createElement("span", { className: "wb-sched-create-label" }, T("schedule.addType")),
            React.createElement(
              "div", { className: "wb-sched-kind-switch" },
              React.createElement("button", {
                type: "button", className: "wb-sched-kind-btn" + (formKind === "entity" ? " on" : ""),
                onClick: function () { setFormKind("entity"); setErr(""); },
              }, T("schedule.event")),
              React.createElement("button", {
                type: "button", className: "wb-sched-kind-btn" + (formKind === "task" ? " on" : ""),
                onClick: function () { setFormKind("task"); setErr(""); },
              }, T("schedule.scheduledTask"))
            )
          ),
          React.createElement(
            "label", { className: "wb-sched-field" },
            React.createElement("span", null, formKind === "task" ? T("schedule.taskContent") : T("schedule.eventTitle")),
            React.createElement("textarea", {
              value: prompt, rows: 3, autoFocus: true,
              placeholder: formKind === "task" ? T("schedule.taskPlaceholder") : T("schedule.eventPlaceholder"),
              onChange: function (e) { setPrompt(e.target.value); },
            })
          ),
          formKind === "entity" && React.createElement(
            React.Fragment, null,
            React.createElement(
              "div", { className: "wb-sched-create-grid" },
              React.createElement(
                "label", { className: "wb-sched-field" },
                React.createElement("span", null, T("schedule.date")),
                React.createElement("input", { type: "date", value: entityDate, onChange: function (e) { setEntityDate(e.target.value); } })
              ),
              React.createElement(
                "label", { className: "wb-sched-field" },
                React.createElement("span", null, T("create.task.priority")),
                React.createElement("select", { value: entityPriority, onChange: function (e) { setEntityPriority(e.target.value); } },
                  React.createElement("option", { value: "high" }, T("priority.high")),
                  React.createElement("option", { value: "medium" }, T("priority.medium")),
                  React.createElement("option", { value: "low" }, T("priority.low")))
              )
            ),
            React.createElement(
              "label", { className: "wb-sched-field" },
              React.createElement("span", null, T("workbenchChat.statusLabel")),
              React.createElement("div", { className: "wb-sched-seg" },
                [{ id: "pending", labelKey: "status.pending" }, { id: "active", labelKey: "status.running" }, { id: "done", labelKey: "status.done" }].map(function (it) {
                  return React.createElement("button", {
                    key: it.id, type: "button", className: entityStatus === it.id ? "on" : "",
                    onClick: function () { setEntityStatus(it.id); },
                  }, T(it.labelKey));
                }))
            ),
            React.createElement(
              "label", { className: "wb-sched-field" },
              React.createElement("span", null, T("schedule.note")),
              React.createElement("textarea", {
                value: note, rows: 4,
                placeholder: T("schedule.notePlaceholder"),
                onChange: function (e) { setNote(e.target.value); },
              })
            )
          ),
          showStart && React.createElement(
            "label", { className: "wb-sched-field" },
            React.createElement("span", null, repeat === "none" ? T("schedule.time") : T("schedule.firstTime")),
            React.createElement("input", { type: "datetime-local", value: startVal, onChange: function (e) { setStartVal(e.target.value); } })
          ),
          formKind === "task" && React.createElement(
            "label", { className: "wb-sched-field" },
            React.createElement("span", null, T("schedule.repeat")),
            React.createElement("div", { className: "wb-sched-seg" },
              REPEATS.map(function (r) {
                return React.createElement("button", {
                  key: r.id, type: "button", className: repeat === r.id ? "on" : "",
                  onClick: function () { setRepeat(r.id); },
                }, T(r.labelKey));
              }))
          ),
          formKind === "task" && repeat === "interval" && React.createElement(
            "label", { className: "wb-sched-field" },
            React.createElement("span", null, T("schedule.interval")),
            React.createElement("div", { className: "wb-sched-inline" },
              React.createElement("input", { type: "number", min: 1, value: ivVal, onChange: function (e) { setIvVal(e.target.value); }, style: { width: "90px" } }),
              React.createElement("select", { value: ivUnit, onChange: function (e) { setIvUnit(e.target.value); } },
                React.createElement("option", { value: "m" }, T("schedule.unit.minutes")),
                React.createElement("option", { value: "h" }, T("schedule.unit.hours")),
                React.createElement("option", { value: "d" }, T("schedule.unit.days"))))
          ),
          formKind === "task" && repeat === "cron" && React.createElement(
            "label", { className: "wb-sched-field" },
            React.createElement("span", null, T("schedule.cronExpression")),
            React.createElement("input", { type: "text", value: cronText, placeholder: T("schedule.cronPlaceholder"), onChange: function (e) { setCronText(e.target.value); } })
          ),
          React.createElement("p", { className: "wb-sched-form-note" }, formKind === "task"
            ? T("schedule.taskFormNote")
            : T("schedule.eventFormNote")),
          err && React.createElement("div", { className: "wb-sched-form-err" }, err)
        ),
        React.createElement(
          "div", { className: "wb-create-foot" },
          React.createElement("button", { type: "button", className: "wb-btn", onClick: props.onClose }, T("common.cancel")),
          React.createElement("button", { type: "button", className: "wb-btn primary", onClick: submit, disabled: saving || !prompt.trim() },
            saving ? T("settings.saving") : (props.task || props.entity ? T("common.save") : T("create.task.create")))
        )
      )
    );
  }

  // ── main page ────────────────────────────────────────────────────────
  function WorkbenchSchedulePage(props) {
    window.CyreneUI.require("i18n").use();
    var project = props && props.project;
    var active = !props || props.active !== false;
    // Send the canonical id. The backend resolves it to the schedule dataKey
    // through its lightweight project lookup, avoiding a full project-store
    // repair scan on every calendar request.
    var workspace = (project && (project.id || project.dataKey)) || "default";
    var API = useMemo(function () { return scheduleApi(workspace); }, [workspace]);
    var today = startOfDay(new Date());
    var viewState = useState("day"); var viewMode = viewState[0], setViewMode = viewState[1];
    var anchorState = useState(today); var anchorDate = anchorState[0], setAnchorDate = anchorState[1];
    var miniState = useState(new Date()); var viewMonth = miniState[0], setViewMonth = miniState[1];
    var rawState = useState([]); var rawEvents = rawState[0], setRawEvents = rawState[1];
    var loadState = useState(true); var loading = loadState[0], setLoading = loadState[1];
    var errState = useState(""); var error = errState[0], setError = errState[1];
    var visState = useState({ task_recurring: true, task_once: true, entity_due: true });
    var visible = visState[0], setVisible = visState[1];
    var selState = useState(null); var selectedId = selState[0], setSelectedId = selState[1];
    var detailTabState = useState("detail"); var detailTab = detailTabState[0], setDetailTab = detailTabState[1];
    var runsState = useState([]); var runs = runsState[0], setRuns = runsState[1];
    var runsLoadState = useState(false); var runsLoading = runsLoadState[0], setRunsLoading = runsLoadState[1];
    var entityDetailState = useState(null); var entityDetail = entityDetailState[0], setEntityDetail = entityDetailState[1];
    var formState = useState(null); var formMode = formState[0], setFormMode = formState[1]; // null | {task?, defaultDate?}
    var contextState = useState(null); var contextMenu = contextState[0], setContextMenu = contextState[1];
    var pendingTaskIdRef = useRef("");
    var pendingEntityIdRef = useRef("");
    var pendingDateAppliedRef = useRef(false);
    var detailRequestSeqRef = useRef(0);

    useEffect(function () {
      if (!contextMenu) return undefined;
      function close() { setContextMenu(null); }
      function onKeyDown(event) { if (event.key === "Escape") close(); }
      window.addEventListener("resize", close);
      window.addEventListener("scroll", close, true);
      document.addEventListener("keydown", onKeyDown);
      return function () {
        window.removeEventListener("resize", close);
        window.removeEventListener("scroll", close, true);
        document.removeEventListener("keydown", onKeyDown);
      };
    }, [!!contextMenu]);

    // Visible window for the occurrences query (a touch wider than the view).
    var windowRange = useMemo(function () {
      if (viewMode === "day") return { start: startOfDay(anchorDate), end: addDays(startOfDay(anchorDate), 1) };
      if (viewMode === "week") { var ws = startOfWeekMon(anchorDate); return { start: ws, end: addDays(ws, 7) }; }
      var first = new Date(anchorDate.getFullYear(), anchorDate.getMonth(), 1);
      var pad = first.getDay() === 0 ? 6 : first.getDay() - 1;
      var gs = addDays(startOfDay(first), -pad);
      return { start: gs, end: addDays(gs, 42) };
    }, [viewMode, anchorDate]);
    var requestKey = workspace + "|" + windowRange.start.getTime() + "|" + windowRange.end.getTime();
    var requestKeyRef = useRef(requestKey); requestKeyRef.current = requestKey;

    function load(options) {
      var opts = options || {};
      var requestedKey = requestKey;
      var cached = schedulePageCache.ranges[requestedKey];
      if (!opts.background && requestKeyRef.current === requestedKey) setLoading(true);
      if (requestKeyRef.current === requestedKey) setError("");
      return fetchScheduleRange(
        requestedKey,
        API,
        windowRange.start.toISOString(),
        windowRange.end.toISOString()
      )
        .then(function (evs) {
          if (requestKeyRef.current === requestedKey) setRawEvents(evs);
          return evs;
        })
        .catch(function (e) {
          if (requestKeyRef.current === requestedKey) {
            setError(e.message || String(e));
            if (!cached) setRawEvents([]);
          }
          return null;
        })
        .finally(function () { if (requestKeyRef.current === requestedKey) setLoading(false); });
    }
    // Apply a schedule search selection once the event list is loaded. We use
    // refs to avoid calling setState inside the rawEvents updater.
    function applyPendingScheduleSelection() {
      var targetTaskId = pendingTaskIdRef.current;
      var targetEntityId = pendingEntityIdRef.current;
      if (!targetTaskId && !targetEntityId) return;
      var list = Array.isArray(rawEvents) ? rawEvents : [];
      var match = null;
      if (targetTaskId) {
        match = list.find(function (e) { return e.task_id === targetTaskId; });
      } else if (targetEntityId) {
        match = list.find(function (e) { return e.entity_id === targetEntityId; });
      }
      if (match) {
        selectEvent(decorate([match])[0]);
        pendingTaskIdRef.current = "";
        pendingEntityIdRef.current = "";
        pendingDateAppliedRef.current = false;
        window.CyreneUI.require("navigation").clearPending();
      }
    }

    function dateFromPending(pending) {
      var time = pending && (pending.nextRun || pending.dueDate);
      if (!time) return null;
      try {
        var d = new Date(time);
        if (!isNaN(d.getTime())) return d;
      } catch (e) {}
      return null;
    }

    function applyPendingDate(pending) {
      if (pendingDateAppliedRef.current) return;
      var d = dateFromPending(pending);
      if (d) {
        pendingDateAppliedRef.current = true;
        setAnchorDate(d);
        setViewMonth(new Date(d.getFullYear(), d.getMonth(), 1));
      }
    }

    useEffect(function () {
      var cached = schedulePageCache.ranges[requestKey];
      if (cached) {
        setRawEvents(cached.value);
        setLoading(false);
      } else {
        setRawEvents([]);
        setLoading(true);
      }

      pendingDateAppliedRef.current = false;
      var pending = window.CyreneUI.require("navigation").getPending();
      var pendingType = pending && pending.type;
      if (pendingType === "schedule") {
        applyPendingDate(pending);
        pendingTaskIdRef.current = pending.taskId || pending.id || "";
        pendingEntityIdRef.current = pending.entityId || "";
      }

      if (!active) return;
      load({ background: !!cached });
      /* eslint-disable-next-line */
    }, [requestKey, active]);

    // The range cache only avoids a blank calendar. Always revalidate on
    // activation, focus, and Agent runtime changes instead of accepting a TTL.
    useEffect(function () {
      if (!active) return undefined;
      var refreshTimer = null;
      function refreshSoon() {
        if (document.hidden) return;
        if (refreshTimer) clearTimeout(refreshTimer);
        refreshTimer = setTimeout(function () {
          refreshTimer = null;
          load({ background: true });
        }, 250);
      }
      function onVisibility() { if (!document.hidden) refreshSoon(); }
      function onRuntimeEvent(event) {
        if (!event) return;
        if (["tool_call", "assistant_message", "chat_message", "session_update", "goal_loop_update"].indexOf(event.type) >= 0) refreshSoon();
      }
      window.addEventListener("focus", refreshSoon);
      document.addEventListener("visibilitychange", onVisibility);
      var unsubscribe = window.CyreneUI.require("events").subscribe(onRuntimeEvent);
      return function () {
        if (refreshTimer) clearTimeout(refreshTimer);
        window.removeEventListener("focus", refreshSoon);
        document.removeEventListener("visibilitychange", onVisibility);
        unsubscribe();
      };
    }, [requestKey, active]);

    useEffect(function () {
      applyPendingScheduleSelection();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [rawEvents]);

    // Listen for search-navigation events while already mounted.
    useEffect(function () {
      function onNavigate(event) {
        var detail = event && event.detail;
        if (!detail || detail.type !== "schedule") return;
        applyPendingDate(detail);
        pendingTaskIdRef.current = detail.taskId || detail.id || "";
        pendingEntityIdRef.current = detail.entityId || "";
        applyPendingScheduleSelection();
      }
      window.addEventListener("cyrene:workbench-navigate", onNavigate);
      return function () { window.removeEventListener("cyrene:workbench-navigate", onNavigate); };
    }, [rawEvents]);

    var events = useMemo(function () {
      return decorate(rawEvents).filter(function (ev) { return visible[ev.category]; });
    }, [rawEvents, visible]);

    // For the mini-month marker dots — which days hold any (visible) event.
    var markedDays = useMemo(function () {
      var m = {};
      events.forEach(function (ev) { var d = ev._start; m[d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate()] = true; });
      return m;
    }, [events]);

    var counts = useMemo(function () {
      var c = { task_recurring: 0, task_once: 0, entity_due: 0 };
      decorate(rawEvents).forEach(function (ev) { if (c[ev.category] != null) c[ev.category]++; });
      return c;
    }, [rawEvents]);

    var selectedEvent = useMemo(function () {
      if (!selectedId) return null;
      var base = events.find(function (ev) { return ev.id === selectedId; }) ||
        decorate(rawEvents).find(function (ev) { return ev.id === selectedId; }) || null;
      if (!base) return null;
      if (base.source === "entity" && entityDetail && entityDetail.id === base.entity_id) return Object.assign({}, base, entityDetail);
      return base;
    }, [selectedId, events, rawEvents, entityDetail]);

    function selectEvent(ev) {
      var requestSeq = ++detailRequestSeqRef.current;
      setSelectedId(ev.id);
      setDetailTab("detail");
      if (ev.source === "task" && ev.task_id) {
        setEntityDetail(null);
        setRuns([]); setRunsLoading(true);
        API.runs(ev.task_id).then(function (r) {
          if (requestSeq === detailRequestSeqRef.current) setRuns(r);
        }).catch(function () {
          if (requestSeq === detailRequestSeqRef.current) setRuns([]);
        }).finally(function () {
          if (requestSeq === detailRequestSeqRef.current) setRunsLoading(false);
        });
      } else if (ev.source === "entity" && ev.entity_id) {
        setEntityDetail(null);
        setRuns([]); setRunsLoading(false);
        API.getEntity(ev.entity_id).then(function (ent) {
          if (requestSeq === detailRequestSeqRef.current) setEntityDetail(ent);
        }).catch(function () {
          if (requestSeq === detailRequestSeqRef.current) setEntityDetail(null);
        });
      }
    }

    function closeDetail() {
      detailRequestSeqRef.current += 1;
      setSelectedId(null);
      setRuns([]);
      setRunsLoading(false);
      setEntityDetail(null);
    }

    function goToday() { setAnchorDate(today); setViewMonth(new Date()); }
    function goPrev() { shift(-1); }
    function goNext() { shift(1); }
    function shift(dir) {
      if (viewMode === "day") setAnchorDate(addDays(anchorDate, dir));
      else if (viewMode === "week") setAnchorDate(addDays(anchorDate, dir * 7));
      else { var n = addMonths(anchorDate, dir); setAnchorDate(n); setViewMonth(n); }
    }
    function pickDay(d) {
      setAnchorDate(d); setViewMonth(new Date(d.getFullYear(), d.getMonth(), 1));
      if (viewMode === "month") setViewMode("day");
    }

    function toggleStatus(ev) {
      var next = ev.status === "paused" ? "active" : "paused";
      API.update(ev.task_id, { status: next }).then(function () { load(); }).catch(function (e) { setError(e.message || String(e)); });
    }
    function removeTask(ev) {
      window.CyreneUI.require("feedback").confirmModal({ body: T("schedule.confirmDeleteTask"), confirmLabel: T("common.delete"), danger: true }).then(function (ok) {
        if (!ok) return;
        API.remove(ev.task_id).then(function () { setSelectedId(null); load(); }).catch(function (e) { setError(e.message || String(e)); });
      });
    }
    function removeEntity(ev) {
      window.CyreneUI.require("feedback").confirmModal({ body: T("schedule.confirmDeleteEvent"), confirmLabel: T("common.delete"), danger: true }).then(function (ok) {
        if (!ok) return;
        API.removeEntity(ev.entity_id).then(function () { setSelectedId(null); setEntityDetail(null); load(); }).catch(function (e) { setError(e.message || String(e)); });
      });
    }
    function openEdit(ev) {
      if (ev.source === "task") {
        setFormMode({ task: rawTaskOf(rawEvents, ev) });
        return;
      }
      if (entityDetail && entityDetail.id === ev.entity_id) {
        setFormMode({ entity: entityDetail });
        return;
      }
      API.getEntity(ev.entity_id).then(function (ent) { setFormMode({ entity: ent }); }).catch(function (e) { setError(e.message || String(e)); });
    }
    function openScheduleContextMenu(ev, event) {
      event.preventDefault();
      event.stopPropagation();
      var menuHeight = ev.source === "task" ? 140 : 102;
      setContextMenu({
        event: ev,
        left: Math.max(8, Math.min(event.clientX, window.innerWidth - 220 - 8)),
        top: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
      });
    }

    var headingText = viewMode === "day" ? dayHeading(anchorDate) : viewMode === "week" ? weekHeading(anchorDate) : monthHeading(anchorDate);

    return React.createElement(
      "section", { className: "wb-sched-page" },
      React.createElement(CalendarRail, {
        onBack: props.onBack,
        viewMonth: viewMonth, onViewMonth: setViewMonth,
        anchorDate: anchorDate, onPickDay: pickDay, markedDays: markedDays,
        visible: visible, counts: counts,
        onToggle: function (id) { setVisible(function (p) { var n = Object.assign({}, p); n[id] = !n[id]; return n; }); },
        sidebarCollapsed: props.sidebarCollapsed,
        collapseControl: props.collapseControl,
        moduleDock: props.moduleDock,
      }),
      React.createElement(
        "div", { className: "wb-sched-main" },
        // toolbar
        React.createElement(
          "div", { className: "wb-sched-toolbar" },
          React.createElement(
            "div", { className: "wb-sched-toolbar-left" },
            React.createElement("button", { type: "button", className: "wb-sched-today", onClick: goToday }, T("schedule.today")),
            React.createElement("button", { type: "button", className: "wb-sched-iconbtn", onClick: goPrev, title: T("schedule.previous") }, svg(["m15 18-6-6 6-6"])),
            React.createElement("button", { type: "button", className: "wb-sched-iconbtn", onClick: goNext, title: T("schedule.next") }, svg(["m9 18 6-6-6-6"])),
            React.createElement("h2", { className: "wb-sched-heading" }, headingText)
          ),
          React.createElement(
            "div", { className: "wb-sched-toolbar-right" },
            React.createElement(
              "div", { className: "wb-sched-viewseg" },
              [{ id: "day", labelKey: "schedule.view.day" }, { id: "week", labelKey: "schedule.view.week" }, { id: "month", labelKey: "schedule.view.month" }].map(function (v) {
                return React.createElement("button", {
                  key: v.id, type: "button", className: viewMode === v.id ? "on" : "",
                  onClick: function () { setViewMode(v.id); },
                }, T(v.labelKey));
              })
            ),
            React.createElement(
              "button", { type: "button", className: "wb-btn primary wb-sched-new", onClick: function () { setFormMode({ defaultDate: anchorDate }); } },
              svg(["M12 5v14M5 12h14"], { width: 15, height: 15, strokeWidth: 2.4 }),
              React.createElement("span", null, T("schedule.add"))
            )
          )
        ),
        error && React.createElement("div", { className: "wb-sched-error" }, error),
        // body
        React.createElement(
          "div", { className: "wb-sched-viewport" },
          loading
            ? React.createElement("div", { className: "wb-sched-loading" }, T("workbench.scheduleLoading"))
            : viewMode === "day" ? React.createElement(DayView, { anchorDate: anchorDate, events: events, activeId: selectedId, onSelect: selectEvent, onContextMenu: openScheduleContextMenu })
              : viewMode === "week" ? React.createElement(WeekView, { anchorDate: anchorDate, events: events, activeId: selectedId, onSelect: selectEvent, onContextMenu: openScheduleContextMenu, onPickDay: pickDay })
                : React.createElement(MonthView, { anchorDate: anchorDate, events: events, activeId: selectedId, onSelect: selectEvent, onContextMenu: openScheduleContextMenu, onPickDay: pickDay })
        )
      ),
      selectedEvent
        ? React.createElement(DetailPanel, {
          event: selectedEvent, tab: detailTab, setTab: setDetailTab,
          runs: runs, runsLoading: runsLoading,
          onClose: closeDetail,
          onEdit: openEdit,
          onToggleStatus: toggleStatus,
          onDelete: function (ev) { return ev.source === "task" ? removeTask(ev) : removeEntity(ev); },
        })
        : React.createElement(
          "aside", { className: "wb-floating-detail-shell wb-sched-detail empty" },
          React.createElement("div", { className: "wb-floating-detail-card wb-sched-detail-card empty" },
            React.createElement("div", { className: "wb-sched-detail-placeholder" },
              svg(["M3 4.5h18M8 2.5v4M16 2.5v4", React.createElement("rect", { key: "r", x: 3, y: 4.5, width: 18, height: 17, rx: 2.5 })], { width: 34, height: 34, strokeWidth: 1.3 }),
              React.createElement("p", null, T("schedule.selectEvent"))
            )
          )
        ),
      contextMenu && React.createElement(
        "div", { className: "wb-item-context-layer" },
        React.createElement("div", { className: "wb-item-context-scrim", onPointerDown: function () { setContextMenu(null); } }),
        React.createElement(
          "div", {
            className: "wb-item-context-menu",
            role: "menu",
            "aria-label": contextMenu.event.title,
            style: { left: contextMenu.left + "px", top: contextMenu.top + "px" },
            onContextMenu: function (event) { event.preventDefault(); },
          },
          React.createElement("button", { type: "button", role: "menuitem", onClick: function () { var ev = contextMenu.event; setContextMenu(null); openEdit(ev); } },
            svg(["m4 16 1-4 8.5-8.5a2.1 2.1 0 0 1 3 3L8 15l-4 1Z"], { width: 15, height: 15 }), T("schedule.edit")),
          contextMenu.event.source === "task" && React.createElement("button", { type: "button", role: "menuitem", onClick: function () { var ev = contextMenu.event; setContextMenu(null); toggleStatus(ev); } },
            svg(["M20 12a8 8 0 1 1-2.3-5.7M20 4v6h-6"], { width: 15, height: 15 }), contextMenu.event.status === "paused" ? T("schedule.enable") : T("schedule.pause")),
          React.createElement("div", { className: "wb-item-context-separator" }),
          React.createElement("button", { type: "button", role: "menuitem", className: "danger", onClick: function () { var ev = contextMenu.event; setContextMenu(null); return ev.source === "task" ? removeTask(ev) : removeEntity(ev); } },
            svg(["M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"], { width: 15, height: 15 }), T("schedule.delete"))
        )
      ),
      formMode && React.createElement(ScheduleForm, {
        api: API,
        task: formMode.task, defaultDate: formMode.defaultDate,
        entity: formMode.entity,
        onClose: function () { setFormMode(null); },
        onSaved: function () { setFormMode(null); setSelectedId(null); setEntityDetail(null); load(); },
      })
    );
  }

  // Recover the raw task fields (schedule_type/value) for the edit form from
  // any occurrence event of that task.
  function rawTaskOf(rawEvents, ev) {
    var match = rawEvents.find(function (e) { return e.task_id === ev.task_id; }) || ev;
    return {
      id: ev.task_id,
      prompt: match.title || ev.title,
      schedule_type: match.schedule_type || ev.schedule_type,
      schedule_value: match.schedule_value || ev.schedule_value,
      next_run: match.next_run || ev.next_run,
    };
  }

  window.CyreneUI.schedule = window.CyreneUI.register("schedule", {
    Page: WorkbenchSchedulePage,
  });
})();
