"""Curated catalog and detection declarations for Plugin-managed environments."""

from __future__ import annotations

RECOMMENDED_ORDER = ("python", "uv", "tex", "node", "github-cli", "bun")

TOOLCHAINS: dict[str, dict] = {
    "python": {
        "name": "Python", "kind": "toolchain", "manager": "uv", "tool": "python",
        "icon": "python",
        "recommended_version": "3.14", "executables": ("python3", "python"),
        "version_args": ("--version",), "description": "Python runtime for scripts, Skills, and local tools.",
        "description_zh": "用于脚本、技能和本地工具的 Python 运行时。",
        "recommended": True,
    },
    "node": {
        "name": "Node.js", "kind": "toolchain", "manager": "mise", "tool": "node",
        "icon": "nodejs",
        "version": "lts", "executables": ("node",), "version_args": ("--version",),
        "description": "JavaScript runtime and npm ecosystem.",
        "description_zh": "JavaScript 运行时与 npm 生态系统。", "recommended": True,
    },
    "bun": {
        "name": "Bun", "kind": "toolchain", "manager": "mise", "tool": "bun",
        "icon": "bun",
        "version": "latest", "executables": ("bun",), "version_args": ("--version",),
        "description": "Fast JavaScript runtime, package manager, and test runner.",
        "description_zh": "快速的 JavaScript 运行时、包管理器和测试运行器。", "recommended": True,
    },
    "go": {
        "name": "Go", "kind": "toolchain", "manager": "mise", "tool": "go",
        "icon": "go",
        "version": "latest", "executables": ("go",), "version_args": ("version",),
        "description": "Go compiler and tooling.",
        "description_zh": "Go 编译器与工具链。",
    },
    "java": {
        "name": "Java", "kind": "toolchain", "manager": "mise", "tool": "java",
        "icon": "java",
        "version": "lts", "executables": ("java",), "version_args": ("-version",),
        "description": "Java runtime and development kit.",
        "description_zh": "Java 运行时与开发工具包。",
    },
    "rust": {
        "name": "Rust", "kind": "toolchain", "manager": "mise", "tool": "rust",
        "icon": "rust",
        "version": "stable", "executables": ("rustc",), "version_args": ("--version",),
        "description": "Rust compiler and Cargo toolchain.",
        "description_zh": "Rust 编译器与 Cargo 工具链。",
    },
    "deno": {
        "name": "Deno", "kind": "toolchain", "manager": "mise", "tool": "deno",
        "icon": "deno",
        "version": "latest", "executables": ("deno",), "version_args": ("--version",),
        "description": "Secure JavaScript and TypeScript runtime.",
        "description_zh": "安全的 JavaScript 与 TypeScript 运行时。",
    },
    "tex": {
        "name": "TeX", "kind": "toolchain", "manager": "tinytex", "tool": "tex",
        "icon": "tex",
        "version": "latest", "executables": ("pdflatex", "xelatex", "lualatex"),
        "version_args": ("--version",), "description": "TeX Live environment for compiling LaTeX and PDF documents.",
        "description_zh": "用于编译 LaTeX 与 PDF 文档的 TeX Live 环境。",
        "recommended": True,
    },
}

CURATED_CLIS: dict[str, dict] = {
    "github-cli": {
        "name": "GitHub CLI", "kind": "cli", "manager": "mise", "tool": "github-cli",
        "icon": "github",
        "ref": "github:cli/cli", "version": "latest", "executables": ("gh",),
        "version_args": ("--version",), "description": "Work with GitHub repositories, issues, and pull requests.",
        "description_zh": "处理 GitHub 仓库、Issue 和拉取请求。",
        "recommended": True, "publisher": "GitHub", "risk": "medium",
    },
    "ripgrep": {
        "name": "ripgrep", "kind": "cli", "manager": "mise", "tool": "ripgrep",
        "icon": "ripgrep",
        "ref": "github:BurntSushi/ripgrep", "version": "latest", "executables": ("rg",),
        "version_args": ("--version",), "description": "Fast recursive text search.",
        "description_zh": "快速递归文本搜索。", "publisher": "BurntSushi", "risk": "medium",
    },
    "jq": {
        "name": "jq", "kind": "cli", "manager": "mise", "tool": "jq",
        "icon": "jq",
        "ref": "aqua:jqlang/jq", "version": "latest", "executables": ("jq",),
        "version_args": ("--version",), "description": "Command-line JSON processor.",
        "description_zh": "命令行 JSON 处理器。", "publisher": "jqlang", "risk": "medium",
    },
}

UV_VERSION = "0.11.28"
MISE_VERSION = "2026.8.5"

DEFAULT_SOURCE_SETTINGS = {
    "auto_mirror": True,
    "network_mode": "auto",
    "github_mirror": "",
    "npm_registry": "",
    "pip_index_url": "",
    "github_token": "",
    "verify_signatures": True,
    "mcp_registry_url": "https://registry.modelcontextprotocol.io",
    "skill_catalog_url": "",
}

ALLOWED_MISE_BACKENDS = frozenset({"core", "aqua", "github", "gitlab", "npm", "pipx", "gem", "cargo", "go"})
HIGH_RISK_MISE_BACKENDS = frozenset({"npm", "pipx", "gem", "cargo", "go"})


# ---------------------------------------------------------------------------
# External Agent catalog (phase 1)
# ---------------------------------------------------------------------------
# Declarative, Cyrene-reviewed profiles only. Recommended distributions are
# pinned to exact versions from the official ACP registry; binary archives
# include the publisher digest and npm packages include the exact version.
AGENT_MANIFEST_API = "cyrene.agent/v1"
SUPPORTED_AGENT_DRIVERS = frozenset({"acp_stdio"})

