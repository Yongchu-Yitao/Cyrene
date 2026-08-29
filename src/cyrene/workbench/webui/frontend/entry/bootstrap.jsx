// Workbench-only browser bootstrap. Quick Chat remains an independent surface
// backed by the same platform data, event, and API services.
var { useState: useStateBootstrap, useEffect: useEffectBootstrap } = React;

function bootstrapT(key, fallback) {
  try { return window.CyreneUI.require("i18n").t(key, null, fallback); }
  catch (error) { return fallback; }
}

function readWorkbenchTweak(key, fallback) {
  try {
    var value = localStorage.getItem("cyrene-tweak-" + key);
    return value !== null ? JSON.parse(value) : fallback;
  } catch (error) {
    return fallback;
  }
}

var workbenchThemeSaveQueue = Promise.resolve();
var workbenchThemePendingMode = "";

function writeWorkbenchThemeLocal(mode) {
  try {
    localStorage.setItem("cyrene-tweak-theme", JSON.stringify(mode));
    localStorage.setItem("cyrene-theme-mode", mode);
  } catch (error) {}
}

function saveWorkbenchThemeToAppearance(mode, retry) {
  return fetch("/api/settings/namespaces/appearance").then(function (response) {
    return response.ok ? response.json() : Promise.reject(new Error("appearance unavailable"));
  }).then(function (payload) {
    var values = payload.values || {};
    var changes = { theme: mode };
    // If the settings overlay has never performed its one-time migration,
    // preserve every existing local appearance choice when the top bar becomes
    // the first writer to the durable namespace.
    if (!values.appearance_migrated) {
      changes.accent = readWorkbenchTweak("accent", "") || "";
      changes.background_light = readWorkbenchTweak("backgroundLight", "") || "";
      changes.background_dark = readWorkbenchTweak("backgroundDark", "") || "";
      changes.text_size = readWorkbenchTweak("textSize", "default") || "default";
      changes.animate_pulse = readWorkbenchTweak("animatePulse", true) !== false;
      changes.appearance_migrated = true;
    }
    return fetch("/api/settings/namespaces/appearance", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        changes: changes,
        expected_revision: payload.revision,
      }),
    });
  }).then(function (response) {
    if (response.ok) return response;
    if (response.status === 409 && !retry) return saveWorkbenchThemeToAppearance(mode, true);
    throw new Error("appearance theme save failed");
  });
}

function persistWorkbenchTheme(mode) {
  workbenchThemePendingMode = mode;
  workbenchThemeSaveQueue = workbenchThemeSaveQueue.catch(function () {}).then(function () {
    return saveWorkbenchThemeToAppearance(mode, false);
  }).catch(function () {}).then(function () {
    if (workbenchThemePendingMode === mode) workbenchThemePendingMode = "";
  });
}

function readWorkbenchSurface() {
  try {
    return new URLSearchParams(window.location.search || "").get("surface") || "";
  } catch (error) {
    return "";
  }
}

