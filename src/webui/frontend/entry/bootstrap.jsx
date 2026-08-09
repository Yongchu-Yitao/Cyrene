// Workbench-only browser bootstrap. Quick Chat remains an independent surface
// backed by the same platform data, event, and API services.
var { useState: useStateBootstrap, useEffect: useEffectBootstrap } = React;

function readWorkbenchTweak(key, fallback) {
  try {
    var value = localStorage.getItem("cyrene-tweak-" + key);
    return value !== null ? JSON.parse(value) : fallback;
  } catch (error) {
    return fallback;
  }
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
  var [density, setDensity] = useStateBootstrap(function () {
    return readWorkbenchTweak("density", "cozy");
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
    try {
      localStorage.setItem("cyrene-tweak-theme", JSON.stringify(themeMode));
      localStorage.setItem("cyrene-theme-mode", themeMode);
    } catch (error) {}
    document.documentElement.dataset.theme = actualTheme;
    delete document.documentElement.dataset.booting;
  }, [themeMode, actualTheme]);

  useEffectBootstrap(function () {
    var nextDensity = density || "cozy";
    var nextTextSize = textSize || "default";
    try {
      localStorage.setItem("cyrene-tweak-density", JSON.stringify(nextDensity));
      localStorage.setItem("cyrene-tweak-textSize", JSON.stringify(nextTextSize));
    } catch (error) {}
    document.documentElement.dataset.density = nextDensity;
    document.documentElement.dataset.textSize = nextTextSize;
  }, [density, textSize]);

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
    function onDensity() {
      setDensity(readWorkbenchTweak("density", "cozy"));
    }
    function onTextSize() {
      setTextSize(readWorkbenchTweak("textSize", "default"));
    }
    window.addEventListener("cyrene-tweak-theme-change", onTheme);
    window.addEventListener("cyrene-tweak-accent-change", onAccent);
    window.addEventListener("cyrene-tweak-backgroundLight-change", onLightBackground);
    window.addEventListener("cyrene-tweak-backgroundDark-change", onDarkBackground);
    window.addEventListener("cyrene-tweak-density-change", onDensity);
    window.addEventListener("cyrene-tweak-textSize-change", onTextSize);
    return function () {
      window.removeEventListener("cyrene-tweak-theme-change", onTheme);
      window.removeEventListener("cyrene-tweak-accent-change", onAccent);
      window.removeEventListener("cyrene-tweak-backgroundLight-change", onLightBackground);
      window.removeEventListener("cyrene-tweak-backgroundDark-change", onDarkBackground);
      window.removeEventListener("cyrene-tweak-density-change", onDensity);
      window.removeEventListener("cyrene-tweak-textSize-change", onTextSize);
    };
  }, []);

  function toggleWorkbenchTheme() {
    var order = ["system", "light", "dark"];
    var index = order.indexOf(themeMode);
    setThemeMode(order[(index + 1) % order.length]);
  }

  var WorkbenchApp = window.CyreneUI.require("shell").App;
  if (typeof WorkbenchApp !== "function") {
    return (
      <main className="workbench-bootstrap-error" role="alert">
        Cyrene Workbench failed to load.
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
        Cyrene Quick Chat failed to load.
      </main>
    );
  }
  return <QuickChatApp />;
}

function WorkbenchBootstrap() {
  return readWorkbenchSurface() === "quick-chat"
    ? <QuickChatRoot />
    : <WorkbenchRoot />;
}

var WorkbenchBootstrapService = {
  readSurface: readWorkbenchSurface,
  readTweak: readWorkbenchTweak,
  applyAccent: applyWorkbenchAccent,
  applyBackgrounds: applyWorkbenchBackgrounds,
  Root: WorkbenchBootstrap,
};
window.CyreneUI.bootstrap = window.CyreneUI.register(
  "bootstrap",
  WorkbenchBootstrapService,
);

ReactDOM.createRoot(document.getElementById("root")).render(<WorkbenchBootstrap />);