# Conservative declarative capability profiles, following the handoff
# capability schema (supported/unsupported/unknown/degraded).  These are
# placeholder profiles that a later protocol handshake or probe may refine.
_AGENT_BASE_CAPABILITIES: dict[str, dict] = {
    "session": {"load": "supported", "fork": "unknown", "close": "supported"},
    "input": {"text": "supported", "image": "unknown", "file": "unknown", "audio": "unknown"},
    "output": {"streaming": "supported", "reasoning": "unknown", "toolLifecycle": "supported", "artifacts": "unknown", "diff": "unknown"},
    "interaction": {"permission": "agent_defined", "elicitation": "unknown", "steer": "unknown", "cancel": "supported"},
    "model": {"agentManaged": "supported", "cyreneManaged": ["openai_chat", "openai_responses"], "switchDuringSession": "unsupported", "reasoningEffort": "supported"},
}

RECOMMENDED_AGENTS: dict[str, dict] = {
    "opencode": {
        "agentId": "opencode", "name": "OpenCode", "kind": "agent",
        "displayName": "OpenCode", "publisher": "Anomaly", "recommended": True,
        "description": "Open-source coding agent driven over ACP stdio.",
        "description_zh": "通过 ACP stdio 驱动的开源编程智能体。",
        "driver": "acp_stdio", "protocol_version": 1, "command": "opencode",
        "recommended_version": "1.18.18", "version_source": "acp_registry",
        "default_model_access": "cyrene_managed", "risk": "medium",
        "capabilities": _AGENT_BASE_CAPABILITIES,
        "distribution": {
            "kind": "binary",
            "platforms": {
                "macos-arm64": {"url": "https://github.com/anomalyco/opencode/releases/download/v1.18.18/opencode-darwin-arm64.zip", "sha256": "7d668bf26496fec8686d4e51ebb1ac2bd2e393f0c1620aa696c4c242a9e5806a", "executable": "opencode"},
                "macos-x64": {"url": "https://github.com/anomalyco/opencode/releases/download/v1.18.18/opencode-darwin-x64.zip", "sha256": "9581bd7683a7528456179fb11e3377d9ef568e10a935611a2c6722e349454d83", "executable": "opencode"},
                "linux-arm64": {"url": "https://github.com/anomalyco/opencode/releases/download/v1.18.18/opencode-linux-arm64.tar.gz", "sha256": "dcb1b5ec5687b43f87749560021f9203f3809e0ce5ae44ff9be8ae17083fe4ba", "executable": "opencode"},
                "linux-x64": {"url": "https://github.com/anomalyco/opencode/releases/download/v1.18.18/opencode-linux-x64.tar.gz", "sha256": "0cddc222418b8553669905a8980c0cda7088f00da24d83d6ac76b01c9fdb2aaf", "executable": "opencode"},
                "windows-arm64": {"url": "https://github.com/anomalyco/opencode/releases/download/v1.18.18/opencode-windows-arm64.zip", "sha256": "0d34d837ea3b5e10349d8550318083040a8b4c061d3faaa4eabd339984aa49b0", "executable": "opencode.exe"},
                "windows-x64": {"url": "https://github.com/anomalyco/opencode/releases/download/v1.18.18/opencode-windows-x64.zip", "sha256": "c6d265376fdb93164013671b0cf402410184f73c34fc15d82d40a16a745b15f4", "executable": "opencode.exe"},
            },
        },
        "repository": "https://github.com/anomalyco/opencode",
    },
    "codex-acp": {
        "agentId": "codex-acp", "name": "Codex ACP", "kind": "agent",
        "displayName": "Codex ACP", "publisher": "Agent Client Protocol", "recommended": True,
        "description": "Codex CLI exposed through the Agent Client Protocol.",
        "description_zh": "通过 Agent Client Protocol 提供的 Codex CLI。",
        "driver": "acp_stdio", "protocol_version": 1, "command": "codex-acp",
        "recommended_version": "1.2.0", "version_source": "acp_registry",
        "default_model_access": "agent_managed", "risk": "medium",
        "capabilities": _AGENT_BASE_CAPABILITIES,
        "distribution": {"kind": "npm", "package": "@agentclientprotocol/codex-acp@1.2.0"},
        "repository": "https://github.com/agentclientprotocol/codex-acp",
    },
    "pi-acp": {
        "agentId": "pi-acp", "name": "Pi ACP", "kind": "agent",
        "displayName": "Pi ACP", "publisher": "Sergii Kozak", "recommended": True,
        "description": "Pi coding agent exposed through the Agent Client Protocol.",
        "description_zh": "通过 Agent Client Protocol 提供的 Pi 编程智能体。",
        "driver": "acp_stdio", "protocol_version": 1, "command": "pi-acp",
        "recommended_version": "0.0.33", "version_source": "acp_registry",
        "default_model_access": "agent_managed", "risk": "medium",
        "capabilities": _AGENT_BASE_CAPABILITIES,
        "distribution": {"kind": "npm", "package": "pi-acp@0.0.33"},
        # The pi-acp adapter spawns the ``pi`` executable when a session/new
        # request arrives, but the adapter package does not bundle it. Install
        # the pinned runtime alongside the adapter (same staging prefix) so the
        # agent works without relying on a global npm install or shell PATH.
        "dependency": {"kind": "npm", "package": "@earendil-works/pi-coding-agent@0.74.2", "bin": "pi"},
        "repository": "https://github.com/svkozak/pi-acp",
    },
}

RECOMMENDED_AGENT_ORDER = ("opencode", "codex-acp", "pi-acp")
