# Cyrene architecture image generation

Generated with the built-in `image_gen` tool. Reference: the previous `cyrene-hero.png` architecture image. Final asset: `cyrene-hero.png`.

## Generation prompt

Use case: infographic-diagram.
Redraw the supplied Cyrene architecture image for the CURRENT implementation, replacing obsolete architectural content entirely while retaining its clean white background, faint dotted grid, teal/blue/violet thin outlines, rounded cards, restrained shadows and elegant technical diagram aesthetic. Produce a polished wide landscape README hero architecture diagram at high resolution, with exceptionally legible English type, generous whitespace, coherent noncrossing connectors, simple line icons. No decorative illegible microtext.

Title top left: "Cyrene"
Subtitle: "One continuous Agent runtime, assembled from plugins"

Diagram layout and exact labels:
LEFT column heading "Interfaces", with 3 stacked cards: "Desktop / Web UI", "CLI", "Channels / Webhooks". All feed into a card "Workbench" with subtitle "Projects · Conversations", then a rightward arrow into the central runtime.
CENTER large teal outlined rounded panel titled "Continuous Agent Runtime". Inside, a central softly luminous teal circle labelled "Cyrene Agent". Under the circle put "Model ↔ Tools ↔ Results" and a single looping arrow around this central sequence, representing ONE continuous run. Two foundation cards inside bottom of runtime: "ContextTree" / "History · Compaction · Recovery" and "Hooks" / "Lifecycle · Permissions". A small footer inside runtime reads "Kernel: Bash · Read · Write · toolbox".
ABOVE runtime a wide rounded panel labelled "Context Plugins", with 3 chips: "System Prompt + SOUL.md", "Memory + Project Context", "Workspace + MCP + Skills". One downward arrow from this panel into ContextTree/runtime labelled "compose". This panel must not be confused with tool execution.
ABOVE RIGHT, separate card "Model Providers", with subtitle "Plugin-selected inference", linked bidirectionally to the central runtime.
RIGHT of runtime a large violet rounded panel "Capability Plugins", directly linked bidirectionally to the runtime, with connection label "Direct tools or toolbox discovery". Inside this panel four compact rows: "MCP · Skills · Code / Git", "Browser · SimpleXNG Search", "Knowledge · Entities · Office", "Goals · Plans · Schedules". Footer inside this panel: "toolbox: list → describe → invoke".
BELOW runtime/right, a blue rounded card labelled "Parallel Subagents", subtitle "Independent loops · Durable inbox", connected bidirectionally DIRECTLY to the runtime by a blue line. Subagents are a branch, never an intermediary between the main agent and capabilities.
BOTTOM full width foundation panel titled "Local Persistence", with subtitle "ContextTree SQLite · App database · Plugin data · Workspace files". Dashed links from runtime and Workbench to persistence.
BOTTOM RIGHT separate slim panel "Observability" / "Context Inspector · Run Timeline · Logs" with a dashed line from runtime to this panel.
Keep diagram balanced and readable, approximately 2:1 landscape ratio. Use only the specified labels, correct spelling. Old Phase 1 / Phase 2, Decision router, SearXNG and SVG Timeline must NOT appear. Do not imply storage is an agent tool. No arrows from tools through subagents. Save a professional finished architecture infographic.

## Final correction prompt

Replace the dangling Model Providers arrow with a continuous bidirectional teal elbow connector touching the Model Providers card and the Continuous Agent Runtime panel. Preserve all other text, layout, colors, and content.
