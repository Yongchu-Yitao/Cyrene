// Cyrene in-app tutorial catalog. Pure data — all copy lives in
// workbench-i18n.jsx under the `tour.*` namespace, so guides stay language
// neutral and version with the app.
//
// Step shape:
//   { id, target?, navigate?: { page }, openSettings?: tabId,
//     interact?: "click" | "type", tipKey?, points? }
//   - no target  -> doc step, rendered inside the tutorial center
//   - target     -> spotlight step: scrims highlight the real UI element
//   - navigate / openSettings run before the step is shown
//   - interact   -> bubble hint copy ("try clicking…" / "try typing…").
//                   For "click" steps, clicking the highlighted element
//                   advances automatically; "type" steps wait for the user.
//   - points     -> bullet list appended to the step body
//
// titleKey/bodyKey are derived: "tour.<guideId>.<stepId>.title|.body|.tip"
// and "tour.<guideId>.<stepId>.p<n>" for points.
(function (root) {
  "use strict";

  var GUIDES = [
    {
      id: "overview",
      module: "overview",
      order: 1,
      minutes: 2,
      titleKey: "tour.overview.title",
      descKey: "tour.overview.desc",
      steps: [
        { id: "rail", target: "rail_chat", interact: "click",
          bodyKey: "tour.overview.rail.body" },
        { id: "dock", target: "rail_task", interact: "click",
          bodyKey: "tour.overview.dock.body" },
        { id: "search", target: "open_search", interact: "click",
          bodyKey: "tour.overview.search.body" },
        { id: "settings", target: "open_settings", interact: "click",
          bodyKey: "tour.overview.settings.body" },
        { id: "notifications", target: "topbar_notifications", interact: "click",
          bodyKey: "tour.overview.notifications.body" },
        { id: "resources", target: "topbar_resources", interact: "click",
          bodyKey: "tour.overview.resources.body" },
        { id: "help", target: "topbar_help", interact: "click",
          bodyKey: "tour.overview.help.body" },
        { id: "done", points: ["tour.overview.done.p1", "tour.overview.done.p2"],
          bodyKey: "tour.overview.done.body" },
      ],
    },
    {
      id: "project-basics",
      module: "overview",
      order: 2,
      minutes: 2,
      titleKey: "tour.project-basics.title",
      descKey: "tour.project-basics.desc",
      steps: [
        { id: "switcher", target: "project_switcher", interact: "click",
          bodyKey: "tour.project-basics.switcher.body" },
        { id: "new", points: ["tour.project-basics.new.p1", "tour.project-basics.new.p2"],
          bodyKey: "tour.project-basics.new.body" },
        { id: "memory", target: "project_memory", interact: "click",
          bodyKey: "tour.project-basics.memory.body" },
        { id: "done", points: ["tour.project-basics.done.p1", "tour.project-basics.done.p2", "tour.project-basics.done.p3"],
          bodyKey: "tour.project-basics.done.body" },
      ],
    },
    {
      id: "chat-basics",
      module: "chat",
      order: 1,
      minutes: 3,
      titleKey: "tour.chat-basics.title",
      descKey: "tour.chat-basics.desc",
      steps: [
        { id: "composer", target: "chat_composer", interact: "type",
          bodyKey: "tour.chat-basics.composer.body" },
        { id: "perm", target: "chat_model_picker", interact: "click",
          bodyKey: "tour.chat-basics.perm.body" },
        { id: "attach", target: "chat_attach", interact: "click",
          bodyKey: "tour.chat-basics.attach.body" },
        { id: "tools", target: "chat_tools", interact: "click",
          bodyKey: "tour.chat-basics.tools.body" },
        { id: "voice", target: "chat_voice", interact: "click",
          bodyKey: "tour.chat-basics.voice.body" },
        { id: "send", points: ["tour.chat-basics.send.p1", "tour.chat-basics.send.p2"],
          bodyKey: "tour.chat-basics.send.body" },
      ],
    },
    {
      id: "chat-deep",
      module: "chat",
      order: 2,
      minutes: 4,
      titleKey: "tour.chat-deep.title",
      descKey: "tour.chat-deep.desc",
      steps: [
        { id: "sidebar", target: "chat_sidebar", interact: "click",
          bodyKey: "tour.chat-deep.sidebar.body" },
        { id: "split", target: "chat_split_pane", interact: "click",
          bodyKey: "tour.chat-deep.split.body" },
        { id: "panels", points: ["tour.chat-deep.panels.p1", "tour.chat-deep.panels.p2", "tour.chat-deep.panels.p3", "tour.chat-deep.panels.p4"],
          bodyKey: "tour.chat-deep.panels.body" },
        { id: "retry", target: "chat_retry", interact: "click",
          bodyKey: "tour.chat-deep.retry.body" },
        { id: "permission", points: ["tour.chat-deep.permission.p1", "tour.chat-deep.permission.p2"],
          bodyKey: "tour.chat-deep.permission.body" },
        { id: "blocks", points: ["tour.chat-deep.blocks.p1", "tour.chat-deep.blocks.p2", "tour.chat-deep.blocks.p3"],
          bodyKey: "tour.chat-deep.blocks.body" },
      ],
    },
    {
      id: "task-basics",
      module: "task",
      order: 1,
      minutes: 4,
      titleKey: "tour.task-basics.title",
      descKey: "tour.task-basics.desc",
      steps: [
        { id: "board", target: "task_board", interact: "click",
          bodyKey: "tour.task-basics.board.body" },
        { id: "new", target: "task_new", interact: "click",
          bodyKey: "tour.task-basics.new.body" },
        { id: "plan", points: ["tour.task-basics.plan.p1", "tour.task-basics.plan.p2"],
          bodyKey: "tour.task-basics.plan.body" },
        { id: "approve", points: ["tour.task-basics.approve.p1", "tour.task-basics.approve.p2"],
          bodyKey: "tour.task-basics.approve.body" },
        { id: "control", points: ["tour.task-basics.control.p1", "tour.task-basics.control.p2"],
          bodyKey: "tour.task-basics.control.body" },
      ],
    },
    {
      id: "knowledge-basics",
      module: "knowledge",
      order: 1,
      minutes: 3,
      titleKey: "tour.knowledge-basics.title",
      descKey: "tour.knowledge-basics.desc",
      steps: [
        { id: "upload", target: "knowledge_add", interact: "click",
          bodyKey: "tour.knowledge-basics.upload.body" },
        { id: "collections", target: "knowledge_collections", interact: "click",
          bodyKey: "tour.knowledge-basics.collections.body" },
        { id: "tags", target: "knowledge_tags", interact: "click",
          bodyKey: "tour.knowledge-basics.tags.body" },
        { id: "embedding", points: ["tour.knowledge-basics.embedding.p1", "tour.knowledge-basics.embedding.p2"],
          bodyKey: "tour.knowledge-basics.embedding.body" },
      ],
    },
    {
      id: "memory-basics",
      module: "memory",
      order: 1,
      minutes: 2,
      titleKey: "tour.memory-basics.title",
      descKey: "tour.memory-basics.desc",
      steps: [
        { id: "new", target: "memory_new", interact: "click",
          bodyKey: "tour.memory-basics.new.body" },
        { id: "skills", target: "memory_skills", interact: "click",
          bodyKey: "tour.memory-basics.skills.body" },
        { id: "auto", points: ["tour.memory-basics.auto.p1", "tour.memory-basics.auto.p2"],
          bodyKey: "tour.memory-basics.auto.body" },
      ],
    },
    {
      id: "schedule-basics",
      module: "schedule",
      order: 1,
      minutes: 2,
      titleKey: "tour.schedule-basics.title",
      descKey: "tour.schedule-basics.desc",
      steps: [
        { id: "views", target: "schedule_views", interact: "click",
          bodyKey: "tour.schedule-basics.views.body" },
        { id: "new", target: "schedule_new", interact: "click",
          bodyKey: "tour.schedule-basics.new.body" },
        { id: "repeat", points: ["tour.schedule-basics.repeat.p1", "tour.schedule-basics.repeat.p2"],
          bodyKey: "tour.schedule-basics.repeat.body" },
        { id: "detail", points: ["tour.schedule-basics.detail.p1"],
          bodyKey: "tour.schedule-basics.detail.body" },
      ],
    },
    {
      id: "settings-overview",
      module: "settings",
      order: 1,
      minutes: 4,
      titleKey: "tour.settings-overview.title",
      descKey: "tour.settings-overview.desc",
      steps: [
        { id: "general", openSettings: "general", target: "settings_content_general", interact: "click",
          bodyKey: "tour.settings-overview.general.body" },
        { id: "models", openSettings: "models", target: "settings_content_models", interact: "click",
          bodyKey: "tour.settings-overview.models.body" },
        { id: "appearance", openSettings: "appearance", target: "settings_content_appearance", interact: "click",
          bodyKey: "tour.settings-overview.appearance.body" },
        { id: "capabilities", openSettings: "capabilities", target: "settings_content_capabilities", interact: "click",
          bodyKey: "tour.settings-overview.capabilities.body" },
        { id: "shortcuts", openSettings: "shortcuts", target: "settings_content_shortcuts", interact: "click",
          bodyKey: "tour.settings-overview.shortcuts.body" },
        { id: "data", openSettings: "data", target: "settings_content_data", interact: "click",
          bodyKey: "tour.settings-overview.data.body" },
        { id: "rest", points: ["tour.settings-overview.rest.p1", "tour.settings-overview.rest.p2"],
          bodyKey: "tour.settings-overview.rest.body" },
      ],
    },
    {
      id: "shortcuts",
      module: "shortcuts",
      order: 1,
      minutes: 2,
      titleKey: "tour.shortcuts.title",
      descKey: "tour.shortcuts.desc",
      steps: [
        { id: "list", points: ["tour.shortcuts.list.p1", "tour.shortcuts.list.p2"],
          bodyKey: "tour.shortcuts.list.body" },
        { id: "rebind", openSettings: "shortcuts", target: "settings_content_shortcuts", interact: "click",
          bodyKey: "tour.shortcuts.rebind.body" },
        { id: "palette", points: ["tour.shortcuts.palette.p1"],
          bodyKey: "tour.shortcuts.palette.body" },
      ],
    },
    {
      id: "browser-live",
      module: "extras",
      order: 1,
      minutes: 2,
      titleKey: "tour.browser-live.title",
      descKey: "tour.browser-live.desc",
      steps: [
        { id: "open", target: "browser_viewport", interact: "click",
          bodyKey: "tour.browser-live.open.body" },
        { id: "control", points: ["tour.browser-live.control.p1", "tour.browser-live.control.p2"],
          bodyKey: "tour.browser-live.control.body" },
      ],
    },
    {
      id: "quick-chat",
      module: "extras",
      order: 2,
      minutes: 1,
      titleKey: "tour.quick-chat.title",
      descKey: "tour.quick-chat.desc",
      steps: [
        { id: "shortcut", points: ["tour.quick-chat.shortcut.p1"],
          bodyKey: "tour.quick-chat.shortcut.body" },
        { id: "screenshot", points: ["tour.quick-chat.screenshot.p1", "tour.quick-chat.screenshot.p2"],
          bodyKey: "tour.quick-chat.screenshot.body" },
      ],
    },
    {
      id: "search",
      module: "extras",
      order: 3,
      minutes: 1,
      titleKey: "tour.search.title",
      descKey: "tour.search.desc",
      steps: [
        { id: "open", target: "open_search", interact: "click",
          bodyKey: "tour.search.open.body" },
        { id: "commands", points: ["tour.search.commands.p1", "tour.search.commands.p2"],
          bodyKey: "tour.search.commands.body" },
      ],
    },
    {
      id: "profile-tray",
      module: "extras",
      order: 4,
      minutes: 1,
      titleKey: "tour.profile-tray.title",
      descKey: "tour.profile-tray.desc",
      steps: [
        { id: "profile", navigate: { page: "profile" },
          bodyKey: "tour.profile-tray.profile.body" },
        { id: "tray", points: ["tour.profile-tray.tray.p1", "tour.profile-tray.tray.p2"],
          bodyKey: "tour.profile-tray.tray.body" },
      ],
    },
  ];

  var MODULES = [
    { id: "overview", labelKey: "tour.module.overview" },
    { id: "chat", labelKey: "tour.module.chat" },
    { id: "task", labelKey: "tour.module.task" },
    { id: "knowledge", labelKey: "tour.module.knowledge" },
    { id: "memory", labelKey: "tour.module.memory" },
    { id: "schedule", labelKey: "tour.module.schedule" },
    { id: "settings", labelKey: "tour.module.settings" },
    { id: "shortcuts", labelKey: "tour.module.shortcuts" },
    { id: "extras", labelKey: "tour.module.extras" },
  ];

  function findGuide(id) {
    for (var i = 0; i < GUIDES.length; i += 1) {
      if (GUIDES[i].id === id) return GUIDES[i];
    }
    return null;
  }

  function stepKey(guideId, stepId, suffix) {
    return "tour." + guideId + "." + stepId + "." + suffix;
  }

  // Catalog grouped by MODULES order; modules with no guides are dropped.
  function catalog() {
    return MODULES.map(function (module) {
      var guides = GUIDES.filter(function (guide) { return guide.module === module.id; })
        .sort(function (a, b) { return a.order - b.order; });
      return { id: module.id, labelKey: module.labelKey, guides: guides };
    }).filter(function (entry) { return entry.guides.length > 0; });
  }

  var service = {
    list: function () { return GUIDES.slice(); },
    find: findGuide,
    catalog: catalog,
    stepKey: stepKey,
    modules: MODULES,
  };
  root.CyreneUI.register("tour-guides", service);
})(window);
