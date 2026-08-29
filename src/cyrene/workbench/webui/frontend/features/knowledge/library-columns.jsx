var LIBRARY_TABLE_COLUMN_IDS = ["title", "author", "year", "source", "added", "tags"];
var LIBRARY_TABLE_COLUMN_WIDTHS = {
  title: "minmax(0, 4fr)",
  author: "minmax(0, 1.25fr)",
  year: "minmax(52px, .55fr)",
  source: "minmax(0, 1fr)",
  added: "minmax(118px, .85fr)",
  tags: "minmax(0, .9fr)",
};

function normalizeLibraryTableColumns(value) {
  var requested = Array.isArray(value) ? value : [];
  return LIBRARY_TABLE_COLUMN_IDS.filter(function (id) {
    return id === "title" || requested.indexOf(id) >= 0;
  });
}

function libraryTableGridTemplate(visibleColumns) {
  return ["36px"].concat(visibleColumns.map(function (id) {
    return LIBRARY_TABLE_COLUMN_WIDTHS[id];
  })).join(" ");
}

function libraryContextMenuTheme() {
  var portalTheme = {};
  var themeSource = document.querySelector(".workbench-shell");
  if (!themeSource || typeof getComputedStyle !== "function") return portalTheme;
  var computedTheme = getComputedStyle(themeSource);
  [
    "--wb-card-bg", "--wb-surface", "--wb-line", "--wb-line-2",
    "--wb-text", "--wb-muted", "--wb-faint", "--wb-accent",
    "--wb-row-hover-bg", "--wb-red", "--wb-ui-font-scale",
  ].forEach(function (name) { portalTheme[name] = computedTheme.getPropertyValue(name); });
  portalTheme.fontFamily = computedTheme.fontFamily;
  portalTheme.colorScheme = computedTheme.colorScheme;
  return portalTheme;
}

function LibraryTableHead(props) {
  var h = React.createElement;
  var visible = props.visibleColumns;
  var t = props.t;
  return h("div", {
    className: "wb-lib-table-head wb-lib-table-grid",
    role: "row",
    "data-cyrene-context-menu": "true",
    style: { gridTemplateColumns: libraryTableGridTemplate(visible) },
    onContextMenu: function (event) {
      event.preventDefault();
      event.stopPropagation();
      props.onColumnContextMenu(event);
    },
  },
    h("label", { className: "wb-lib-check" }, h("input", { type: "checkbox", checked: props.allSelected, onChange: props.onToggleAll, "aria-label": t("library.selectAll", "Select all knowledge") }), h("span")),
    visible.indexOf("title") >= 0 && h("span", { className: "wb-lib-title-head" }, t("library.column.title", "Title")),
    visible.indexOf("author") >= 0 && h("span", null, t("library.column.author", "Author")),
    visible.indexOf("year") >= 0 && h("span", null, t("library.column.year", "Year")),
    visible.indexOf("source") >= 0 && h("span", null, t("library.column.source", "Source")),
    visible.indexOf("added") >= 0 && h("span", null, t("library.column.added", "Added")),
    visible.indexOf("tags") >= 0 && h("span", null, t("library.column.tags", "Tags")));
}

function useLibraryColumns(options) {
  var state = React.useState(function () {
    try {
      var saved = window.localStorage.getItem("cyrene.library.tableColumns");
      return saved ? normalizeLibraryTableColumns(JSON.parse(saved)) : LIBRARY_TABLE_COLUMN_IDS.slice();
    } catch (_) { return LIBRARY_TABLE_COLUMN_IDS.slice(); }
  });
  var visible = state[0];
  var setVisible = state[1];
  var menuState = React.useState(null);
  var menu = menuState[0];
  var setMenu = menuState[1];
  React.useEffect(function () {
    try { window.localStorage.setItem("cyrene.library.tableColumns", JSON.stringify(visible)); } catch (_) {}
  }, [visible.join("|")]);
  React.useEffect(function () {
    if (!menu) return undefined;
    function close() { setMenu(null); }
    function onKey(event) { if (event.key === "Escape") close(); }
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    document.addEventListener("keydown", onKey);
    return function () {
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [!!menu]);
  function open(event) {
    options.closeMenus();
    setMenu({
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - 228)),
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - 260)),
      portalTheme: libraryContextMenuTheme(),
    });
  }
  function toggle(id) {
    if (id === "title") return;
    setVisible(function (current) {
      return normalizeLibraryTableColumns(current.indexOf(id) >= 0
        ? current.filter(function (value) { return value !== id; })
        : current.concat([id]));
    });
  }
  var labels = [
    ["title", options.t("library.column.title", "Title"), true],
    ["author", options.t("library.column.author", "Author")],
    ["year", options.t("library.column.year", "Year")],
    ["source", options.t("library.column.source", "Source")],
    ["added", options.t("library.column.added", "Added")],
    ["tags", options.t("library.column.tags", "Tags")],
  ];
  var portal = menu && typeof ReactDOM !== "undefined" ? ReactDOM.createPortal(
    React.createElement("div", { className: "wb-lib-context-layer", style: menu.portalTheme },
      React.createElement("div", { className: "wb-lib-context-scrim", onPointerDown: function () { setMenu(null); } }),
      React.createElement("div", { className: "wb-lib-context-menu wb-lib-column-menu", role: "menu", "aria-label": options.t("library.displayedColumns", "Displayed columns"), style: { left: menu.left + "px", top: menu.top + "px" }, onContextMenu: function (event) { event.preventDefault(); } },
        React.createElement("div", { className: "wb-lib-column-menu-title" }, options.t("library.displayedColumns", "Displayed columns")),
        labels.map(function (column) {
          var selected = visible.indexOf(column[0]) >= 0;
          return React.createElement("button", { key: column[0], type: "button", role: "menuitemcheckbox", "aria-checked": selected, disabled: !!column[2], onClick: function () { toggle(column[0]); } },
            React.createElement("span", { className: "wb-lib-column-check" + (selected ? " checked" : "") }, selected && options.icon("check", 12)),
            React.createElement("span", null, column[1]), column[2] && React.createElement("small", null, options.t("library.columnAlwaysShown", "Always shown")));
        }))), document.body) : null;
  return { close: function () { setMenu(null); }, open: open, portal: portal, visible: visible };
}

function selectLibraryView(nextView, setView) {
  if (nextView !== "table" && nextView !== "grid") return;
  try { window.localStorage.setItem("cyrene.library.viewMode", nextView); } catch (_) {}
  setView(nextView);
}

export {
  LibraryTableHead,
  libraryContextMenuTheme,
  libraryTableGridTemplate,
  selectLibraryView,
  useLibraryColumns,
};