function applyWorkbenchAccent(accent) {
  var rootStyle = document.documentElement.style;
  var value = typeof accent === "string" ? accent.trim() : "";
  var match = value.match(/^#([0-9a-f]{6})$/i);
  if (!match) {
    rootStyle.removeProperty("--accent");
    rootStyle.removeProperty("--accent-faint");
    rootStyle.removeProperty("--accent-dim");
    rootStyle.removeProperty("--accent-text");
    return;
  }
  var red = parseInt(match[1].slice(0, 2), 16);
  var green = parseInt(match[1].slice(2, 4), 16);
  var blue = parseInt(match[1].slice(4, 6), 16);
  rootStyle.setProperty("--accent", value);
  rootStyle.setProperty("--accent-faint", "rgba(" + red + "," + green + "," + blue + ",0.08)");
  rootStyle.setProperty("--accent-dim", "rgba(" + red + "," + green + "," + blue + ",0.35)");
  rootStyle.setProperty(
    "--accent-text",
    (0.299 * red + 0.587 * green + 0.114 * blue) / 255 > 0.55
      ? "#0d1612"
      : "#ffffff",
  );
}

function applyWorkbenchBackgrounds(lightBackground, darkBackground) {
  var rootStyle = document.documentElement.style;
  [
    ["--wb-user-bg-light", lightBackground],
    ["--wb-user-bg-dark", darkBackground],
  ].forEach(function (entry) {
    var value = typeof entry[1] === "string" ? entry[1].trim() : "";
    if (/^#[0-9a-f]{6}$/i.test(value)) rootStyle.setProperty(entry[0], value);
    else rootStyle.removeProperty(entry[0]);
  });
}

function WorkbenchRoot() {
  var dataStore = window.CyreneUI.require("data");
  dataStore.useVersion();
  var [themeMode, setThemeMode] = useStateBootstrap(function () {
    return readWorkbenchTweak("theme", "system");
  });
  var [accent, setAccent] = useStateBootstrap(function () {
    return readWorkbenchTweak("accent", null);
  });
  var [lightBackground, setLightBackground] = useStateBootstrap(function () {
    return readWorkbenchTweak("backgroundLight", null);
  });
  var [darkBackground, setDarkBackground] = useStateBootstrap(function () {
    return readWorkbenchTweak("backgroundDark", null);
  });
  var [textSize, setTextSize] = useStateBootstrap(function () {
    return readWorkbenchTweak("textSize", "default");
  });
  var [systemTheme, setSystemTheme] = useStateBootstrap(function () {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  var actualTheme = themeMode === "system" ? systemTheme : themeMode;
  var needsOnboarding = !!(
    dataStore.state.onboarding && dataStore.state.onboarding.needsOnboarding
  );

  useEffectBootstrap(function () {
    var media = window.matchMedia("(prefers-color-scheme: dark)");
    function onChange(event) {
      setSystemTheme(event.matches ? "dark" : "light");
    }
    media.addEventListener("change", onChange);
    return function () {
      media.removeEventListener("change", onChange);
    };
  }, []);

  useEffectBootstrap(function () {
    function refreshAppearance() {
      fetch("/api/settings/namespaces/appearance").then(function (response) {
        return response.ok ? response.json() : Promise.reject(new Error("appearance unavailable"));
      }).then(function (payload) {
        var values = payload.values || {};
        if (!values.appearance_migrated) return;
        var next = {
          // A settings_changed event can arrive while rapid top-bar toggles
          // are still queued. Keep the newest optimistic choice visible until
          // its matching backend write completes instead of flashing an older
          // server value between clicks.
          theme: workbenchThemePendingMode || values.theme || "system",
          accent: values.accent || null,
          backgroundLight: values.background_light || null,
          backgroundDark: values.background_dark || null,
          textSize: values.text_size || "default",
        };
        try {
          Object.keys(next).forEach(function (key) {
            localStorage.setItem("cyrene-tweak-" + key, JSON.stringify(next[key]));
          });
        } catch (error) {}
        setThemeMode(next.theme);
        setAccent(next.accent);
        setLightBackground(next.backgroundLight);
        setDarkBackground(next.backgroundDark);
        setTextSize(next.textSize);
      }).catch(function () {});
    }
    refreshAppearance();
    return window.CyreneUI.require("events").subscribe(function (event) {
      if (event && event.type === "settings_changed" && event.namespace === "appearance") {
        refreshAppearance();
      }
    });
  }, []);

  useEffectBootstrap(function () {
    writeWorkbenchThemeLocal(themeMode);
    document.documentElement.dataset.theme = actualTheme;
    delete document.documentElement.dataset.booting;
  }, [themeMode, actualTheme]);

  useEffectBootstrap(function () {
    var nextTextSize = textSize || "default";
    try {
      localStorage.removeItem("cyrene-tweak-density");
      localStorage.setItem("cyrene-tweak-textSize", JSON.stringify(nextTextSize));
    } catch (error) {}
    document.documentElement.dataset.density = "cozy";
    document.documentElement.dataset.textSize = nextTextSize;
  }, [textSize]);

  useEffectBootstrap(function () {
    applyWorkbenchAccent(accent);
  }, [accent, actualTheme, needsOnboarding]);

  useEffectBootstrap(function () {
    applyWorkbenchBackgrounds(lightBackground, darkBackground);
  }, [lightBackground, darkBackground]);

  useEffectBootstrap(function () {
    function onTheme() {
      setThemeMode(readWorkbenchTweak("theme", "system"));
    }
    function onAccent() {
      setAccent(readWorkbenchTweak("accent", null));
    }
    function onLightBackground() {
      setLightBackground(readWorkbenchTweak("backgroundLight", null));
    }
    function onDarkBackground() {
      setDarkBackground(readWorkbenchTweak("backgroundDark", null));
    }
    function onTextSize() {
      setTextSize(readWorkbenchTweak("textSize", "default"));
    }
    window.addEventListener("cyrene-tweak-theme-change", onTheme);
    window.addEventListener("cyrene-tweak-accent-change", onAccent);
    window.addEventListener("cyrene-tweak-backgroundLight-change", onLightBackground);
    window.addEventListener("cyrene-tweak-backgroundDark-change", onDarkBackground);
    window.addEventListener("cyrene-tweak-textSize-change", onTextSize);
    return function () {
      window.removeEventListener("cyrene-tweak-theme-change", onTheme);
      window.removeEventListener("cyrene-tweak-accent-change", onAccent);
      window.removeEventListener("cyrene-tweak-backgroundLight-change", onLightBackground);
      window.removeEventListener("cyrene-tweak-backgroundDark-change", onDarkBackground);
      window.removeEventListener("cyrene-tweak-textSize-change", onTextSize);
    };
  }, []);

  function toggleWorkbenchTheme() {
    var order = ["system", "light", "dark"];
    var index = order.indexOf(themeMode);
    var next = order[(index + 1) % order.length];
    writeWorkbenchThemeLocal(next);
    setThemeMode(next);
    persistWorkbenchTheme(next);
  }

  var WorkbenchApp = window.CyreneUI.require("shell").App;
  if (typeof WorkbenchApp !== "function") {
    return (
      <main className="workbench-bootstrap-error" role="alert">
        {bootstrapT("workbench.bootstrapLoadFailed", "Cyrene Workbench failed to load.")}
      </main>
    );
  }
  return (
    <WorkbenchApp
      theme={themeMode}
      actualTheme={actualTheme}
      onToggleTheme={toggleWorkbenchTheme}
      needsOnboarding={needsOnboarding}
    />
  );
}

function QuickChatRoot() {
  useEffectBootstrap(function () {
    Promise.resolve(window.CyreneUI.require("data").ready)
      .catch(function () {})
      .then(function () {
        window.CyreneUI.require("readiness").markReady();
      });
  }, []);
  var QuickChatApp = window.CyreneUI.require("quickChat").App;
  if (typeof QuickChatApp !== "function") {
    return (
      <main className="workbench-bootstrap-error" role="alert">
        {bootstrapT("workbench.quickChatLoadFailed", "Cyrene Quick Chat failed to load.")}
      </main>
    );
  }
  return <QuickChatApp />;
}

function DetachedPaneRoot() {
  useEffectBootstrap(function () {
    var mode = readWorkbenchTweak("theme", "system");
    var actual = mode === "system"
      ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : mode;
    document.documentElement.dataset.theme = actual;
    document.documentElement.dataset.density = "cozy";
    document.documentElement.dataset.textSize = readWorkbenchTweak("textSize", "default") || "default";
    document.body.classList.add("wbc-detached-pane-surface");
    applyWorkbenchAccent(readWorkbenchTweak("accent", null));
    applyWorkbenchBackgrounds(
      readWorkbenchTweak("backgroundLight", null),
      readWorkbenchTweak("backgroundDark", null),
    );
    delete document.documentElement.dataset.booting;
    Promise.resolve(window.CyreneUI.require("data").ready).catch(function () {}).then(function () {
      var readiness = window.CyreneUI.has("readiness") ? window.CyreneUI.require("readiness") : null;
      if (readiness && typeof readiness.markReady === "function") readiness.markReady();
    });
    return function () { document.body.classList.remove("wbc-detached-pane-surface"); };
  }, []);
  var DetachedPaneApp = window.CyreneUI.require("chat").DetachedPaneApp;
  return typeof DetachedPaneApp === "function"
    ? <DetachedPaneApp />
    : <main className="workbench-bootstrap-error" role="alert">{bootstrapT("workbench.detachedPaneLoadFailed", "Cyrene pane failed to load.")}</main>;
}

var WORKBENCH_REQUIRED_SERVICES = [
  "browser", "chat", "create", "data", "events", "feedback", "i18n",
  "library", "memory", "model", "navigation", "profile", "schedule",
  "search", "settings", "shell", "shortcuts", "welcome",
];

function workbenchServicesReady(surface) {
  var required = surface === "quick-chat"
    ? ["chat", "data", "events", "feedback", "i18n", "quickChat", "readiness"]
    : surface === "detached-pane"
      ? ["api", "browser", "chat", "data", "events", "feedback", "i18n", "markdown", "readiness"]
    : WORKBENCH_REQUIRED_SERVICES;
  return required.every(function (name) { return window.CyreneUI.has(name); });
}

function WorkbenchBootstrap() {
  var surface = readWorkbenchSurface();
  return surface === "quick-chat"
    ? <QuickChatRoot />
    : surface === "detached-pane"
      ? <DetachedPaneRoot />
    : <WorkbenchRoot />;
}

var WorkbenchBootstrapService = {
  readSurface: readWorkbenchSurface,
  readTweak: readWorkbenchTweak,
  applyAccent: applyWorkbenchAccent,
  applyBackgrounds: applyWorkbenchBackgrounds,
  servicesReady: workbenchServicesReady,
  Root: WorkbenchBootstrap,
};
window.CyreneUI.bootstrap = window.CyreneUI.register(
  "bootstrap",
  WorkbenchBootstrapService,
);

var workbenchReactRoot = ReactDOM.createRoot(document.getElementById("root"));
function disposeInvalidatedWorkbenchPage() {
  try { workbenchReactRoot.unmount(); } catch (error) {}
}
window.addEventListener(
  "cyrene:page-invalidated",
  disposeInvalidatedWorkbenchPage,
  { once: true },
);
if (window.CyrenePageLifecycle && window.CyrenePageLifecycle.isInvalidated()) {
  disposeInvalidatedWorkbenchPage();
} else {
  var workbenchMountStartedAt = Date.now();
  var workbenchMountRetryKey = "cyrene-ui-service-load-retries";
  function mountWorkbenchPage() {
    var surface = readWorkbenchSurface();
    if (workbenchServicesReady(surface)) {
      try { sessionStorage.removeItem(workbenchMountRetryKey); } catch (error) {}
      workbenchReactRoot.render(<WorkbenchBootstrap />);
      return;
    }
    if (Date.now() - workbenchMountStartedAt < 2500) {
      window.setTimeout(mountWorkbenchPage, 50);
      return;
    }
    var required = surface === "quick-chat"
      ? ["chat", "data", "events", "feedback", "i18n", "quickChat", "readiness"]
      : surface === "detached-pane"
        ? ["api", "browser", "chat", "data", "events", "feedback", "i18n", "markdown", "readiness"]
        : WORKBENCH_REQUIRED_SERVICES;
    var missing = required.filter(function (name) { return !window.CyreneUI.has(name); });
    var retries = 0;
    try { retries = Number(sessionStorage.getItem(workbenchMountRetryKey) || 0); } catch (error) {}
    if (retries < 2) {
      try { sessionStorage.setItem(workbenchMountRetryKey, String(retries + 1)); } catch (error) {}
      console.warn("Cyrene UI services did not finish loading; retrying page: " + missing.join(", "));
      window.location.reload();
      return;
    }
    console.error("Cyrene UI services failed to load: " + missing.join(", "));
    workbenchReactRoot.render(
      <main className="workbench-bootstrap-error" role="alert">
        {bootstrapT("workbench.bootstrapLoadFailed", "Cyrene Workbench failed to load.")}
      </main>,
    );
  }
  mountWorkbenchPage();
}
