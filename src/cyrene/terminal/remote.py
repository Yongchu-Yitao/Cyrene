"""Managed SSH launch construction for durable remote terminals."""

from __future__ import annotations

import base64
import hashlib
import re
import shlex
import shutil
from dataclasses import dataclass

from .shell_integration import remote_shell_integration_files


_SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,255}$")
_TMUX_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True, slots=True)
class ManagedSshLaunch:
    argv: list[str]
    target: str
    remote_cwd: str
    tmux_session: str
    bundle_version: str


_REMOTE_LAUNCHER = r'''#!/bin/sh
cyrene_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 126
shell_path=${SHELL:-}
if [ -z "$shell_path" ] && command -v getent >/dev/null 2>&1; then
  shell_path=$(getent passwd "$(id -u)" 2>/dev/null | awk -F: '{print $7}')
fi
if [ -z "$shell_path" ]; then
  shell_path=/bin/sh
fi
shell_name=${shell_path##*/}
export CYRENE_SHELL_INTEGRATION=1
export CYRENE_SHELL_INTEGRATION_DIR="$cyrene_dir"
case "$shell_name" in
  bash)
    export CYRENE_SHELL_INTEGRATION_SCRIPT="$cyrene_dir/cyrene.bash.integration"
    export BASH_ENV="$CYRENE_SHELL_INTEGRATION_SCRIPT"
    exec "$shell_path" --rcfile "$cyrene_dir/cyrene.bash" -i
    ;;
  zsh)
    export CYRENE_ORIGINAL_ZDOTDIR=${ZDOTDIR:-$HOME}
    export CYRENE_INTEGRATION_ZDOTDIR="$cyrene_dir/zsh"
    export CYRENE_SHELL_INTEGRATION_SCRIPT="$cyrene_dir/cyrene.zsh.integration"
    export ZDOTDIR="$CYRENE_INTEGRATION_ZDOTDIR"
    exec "$shell_path" -i
    ;;
  fish)
    export CYRENE_SHELL_INTEGRATION_SCRIPT="$cyrene_dir/cyrene.fish"
    exec "$shell_path" -C "source \"$CYRENE_SHELL_INTEGRATION_SCRIPT\"" -i
    ;;
  *)
    exec "$shell_path" -i
    ;;
esac
'''


def _normalized_target(value: str) -> str:
    target = str(value or "").strip()
    if not _SSH_TARGET_RE.fullmatch(target) or target.startswith("-"):
        raise ValueError("ssh target must be a host or user@host without options")
    return target


def _normalized_remote_cwd(value: str) -> str:
    cwd = str(value or "").strip()
    if any(character in cwd for character in ("\x00", "\r", "\n")):
        raise ValueError("remote cwd contains invalid control characters")
    if cwd and not (cwd.startswith("/") or cwd == "~" or cwd.startswith("~/")):
        raise ValueError("remote cwd must be an absolute path or start with ~")
    return cwd


def _normalized_tmux_session(value: str) -> str:
    session = str(value or "").strip()
    if session and not _TMUX_SESSION_RE.fullmatch(session):
        raise ValueError("tmux session may contain only letters, numbers, _ and -")
    return session


def _bundle_files() -> dict[str, str]:
    source = remote_shell_integration_files()
    return {
        "launch": _REMOTE_LAUNCHER,
        "cyrene.bash": source["cyrene.bash"],
        "cyrene.bash.integration": source["cyrene.bash.integration"],
        "cyrene.zsh.integration": source["cyrene.zsh.integration"],
        "zsh/.zshenv": source["cyrene.zshenv"],
        "zsh/.zshrc": source["cyrene.zshrc"],
        "cyrene.fish": source["cyrene.fish"],
    }


