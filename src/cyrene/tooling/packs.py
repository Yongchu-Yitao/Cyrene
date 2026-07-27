"""Stable tool-pack declarations and concrete capability ownership."""

from __future__ import annotations

from cyrene.tooling.types import PackSpec

PACKS = (
    PackSpec("code", "code_tools", "Code analysis, Git, persistent shells, and Claude Code helpers.", ("code.",), 100),
    PackSpec("browser", "browser_tools", "Persistent browser navigation, inspection, interaction, uploads, and takeover.", ("browser.",), 200),
    PackSpec("desktop", "desktop_tools", "Desktop application discovery and interaction through App Use.", ("desktop.",), 300),
    PackSpec("memory", "memory_tools", "Conversation, short-term, and project memory retrieval and maintenance.", ("memory.",), 400),
    PackSpec("knowledge", "knowledge_tools", "Project knowledge documents and literature-library search and metadata.", ("knowledge.",), 500),
    PackSpec("task", "task_tools", "Scheduled tasks plus durable task goals and plan state.", ("task.",), 600),
    PackSpec("entity", "entity_tools", "Track, query, update, list, and delete durable entities.", ("entity.",), 700),
    PackSpec("map", "map_tools", "Create map pins and connect locations.", ("map.",), 800),
    PackSpec("subagent", "subagent_tools", "Spawn, inspect, and communicate with subagents.", ("subagent.",), 900),
    PackSpec("delivery", "delivery_tools", "Progress updates, notifications, messages, and file delivery.", ("delivery.",), 1000),
    PackSpec("skill", "skill_tools", "Discover, install, remove, inspect, and run Agent Skills.", ("skill.",), 1100),
    PackSpec("remote", "remote_tools", "Operate paired Cyrene devices explicitly selected in the current chat.", ("remote.",), 1200),
    PackSpec("integration", "integration_tools", "Dynamically connected MCP and external integration capabilities.", ("integration.",), 1300),
)

CAPABILITY_BINDINGS: dict[str, tuple[tuple[str, str], ...]] = {
    "code_tools": (
        ("code.prompt_claude_code", "PromptClaudeCode"),
        ("code.check_claude_code", "CheckClaudeCode"),
        ("code.start_claude_code", "StartClaudeCode"),
        ("code.shell.start", "StartShell"),
        ("code.shell.send", "SendShell"),
        ("code.shell.list", "ListShells"),
        ("code.shell.close", "CloseShell"),
        ("code.lint", "LintCode"),
        ("code.format", "FormatCode"),
        ("code.review", "CodeReview"),
        ("code.git.status", "GitStatus"),
        ("code.git.diff", "GitDiff"),
        ("code.git.log", "GitLog"),
        ("code.git.commit", "GitCommit"),
        ("code.git.branch", "GitBranch"),
        ("code.index", "IndexCodebase"),
        ("code.search_symbol", "SearchSymbol"),
        ("code.find_references", "FindReferences"),
        ("code.file_symbols", "GetFileSymbols"),
    ),
    "browser_tools": (
        ("browser.navigate", "browser_navigate"),
        ("browser.snapshot", "browser_snapshot"),
        ("browser.screenshot", "browser_screenshot"),
        ("browser.click", "browser_click"),
        ("browser.click_ref", "browser_click_ref"),
        ("browser.click_text", "browser_click_text"),
        ("browser.click_at", "browser_click_at"),
        ("browser.type", "browser_type"),
        ("browser.type_ref", "browser_type_ref"),
        ("browser.upload_files", "browser_upload_files"),
        ("browser.wait", "browser_wait"),
        ("browser.network_log", "browser_network_log"),
        ("browser.tab.list", "browser_tab_list"),
        ("browser.tab.new", "browser_tab_new"),
        ("browser.tab.select", "browser_tab_select"),
        ("browser.tab.close", "browser_tab_close"),
        ("browser.scroll", "browser_scroll"),
        ("browser.user_events", "browser_user_events"),
        ("browser.request_takeover", "browser_request_takeover"),
    ),
    "desktop_tools": (("desktop.use", "app_use"),),
    "memory_tools": (
        ("memory.list", "ListMemories"),
        ("memory.recall", "RecallMemory"),
        ("memory.recall_conversation", "RecallConversation"),
        ("memory.short_term.retire", "retire_short_term_memory"),
        ("memory.project.search", "search_project_memory"),
        ("memory.project.save", "save_project_memory"),
        ("memory.project.retire", "retire_project_memory"),
    ),
    "knowledge_tools": (
        ("knowledge.list_documents", "ListKnowledgeDocuments"),
        ("knowledge.search", "SearchKnowledge"),
        ("knowledge.library.list", "ListLibraryItems"),
        ("knowledge.library.search", "SearchLibrary"),
        ("knowledge.library.update_metadata", "UpdateLibraryMetadata"),
    ),
    "task_tools": (
        ("task.schedule", "schedule_task"),
        ("task.list", "list_tasks"),
        ("task.pause", "pause_task"),
        ("task.resume", "resume_task"),
        ("task.cancel", "cancel_task"),
        ("task.goal.set", "set_task_goal"),
        ("task.plan.update", "update_task_plan"),
    ),
    "entity_tools": (
        ("entity.track", "track_entity"),
        ("entity.update", "update_entity"),
        ("entity.list", "list_entities"),
        ("entity.query", "query_entities"),
        ("entity.delete", "delete_entity"),
    ),
    "map_tools": (
        ("map.pin_location", "pin_location"),
        ("map.connect_pins", "connect_pins"),
    ),
    "subagent_tools": (
        ("subagent.spawn", "spawn_subagent"),
        ("subagent.send_message", "send_agent_message"),
        ("subagent.broadcast", "broadcast_agent_message"),
        ("subagent.query_round", "query_round"),
    ),
    "delivery_tools": (
        ("delivery.send_telegram", "send_telegram"),
        ("delivery.send_message_to_user", "send_message_to_user"),
        ("delivery.send_file", "send_file"),
        ("delivery.send_wechat_file", "send_wechat_file"),
        ("delivery.send_notification", "send_notification"),
    ),
    "skill_tools": (
        ("skill.install", "InstallSkill"),
        ("skill.uninstall", "UninstallSkill"),
        ("skill.list", "ListSkills"),
        ("skill.get_learned", "GetLearnedSkill"),
        ("skill.run_learned", "RunLearnedSkill"),
    ),
    "remote_tools": (
        ("remote.devices.list", "ListRemoteDevices"),
        ("remote.status", "RemoteCyreneStatus"),
        ("remote.harness", "RemoteHarness"),
        ("remote.action", "RemoteCyreneAction"),
        ("remote.run", "RunRemoteCyrene"),
    ),
    "integration_tools": (),
}

PACK_BY_WIRE_NAME = {pack.wire_name: pack for pack in PACKS}
MODULE_TOOL_NAMES = tuple(pack.wire_name for pack in PACKS)
WIRE_NAME_BY_PACK_ID = {
    pack.pack_id: pack.wire_name
    for pack in PACKS
}
WIRE_NAME_BY_CONCRETE_TOOL = {
    concrete_name: wire_name
    for wire_name, bindings in CAPABILITY_BINDINGS.items()
    for _capability_id, concrete_name in bindings
}
