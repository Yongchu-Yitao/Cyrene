"""Curated catalog and detection declarations for the Extension Center."""

from __future__ import annotations

RECOMMENDED_ORDER = ("python", "uv", "tex", "node", "github-cli", "bun")

TOOLCHAINS: dict[str, dict] = {
    "python": {
        "name": "Python", "kind": "toolchain", "manager": "uv", "tool": "python",
        "recommended_version": "3.14", "executables": ("python3", "python"),
        "version_args": ("--version",), "description": "Python runtime for scripts, Skills, and local tools.",
        "recommended": True,
    },
    "node": {
        "name": "Node.js", "kind": "toolchain", "manager": "mise", "tool": "node",
        "version": "lts", "executables": ("node",), "version_args": ("--version",),
        "description": "JavaScript runtime and npm ecosystem.", "recommended": True,
    },
    "bun": {
        "name": "Bun", "kind": "toolchain", "manager": "mise", "tool": "bun",
        "version": "latest", "executables": ("bun",), "version_args": ("--version",),
        "description": "Fast JavaScript runtime, package manager, and test runner.", "recommended": True,
    },
    "go": {
        "name": "Go", "kind": "toolchain", "manager": "mise", "tool": "go",
        "version": "latest", "executables": ("go",), "version_args": ("version",),
        "description": "Go compiler and tooling.",
    },
    "java": {
        "name": "Java", "kind": "toolchain", "manager": "mise", "tool": "java",
        "version": "lts", "executables": ("java",), "version_args": ("-version",),
        "description": "Java runtime and development kit.",
    },
    "rust": {
        "name": "Rust", "kind": "toolchain", "manager": "mise", "tool": "rust",
        "version": "stable", "executables": ("rustc",), "version_args": ("--version",),
        "description": "Rust compiler and Cargo toolchain.",
    },
    "deno": {
        "name": "Deno", "kind": "toolchain", "manager": "mise", "tool": "deno",
        "version": "latest", "executables": ("deno",), "version_args": ("--version",),
        "description": "Secure JavaScript and TypeScript runtime.",
    },
    "tex": {
        "name": "TeX", "kind": "toolchain", "manager": "tinytex", "tool": "tex",
        "version": "latest", "executables": ("pdflatex", "xelatex", "lualatex"),
        "version_args": ("--version",), "description": "TeX Live environment for compiling LaTeX and PDF documents.",
        "recommended": True,
    },
}

CURATED_CLIS: dict[str, dict] = {
    "github-cli": {
        "name": "GitHub CLI", "kind": "cli", "manager": "mise", "tool": "github-cli",
        "ref": "github:cli/cli", "version": "latest", "executables": ("gh",),
        "version_args": ("--version",), "description": "Work with GitHub repositories, issues, and pull requests.",
        "recommended": True, "publisher": "GitHub", "risk": "medium",
    },
    "ripgrep": {
        "name": "ripgrep", "kind": "cli", "manager": "mise", "tool": "ripgrep",
        "ref": "github:BurntSushi/ripgrep", "version": "latest", "executables": ("rg",),
        "version_args": ("--version",), "description": "Fast recursive text search.", "publisher": "BurntSushi", "risk": "medium",
    },
    "jq": {
        "name": "jq", "kind": "cli", "manager": "mise", "tool": "jq",
        "ref": "aqua:jqlang/jq", "version": "latest", "executables": ("jq",),
        "version_args": ("--version",), "description": "Command-line JSON processor.", "publisher": "jqlang", "risk": "medium",
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