def _remote_command(
    *, target: str, remote_cwd: str, tmux_session: str,
) -> tuple[str, str]:
    files = _bundle_files()
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode("utf-8") + b"\0" + content.encode("utf-8") + b"\0")
    version = digest.hexdigest()[:16]
    directory = f'$HOME/.cache/cyrene/shell-integration/{version}'
    lines = [
        "umask 077",
        f'cyrene_dir="{directory}"',
        'cyrene_marker="$cyrene_dir/.complete"',
        'if [ ! -f "$cyrene_marker" ]; then',
        '  mkdir -p "$cyrene_dir/zsh" || exit 126',
    ]
    for path, content in sorted(files.items()):
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        lines.append(
            f"  printf '%s' {shlex.quote(encoded)} | base64 -d > "
            f'"$cyrene_dir/{path}" || exit 126'
        )
    lines.extend([
        '  chmod 700 "$cyrene_dir/launch" || exit 126',
        '  chmod 600 "$cyrene_dir/cyrene.bash" '
        '"$cyrene_dir/cyrene.bash.integration" '
        '"$cyrene_dir/cyrene.zsh.integration" '
        '"$cyrene_dir/zsh/.zshenv" "$cyrene_dir/zsh/.zshrc" '
        '"$cyrene_dir/cyrene.fish" || exit 126',
        '  : > "$cyrene_marker"',
        "fi",
        'export CYRENE_TERMINAL_CONTEXT=ssh',
        f"export CYRENE_SSH_TARGET={shlex.quote(target)}",
        'export CYRENE_REMOTE_SHELL_LAUNCHER="$cyrene_dir/launch"',
        r'''cyrene_emit_property() { printf '\033]133;P;%s=%s\007' "$1" "$2"; }''',
    ])
    if remote_cwd:
        lines.append(f"cd -- {shlex.quote(remote_cwd)} || exit 72")
    if tmux_session:
        quoted_session = shlex.quote(tmux_session)
        lines.extend([
            "command -v tmux >/dev/null 2>&1 || exit 127",
            f"if tmux has-session -t {quoted_session} 2>/dev/null; then",
            f'  tmux set-option -t {quoted_session} default-command "$cyrene_dir/launch" '
            ">/dev/null 2>&1 || true",
            "fi",
            "cyrene_emit_property Context ssh",
            f"cyrene_emit_property ProfileId {shlex.quote(target)}",
            "cyrene_emit_property Lifecycle connected",
            f'tmux new-session -A -s {quoted_session} "$cyrene_dir/launch"',
            "cyrene_status=$?",
            f"if tmux has-session -t {quoted_session} 2>/dev/null; then",
            "  cyrene_emit_property Lifecycle tmux_detached",
            "else",
            "  cyrene_emit_property Lifecycle tmux_ended",
            "fi",
            "exit \"$cyrene_status\"",
        ])
    else:
        lines.extend([
            "cyrene_emit_property Context ssh",
            f"cyrene_emit_property ProfileId {shlex.quote(target)}",
            "cyrene_emit_property Lifecycle connected",
            '"$cyrene_dir/launch"',
            "cyrene_status=$?",
            "cyrene_emit_property Lifecycle user_exit",
            "exit \"$cyrene_status\"",
        ])
    script = "\n".join(lines)
    return "exec /bin/sh -c " + shlex.quote(script), version


def build_managed_ssh_launch(
    *, target: str, remote_cwd: str = "", tmux_session: str = "",
    ssh_executable: str = "",
) -> ManagedSshLaunch:
    """Build a single-auth OpenSSH launch with a cached remote bootstrap."""
    normalized_target = _normalized_target(target)
    normalized_cwd = _normalized_remote_cwd(remote_cwd)
    normalized_tmux = _normalized_tmux_session(tmux_session)
    executable = str(ssh_executable or shutil.which("ssh") or "").strip()
    if not executable:
        raise RuntimeError("OpenSSH client is not installed")
    command, version = _remote_command(
        target=normalized_target,
        remote_cwd=normalized_cwd,
        tmux_session=normalized_tmux,
    )
    return ManagedSshLaunch(
        argv=[
            executable,
            "-tt",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            normalized_target,
            command,
        ],
        target=normalized_target,
        remote_cwd=normalized_cwd,
        tmux_session=normalized_tmux,
        bundle_version=version,
    )


__all__ = ["ManagedSshLaunch", "build_managed_ssh_launch"]
