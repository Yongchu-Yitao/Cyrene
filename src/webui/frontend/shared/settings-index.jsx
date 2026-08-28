// Workbench settings index — shared by the search overlay (settings results)
// and the settings overlay (anchor scrolling). The search overlay matches
// items by their translated label/hint/keywords; clicking one opens the
// settings overlay on the item's tab and scrolls to the DOM element whose id
// equals the item id. Keep ids stable and unique across the whole index.
window.CyreneUI.settingsIndex = window.CyreneUI.register("settings-index", {
  // Tab-level entries let a query jump straight to a settings tab.
  tabs: [
    { id: "profile", labelKey: "rail.profile" },
    { id: "general", labelKey: "settings.general" },
    { id: "search", labelKey: "settings.searchProviders" },
    { id: "appearance", labelKey: "settings.appearance" },
    { id: "shortcuts", labelKey: "settings.shortcuts" },
    { id: "models", labelKey: "settings.modelServices" },
    { id: "media", labelKey: "settings.mediaGeneration" },
    { id: "model-usage", labelKey: "settings.modelUsage" },
    { id: "agents", labelKey: "settings.agents" },
    { id: "voice", labelKey: "settings.voiceTab" },
    { id: "channels", labelKey: "settings.channels" },
    { id: "remote", labelKey: "settings.remoteTab" },
    { id: "plugin-registry", labelKey: "settings.pluginRegistry" },
    { id: "hooks", labelKey: "settings.hooks" },
    { id: "integrations", labelKey: "settings.integrations" },
    { id: "budget", labelKey: "settings.budget" },
    { id: "usage", labelKey: "settings.usage" },
    { id: "data", labelKey: "settings.data" },
    { id: "about", labelKey: "settings.about" },
  ],
  // Item-level entries: id doubles as the DOM anchor rendered by the settings
  // overlay, so every item here must have a matching anchor on its panel.
  items: [
    // ── General ──
    { id: "setting-language", tab: "general", labelKey: "settings.language", hintKey: "settings.languageHint", keywords: ["语言", "lang", "中文", "english"] },
    { id: "setting-timezone", tab: "general", labelKey: "settings.timezone", hintKey: "settings.timezoneHint", keywords: ["时区", "timezone"] },
    { id: "setting-desktop-notifications", tab: "general", labelKey: "settings.desktopNotifications", hintKey: "settings.desktopNotificationsHint", keywords: ["通知", "notify", "notification"] },
    { id: "setting-agent-proxy", tab: "general", labelKey: "settings.agentProxy", hintKey: "settings.agentProxyHint", keywords: ["代理", "proxy", "端口", "port"] },
    { id: "setting-map-provider", tab: "general", labelKey: "settings.mapProvider", hintKey: "settings.mapProviderHint", keywords: ["地图", "map", "amap", "高德"] },
    { id: "setting-amap-key", tab: "general", labelKey: "settings.amapKey", hintKey: "settings.amapKeyHint", keywords: ["地图", "amap", "高德", "key", "密钥"] },
    { id: "setting-run-in-background", tab: "general", labelKey: "settings.runInBackground", hintKey: "settings.runInBackgroundHint", keywords: ["后台", "常驻", "resident", "background"] },
    { id: "setting-quick-chat", tab: "general", labelKey: "settings.quickChatAssistant", hintKey: "settings.quickChatAssistantHint", keywords: ["快捷对话", "quick", "chat", "截图"] },
    { id: "setting-search-enabled", tab: "search", labelKey: "settings.searchEnabled", hintKey: "settings.searchEnabledHint", keywords: ["搜索", "search", "联网", "开关"] },
    { id: "setting-search-providers", tab: "search", labelKey: "settings.searchProviders", hintKey: "settings.searchFallbackHint", keywords: ["搜索器", "search provider", "simplexng", "deepseek", "tavily", "brave", "fallback", "顺位"] },
    { id: "setting-zotero", tab: "integrations", labelKey: "settings.zoteroIntegration", hintKey: "settings.zoteroIntegrationHint", keywords: ["zotero", "文献", "引用", "集成"] },
    // ── Models ──
    { id: "setting-model-connections", tab: "models", labelKey: "settings.modelServices", keywords: ["模型服务", "provider", "api", "key", "base url", "codex", "ollama", "连接"] },
    { id: "setting-model-primary-route", tab: "model-usage", labelKey: "settings.primaryModelSlot", keywords: ["默认模型", "主模型", "顺位", "fallback", "model"] },
    { id: "setting-model-vision-route", tab: "model-usage", labelKey: "settings.visionModelSlot", keywords: ["识图模型", "视觉模型", "vision", "图像"] },
    { id: "setting-model-secondary-route", tab: "model-usage", labelKey: "settings.secondaryModelSlot", hintKey: "settings.secondaryModelHint", keywords: ["次要模型", "secondary", "后备"] },
    { id: "setting-model-embedding-route", tab: "model-usage", labelKey: "settings.embeddingIntegration", hintKey: "settings.embeddingIntegrationHint", keywords: ["嵌入", "embedding", "向量", "vector", "知识库"] },
    // ── Media generation ──
    { id: "setting-media-runtime", tab: "media", labelKey: "settings.mediaRuntime", hintKey: "settings.mediaRuntimeHint", keywords: ["媒体生成", "高级任务", "后台生成", "并发", "重试", "下载上限", "background generation", "parallel", "retry"] },
    { id: "setting-media-defaults", tab: "media", labelKey: "settings.mediaDefaultProviders", hintKey: "settings.mediaDefaultProvidersHint", keywords: ["图片", "视频", "音频", "音乐", "参考素材", "image", "video", "audio", "music", "reference", "default provider"] },
    { id: "setting-media-providers", tab: "media", labelKey: "settings.mediaProviders", hintKey: "settings.mediaProvidersHint", keywords: ["provider", "API", "生成服务", "模型服务", "连接", "reference", "image", "video", "audio"] },
    { id: "setting-media-openai", tab: "media", labelKey: "settings.mediaOpenAi", hintKey: "settings.mediaProviderHint.openai", keywords: ["openai", "gpt-image", "dall-e", "reference", "image", "mask"] },
    { id: "setting-media-seedream", tab: "media", labelKey: "settings.mediaSeedream", hintKey: "settings.mediaProviderHint.seedream", keywords: ["seedream", "即梦", "豆包", "volcengine", "byteplus", "reference", "image"] },
    { id: "setting-media-seedance", tab: "media", labelKey: "settings.mediaSeedance", hintKey: "settings.mediaProviderHint.seedance", keywords: ["seedance", "即梦", "豆包", "volcengine", "byteplus", "reference", "image", "video"] },
    { id: "setting-media-minimax", tab: "media", labelKey: "settings.mediaMiniMax", hintKey: "settings.mediaProviderHint.minimax", keywords: ["minimax", "MiniMax-H3", "H3", "hailuo", "海螺", "music-3.0", "reference", "image", "video", "audio"] },
    { id: "setting-media-google", tab: "media", labelKey: "settings.mediaGoogle", hintKey: "settings.mediaProviderHint.google", keywords: ["google", "gemini", "omni", "gemini-omni-flash-preview", "veo", "imagen", "reference", "image", "video", "audio"] },
    // ── Channels ──
    { id: "setting-telegram", tab: "channels", labelKey: "settings.telegram", keywords: ["telegram", "电报"] },
    { id: "setting-wechat", tab: "channels", labelKey: "settings.wechat", keywords: ["微信", "wechat", "weixin"] },
    // ── Agents ──
    { id: "setting-soul", tab: "agents", labelKey: "settings.soulMd", hintKey: "settings.soulMdHint", keywords: ["soul", "人格", "人设", "角色"] },
    { id: "setting-spawn-policy", tab: "agents", labelKey: "settings.spawnPolicy", hintKey: "settings.spawnPolicyHint", keywords: ["子代理", "spawn", "策略", "policy", "并发"] },
    { id: "setting-agent-proactive", tab: "agents", labelKey: "settings.agentProactive", hintKey: "settings.agentProactiveHint", keywords: ["主动", "proactive", "自发"] },
    { id: "setting-heartbeat", tab: "agents", labelKey: "settings.heartbeatInterval", hintKey: "settings.heartbeatIntervalHint", keywords: ["心跳", "heartbeat", "间隔"] },
    // ── Appearance ──
    { id: "setting-theme", tab: "appearance", labelKey: "settings.theme", hintKey: "settings.themeHint", keywords: ["主题", "theme", "深色", "浅色", "dark", "light"] },
    { id: "setting-theme-color", tab: "appearance", labelKey: "settings.themeColor", keywords: ["主题色", "颜色", "color", "hue"] },
    { id: "setting-text-size", tab: "appearance", labelKey: "settings.textSize", hintKey: "settings.textSizeHint", keywords: ["字体", "字号", "text", "size", "字号"] },
    { id: "setting-workbench-background", tab: "appearance", labelKey: "settings.workbenchBackground", hintKey: "settings.workbenchBackgroundHint", keywords: ["背景", "background", "壁纸"] },
    { id: "setting-performance-mode", tab: "appearance", labelKey: "settings.performanceMode", hintKey: "settings.performanceModeHint", keywords: ["性能", "performance"] },
    { id: "setting-pulse-animation", tab: "appearance", labelKey: "settings.pulseAnimation", hintKey: "settings.pulseAnimationHint", keywords: ["动画", "animation", "pulse", "呼吸"] },
    // ── Voice ──
    { id: "setting-voice", tab: "voice", labelKey: "settings.voiceCapability", hintKey: "settings.voiceCapabilityHint", keywords: ["语音", "voice", "tts", "asr", "声音"] },
    // ── Plugin framework ──
    { id: "setting-plugin-registry", tab: "plugin-registry", labelKey: "settings.pluginRegistry", hintKey: "settings.pluginRegistryHint", keywords: ["插件中心", "plugin center", "插件注册表", "plugin registry", "插件包", "standalone plugin", "Skill", "技能", "MCP", "provider", "服务器", "服务", "CLI", "命令行", "模型", "model"] },
    { id: "setting-hooks", tab: "hooks", labelKey: "settings.hooks", hintKey: "settings.hooksSubtitle", keywords: ["自动触发", "Hook", "Hooks", "钩子", "生命周期", "PreToolUse", "PostToolUse", "TurnStart", "SessionStart"] },
    // ── Data ──
    { id: "setting-storage", tab: "data", labelKey: "settings.storageUsage", keywords: ["存储", "storage", "空间", "占用", "缓存"] },
    { id: "setting-paths", tab: "data", labelKey: "settings.pathInfo", keywords: ["路径", "path", "目录", "目录位置"] },
    { id: "setting-backup", tab: "data", labelKey: "settings.backup", hintKey: "settings.backupHint", keywords: ["备份", "backup", "恢复", "restore"] },
    { id: "setting-session-export", tab: "data", labelKey: "settings.sessionExport", hintKey: "settings.sessionExportHint", keywords: ["导出", "export", "会话"] },
    { id: "setting-clear-session", tab: "data", labelKey: "settings.clearSession", hintKey: "settings.clearSessionHint", keywords: ["清空", "clear", "清理"] },
    { id: "setting-redact-secrets", tab: "data", labelKey: "settings.redactSecrets", hintKey: "settings.redactSecretsHint", keywords: ["密钥", "secret", "敏感", "redact"] },
    { id: "setting-reset-app-data", tab: "data", labelKey: "settings.resetAppData", hintKey: "settings.resetAppDataHint", keywords: ["重置", "reset", "恢复出厂"] },
    // ── Budget ──
    { id: "setting-budget", tab: "budget", labelKey: "settings.budgetConfig", keywords: ["预算", "budget", "限额", "limit"] },
    { id: "setting-codex-quota", tab: "usage", labelKey: "settings.codexQuota", keywords: ["codex", "quota", "配额"] },
  ],
});
