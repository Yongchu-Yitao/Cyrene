"""Shell integration launch helpers and incremental OSC metadata parsing.

Interactive shells emit standard OSC 7, OSC 2, and OSC 133 markers.  The
parser observes those markers without modifying the PTY byte stream, so the
same bytes remain authoritative for the renderer and durable scrollback.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


_BASH_SCRIPT = r'''# Cyrene shell integration launcher. Loaded instead of the user's bashrc.
if [[ -r "${HOME}/.bashrc" ]]; then
  source "${HOME}/.bashrc"
fi
__cyrene_launcher_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${__cyrene_launcher_dir}/cyrene.bash.integration"
unset __cyrene_launcher_dir
'''

_BASH_INTEGRATION_SCRIPT = r'''# Cyrene shell integration. Safe to source in child shells.
if [[ -z "${__CYRENE_SHELL_INTEGRATION_LOADED:-}" ]]; then
  __CYRENE_SHELL_INTEGRATION_LOADED=1
  __cyrene_emit_osc() {
    if [[ -n "${TMUX:-}" ]]; then
      printf '\033Ptmux;\033\033]%s\007\033\\' "$1"
    else
      printf '\033]%s\007' "$1"
    fi
  }
  if [[ -n "${TMUX_PANE:-}" ]]; then
    command tmux set-option -p -t "${TMUX_PANE}" allow-passthrough on >/dev/null 2>&1 || true
    if [[ -n "${CYRENE_REMOTE_SHELL_LAUNCHER:-}" ]]; then
      command tmux set-option default-command "${CYRENE_REMOTE_SHELL_LAUNCHER}" >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "${TMUX:-}" ]]; then
    __cyrene_prompt_marker_b=$'\033Ptmux;\033\033]133;B\007\033\\'
    __cyrene_prompt_marker_c=$'\033Ptmux;\033\033]133;C\007\033\\'
  else
    __cyrene_prompt_marker_b=$'\033]133;B\007'
    __cyrene_prompt_marker_c=$'\033]133;C\007'
  fi
  __cyrene_integration_level=basic
  if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )); then
    __cyrene_integration_level=full
  fi
  __cyrene_original_prompt_command="${PROMPT_COMMAND-}"
  if declare -p PROMPT_COMMAND 2>/dev/null | grep -q '^declare -a'; then
    __cyrene_original_prompt_command="$(IFS=';'; printf '%s' "${PROMPT_COMMAND[*]}")"
  fi
  __cyrene_restore_status() { return "$1"; }
  __cyrene_prompt_command() {
    local __cyrene_status=$?
    if [[ -n "${__cyrene_original_prompt_command}" ]]; then
      __cyrene_restore_status "${__cyrene_status}"
      eval "${__cyrene_original_prompt_command}"
    fi
    local __cyrene_path="${PWD//\%/%25}"
    __cyrene_path="${__cyrene_path// /%20}"
    __cyrene_emit_osc "133;D;${__cyrene_status}"
    __cyrene_emit_osc "7;file://${HOSTNAME:-localhost}${__cyrene_path}"
    __cyrene_emit_osc "2;${PWD}"
    __cyrene_emit_osc "133;P;Integration=${__cyrene_integration_level}"
    __cyrene_emit_osc "133;A"
  }
  PROMPT_COMMAND=__cyrene_prompt_command
  PS1="${PS1}"'\['"${__cyrene_prompt_marker_b}"'\]'
  if [[ "${__cyrene_integration_level}" == full ]]; then
    PS0="${PS0-}"'\['"${__cyrene_prompt_marker_c}"'\]'
  fi
  export PROMPT_COMMAND PS0 PS1
  export __cyrene_integration_level __cyrene_original_prompt_command
  export -f __cyrene_emit_osc __cyrene_restore_status __cyrene_prompt_command
  if [[ -n "${CYRENE_CLAUDE_PLUGIN_DIR:-}" ]]; then
    claude() {
      command claude --plugin-dir "${CYRENE_CLAUDE_PLUGIN_DIR}" "$@"
    }
  fi
fi
'''

_ZSH_ENV_SCRIPT = r'''# Keep normal user startup files while routing zsh through Cyrene.
if [[ -n "${CYRENE_ORIGINAL_ZDOTDIR:-}" && -r "${CYRENE_ORIGINAL_ZDOTDIR}/.zshenv" ]]; then
  source "${CYRENE_ORIGINAL_ZDOTDIR}/.zshenv"
fi
export ZDOTDIR="${CYRENE_INTEGRATION_ZDOTDIR}"
'''

_ZSH_RC_SCRIPT = r'''# Cyrene shell integration launcher. Loaded instead of the user's zshrc.
if [[ -n "${CYRENE_ORIGINAL_ZDOTDIR:-}" && -r "${CYRENE_ORIGINAL_ZDOTDIR}/.zshrc" ]]; then
  source "${CYRENE_ORIGINAL_ZDOTDIR}/.zshrc"
fi
source "${CYRENE_SHELL_INTEGRATION_SCRIPT}"
'''

_ZSH_INTEGRATION_SCRIPT = r'''# Cyrene shell integration. Safe to source in child shells.
if [[ -z "${__CYRENE_SHELL_INTEGRATION_LOADED:-}" ]]; then
  typeset -g __CYRENE_SHELL_INTEGRATION_LOADED=1
  __cyrene_emit_osc() {
    if [[ -n "${TMUX:-}" ]]; then
      printf '\033Ptmux;\033\033]%s\007\033\\' "$1"
    else
      printf '\033]%s\007' "$1"
    fi
  }
  if [[ -n "${TMUX_PANE:-}" ]]; then
    command tmux set-option -p -t "${TMUX_PANE}" allow-passthrough on >/dev/null 2>&1 || true
    if [[ -n "${CYRENE_REMOTE_SHELL_LAUNCHER:-}" ]]; then
      command tmux set-option default-command "${CYRENE_REMOTE_SHELL_LAUNCHER}" >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "${TMUX:-}" ]]; then
    __cyrene_prompt_marker_b=$'\033Ptmux;\033\033]133;B\007\033\\'
  else
    __cyrene_prompt_marker_b=$'\033]133;B\007'
  fi
  autoload -Uz add-zsh-hook
  __cyrene_zsh_precmd() {
    local __cyrene_status=$?
    local __cyrene_path="${PWD//\%/%25}"
    __cyrene_path="${__cyrene_path// /%20}"
    __cyrene_emit_osc "133;D;${__cyrene_status}"
    __cyrene_emit_osc "7;file://${HOST:-localhost}${__cyrene_path}"
    __cyrene_emit_osc "2;${PWD}"
    __cyrene_emit_osc "133;P;Integration=full"
    __cyrene_emit_osc "133;A"
  }
  __cyrene_zsh_preexec() { __cyrene_emit_osc "133;C"; }
  add-zsh-hook precmd __cyrene_zsh_precmd
  add-zsh-hook preexec __cyrene_zsh_preexec
  precmd_functions=(__cyrene_zsh_precmd ${precmd_functions:#__cyrene_zsh_precmd})
  preexec_functions=(__cyrene_zsh_preexec ${preexec_functions:#__cyrene_zsh_preexec})
  PROMPT="${PROMPT}"$'%{'"${__cyrene_prompt_marker_b}"$'%}'
  if [[ -n "${CYRENE_CLAUDE_PLUGIN_DIR:-}" ]]; then
    claude() {
      command claude --plugin-dir "${CYRENE_CLAUDE_PLUGIN_DIR}" "$@"
    }
  fi
fi
'''

_FISH_SCRIPT = r'''# Cyrene shell integration. Loaded after config.fish.
if not set -q __CYRENE_SHELL_INTEGRATION_LOADED
    set -g __CYRENE_SHELL_INTEGRATION_LOADED 1
    function __cyrene_emit_osc
        if set -q TMUX
            printf '\033Ptmux;\033\033]%s\007\033\\' "$argv[1]"
        else
            printf '\033]%s\007' "$argv[1]"
        end
    end
    if set -q TMUX_PANE
        command tmux set-option -p -t "$TMUX_PANE" allow-passthrough on >/dev/null 2>&1
        if set -q CYRENE_REMOTE_SHELL_LAUNCHER
            command tmux set-option default-command "$CYRENE_REMOTE_SHELL_LAUNCHER" >/dev/null 2>&1
        end
    end
    if functions -q fish_prompt
        functions -c fish_prompt __cyrene_original_fish_prompt
    end
    function __cyrene_fish_preexec --on-event fish_preexec
        __cyrene_emit_osc '133;C'
    end
    function __cyrene_fish_postexec --on-event fish_postexec
        set -l __cyrene_status $status
        __cyrene_emit_osc "133;D;$__cyrene_status"
    end
    function fish_prompt
        set -l __cyrene_path (string replace -a '%' '%25' "$PWD")
        set __cyrene_path (string replace -a ' ' '%20' "$__cyrene_path")
        __cyrene_emit_osc "7;file://"(hostname)"$__cyrene_path"
        __cyrene_emit_osc "2;$PWD"
        __cyrene_emit_osc '133;P;Integration=full'
        __cyrene_emit_osc '133;A'
        if functions -q __cyrene_original_fish_prompt
            __cyrene_original_fish_prompt
        end
        __cyrene_emit_osc '133;B'
    end
    if set -q CYRENE_CLAUDE_PLUGIN_DIR
        function claude
            command claude --plugin-dir "$CYRENE_CLAUDE_PLUGIN_DIR" $argv
        end
    end
end
'''

_POWERSHELL_SCRIPT = r'''# Cyrene shell integration. PowerShell is launched with -NoProfile as before.
if ($global:CyreneShellIntegrationLoaded) { return }
$global:CyreneShellIntegrationLoaded = $true
$script:CyreneEsc = [char]27
$script:CyreneOriginalPrompt = if (Test-Path Function:\prompt) {
    (Get-Item Function:\prompt).ScriptBlock
} else {
    { "PS $($executionContext.SessionState.Path.CurrentLocation)> " }
}
function global:prompt {
    $cyreneSucceeded = $?
    $cyreneNativeExit = $global:LASTEXITCODE
    $cyreneExit = if ($cyreneSucceeded) { 0 } elseif ($cyreneNativeExit -is [int] -and $cyreneNativeExit -ne 0) { $cyreneNativeExit } else { 1 }
    $cyrenePath = $executionContext.SessionState.Path.CurrentLocation.Path
    try { $cyreneUri = ([Uri]$cyrenePath).AbsoluteUri } catch { $cyreneUri = "file://localhost/$cyrenePath" }
    [Console]::Write("$($script:CyreneEsc)]133;D;$cyreneExit$($script:CyreneEsc)\")
    [Console]::Write("$($script:CyreneEsc)]7;$cyreneUri$($script:CyreneEsc)\")
    [Console]::Write("$($script:CyreneEsc)]2;$cyrenePath$($script:CyreneEsc)\")
    [Console]::Write("$($script:CyreneEsc)]133;A$($script:CyreneEsc)\")
    $cyrenePrompt = & $script:CyreneOriginalPrompt
    [Console]::Write("$($script:CyreneEsc)]133;B$($script:CyreneEsc)\")
    return $cyrenePrompt
}
Import-Module PSReadLine -ErrorAction SilentlyContinue
if ($env:CYRENE_CLAUDE_PLUGIN_DIR) {
    function global:claude {
        $cyreneClaude = Get-Command claude -CommandType Application -ErrorAction Stop | Select-Object -First 1
        & $cyreneClaude.Source --plugin-dir $env:CYRENE_CLAUDE_PLUGIN_DIR @args
    }
}
if ("Microsoft.PowerShell.PSConsoleReadLine" -as [type]) {
    function global:PSConsoleHostReadLine {
        $cyreneLine = [Microsoft.PowerShell.PSConsoleReadLine]::ReadLine($host.Runspace, $ExecutionContext)
        [Console]::Write("$($script:CyreneEsc)]133;C$($script:CyreneEsc)\")
        return $cyreneLine
    }
}
'''

_CMD_SCRIPT = r'''@echo off
prompt $E]2;$P$E\$E]7;file://localhost/$P$E\$E]133;A$E\$P$G$S$E]133;B$E\
'''


@dataclass(frozen=True, slots=True)
class ShellIntegrationLaunch:
    argv: list[str]
    env: dict[str, str]
    integration_level: str
    shell_kind: str


def shell_kind(shell: str, argv: list[str]) -> str:
    """Detect the actual executable, even for historical ``shell='bash'`` rows."""
    executable = str(argv[0] if argv else shell or "")
    name = Path(executable.replace("\\", "/")).name.casefold()
    if name.endswith(".exe"):
        name = name[:-4]
    if name in {"bash", "zsh", "fish", "pwsh", "powershell", "cmd"}:
        return "powershell" if name in {"pwsh", "powershell"} else name
    declared = str(shell or "").casefold()
    return declared if declared in {"bash", "zsh", "fish", "powershell", "cmd"} else ""


def _write_script(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    try:
        if path.read_bytes() == encoded:
            return path
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(encoded)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


def _prepare_claude_hook_plugin(root: Path) -> Path:
    """Create a session-scoped Claude plugin without touching user settings."""
    plugin = root / "agent-hooks" / "claude"
    command = (
        '"${CYRENE_AGENT_HOOK_PYTHON}" -m "${CYRENE_AGENT_HOOK_MODULE}" '
        "--agent claude"
    )

    def command_hook(state: str) -> dict[str, str | int]:
        return {
            "type": "command",
            "command": f"{command} --event {state}",
            "timeout": 2,
        }

    hooks = {
        "description": "Report Claude Code lifecycle state to Cyrene.",
        "hooks": {
            "SessionStart": [{"hooks": [command_hook("idle")]}],
            "UserPromptSubmit": [{"hooks": [command_hook("working")]}],
            "PermissionRequest": [{"hooks": [command_hook("waiting")]}],
            "PreToolUse": [{
                "matcher": "AskUserQuestion",
                "hooks": [command_hook("waiting")],
            }],
            "Notification": [{
                "matcher": "permission_prompt|elicitation_dialog",
                "hooks": [command_hook("waiting")],
            }],
            "Stop": [{"hooks": [command_hook("completed")]}],
            "SessionEnd": [{"hooks": [command_hook("interrupted")]}],
        },
    }
    manifest = {
        "name": "cyrene-agent-status",
        "version": "1.0.0",
        "description": "Session-local lifecycle bridge for Cyrene terminals.",
        "author": {"name": "Cyrene"},
    }
    _write_script(
        plugin / ".claude-plugin" / "plugin.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    _write_script(
        plugin / "hooks" / "hooks.json",
        json.dumps(hooks, ensure_ascii=False, indent=2) + "\n",
    )
    return plugin


def prepare_shell_integration(
    *,
    shell: str,
    argv: list[str],
    env: dict[str, str],
    runtime_dir: Path,
    launch_mode: str = "interactive",
) -> ShellIntegrationLaunch:
    """Prepare an integrated interactive launch without mutating its inputs."""
    original_argv = [str(part) for part in argv]
    prepared_env = dict(env)
    kind = shell_kind(shell, original_argv)
    if (
        str(launch_mode or "interactive") != "interactive"
        or not original_argv
        or kind not in {"bash", "zsh", "fish", "powershell", "cmd"}
    ):
        return ShellIntegrationLaunch(original_argv, prepared_env, "none", kind)

    scripts = Path(runtime_dir) / "shell-integration"
    prepared_env["CYRENE_CLAUDE_PLUGIN_DIR"] = str(
        _prepare_claude_hook_plugin(scripts)
    )
    executable = original_argv[0]
    if kind == "bash":
        integration = _write_script(
            scripts / "cyrene.bash.integration", _BASH_INTEGRATION_SCRIPT
        )
        launcher = _write_script(scripts / "cyrene.bash", _BASH_SCRIPT)
        prepared_env["CYRENE_SHELL_INTEGRATION_SCRIPT"] = str(integration)
        # Bash imports these hooks in descendant shells. Interactive children
        # inherit the exported prompt hooks; non-interactive children source
        # the same script through Bash's native BASH_ENV mechanism.
        prepared_env["BASH_ENV"] = str(integration)
        launch_argv = [executable, "--rcfile", str(launcher), "-i"]
        # Bash itself upgrades this to full after confirming PS0 support (4.4+).
        level = "basic"
    elif kind == "zsh":
        zsh_dir = scripts / "zsh"
        _write_script(zsh_dir / ".zshenv", _ZSH_ENV_SCRIPT)
        _write_script(zsh_dir / ".zshrc", _ZSH_RC_SCRIPT)
        integration = _write_script(
            scripts / "cyrene.zsh.integration", _ZSH_INTEGRATION_SCRIPT
        )
        prepared_env["CYRENE_ORIGINAL_ZDOTDIR"] = (
            prepared_env.get("ZDOTDIR") or prepared_env.get("HOME") or ""
        )
        prepared_env["CYRENE_INTEGRATION_ZDOTDIR"] = str(zsh_dir)
        prepared_env["CYRENE_SHELL_INTEGRATION_SCRIPT"] = str(integration)
        prepared_env["ZDOTDIR"] = str(zsh_dir)
        launch_argv = [executable, "-i"]
        level = "full"
    elif kind == "fish":
        script = _write_script(scripts / "cyrene.fish", _FISH_SCRIPT)
        xdg_root = scripts / "xdg"
        _write_script(xdg_root / "fish" / "conf.d" / "cyrene.fish", _FISH_SCRIPT)
        config_dirs = prepared_env.get("XDG_CONFIG_DIRS") or (
            "/etc/xdg" if os.name != "nt" else ""
        )
        prepared_env["XDG_CONFIG_DIRS"] = os.pathsep.join(
            part for part in (str(xdg_root), config_dirs) if part
        )
        prepared_env["CYRENE_SHELL_INTEGRATION_SCRIPT"] = str(script)
        launch_argv = [executable, "-C", f'source "{script}"', "-i"]
        level = "full"
    elif kind == "powershell":
        script = _write_script(scripts / "cyrene.ps1", _POWERSHELL_SCRIPT)
        prepared_env["CYRENE_SHELL_INTEGRATION_SCRIPT"] = str(script)
        launch_argv = [
            executable, "-NoLogo", "-NoProfile", "-NoExit", "-File", str(script),
        ]
        level = "full"
    else:
        script = _write_script(scripts / "cyrene.cmd", _CMD_SCRIPT)
        prepared_env["CYRENE_SHELL_INTEGRATION_SCRIPT"] = str(script)
        launch_argv = [executable, "/d", "/q", "/k", f'call "{script}"']
        level = "basic"
    prepared_env["CYRENE_SHELL_INTEGRATION"] = "1"
    prepared_env["CYRENE_SHELL_INTEGRATION_DIR"] = str(scripts)
    return ShellIntegrationLaunch(launch_argv, prepared_env, level, kind)


def remote_shell_integration_files() -> dict[str, str]:
    """Return the versioned files installed by the managed SSH bootstrap."""
    return {
        "cyrene.bash": _BASH_SCRIPT,
        "cyrene.bash.integration": _BASH_INTEGRATION_SCRIPT,
        "cyrene.zshenv": _ZSH_ENV_SCRIPT,
        "cyrene.zshrc": _ZSH_RC_SCRIPT,
        "cyrene.zsh.integration": _ZSH_INTEGRATION_SCRIPT,
        "cyrene.fish": _FISH_SCRIPT,
    }


class OscMetadataParser:
    """Incrementally parse OSC metadata across arbitrary PTY chunk boundaries."""

    def __init__(self, *, max_payload: int = 64 * 1024) -> None:
        self.max_payload = max(256, int(max_payload))
        self._mode = "normal"
        self._escape_seq = 0
        self._osc_start_seq = 0
        self._payload = bytearray()
        self._next_seq: int | None = None

    def reset(self) -> None:
        self._mode = "normal"
        self._payload.clear()
        self._next_seq = None

    @property
    def pending_sequence_start(self) -> int | None:
        """Absolute start of an escape sequence awaiting more PTY bytes."""
        if self._mode == "escape":
            return self._escape_seq
        if self._mode in {"osc", "osc_escape"}:
            return self._osc_start_seq
        return None

    def feed(self, data: bytes, *, start_seq: int) -> list[dict[str, Any]]:
        """Return metadata events; ``data`` itself is never modified or returned."""
        payload = bytes(data or b"")
        absolute_start = int(start_seq)
        if self._next_seq is not None and absolute_start != self._next_seq:
            self.reset()
        if self._mode == "normal" and b"\x1b" not in payload:
            self._next_seq = absolute_start + len(payload)
            return []
        events: list[dict[str, Any]] = []
        for offset, value in enumerate(payload):
            seq = absolute_start + offset
            if self._mode == "normal":
                if value == 0x1B:
                    self._mode = "escape"
                    self._escape_seq = seq
                continue
            if self._mode == "escape":
                if value == ord("]"):
                    self._mode = "osc"
                    self._osc_start_seq = self._escape_seq
                    self._payload.clear()
                elif value == 0x1B:
                    self._escape_seq = seq
                else:
                    self._mode = "normal"
                continue
            if self._mode == "osc_escape":
                if value == ord("\\"):
                    event = self._event(self._osc_start_seq, seq + 1)
                    if event is not None:
                        events.append(event)
                    self._mode = "normal"
                    self._payload.clear()
                    continue
                self._payload.append(0x1B)
                self._mode = "osc"
            if value == 0x07:
                event = self._event(self._osc_start_seq, seq + 1)
                if event is not None:
                    events.append(event)
                self._mode = "normal"
                self._payload.clear()
            elif value == 0x1B:
                self._mode = "osc_escape"
            else:
                self._payload.append(value)
                if len(self._payload) > self.max_payload:
                    self._mode = "normal"
                    self._payload.clear()
        self._next_seq = absolute_start + len(payload)
        return events

    def _event(self, start_seq: int, end_seq: int) -> dict[str, Any] | None:
        raw = bytes(self._payload)
        identifier, separator, body = raw.partition(b";")
        if not separator:
            return None
        if identifier in {b"0", b"2"}:
            return {
                "kind": "title",
                "value": body.decode("utf-8", errors="replace"),
                "startSeq": start_seq,
                "endSeq": end_seq,
            }
        if identifier == b"7":
            uri = body.decode("utf-8", errors="replace")
            parsed = urlsplit(uri)
            if parsed.scheme.casefold() != "file":
                return None
            path = unquote(parsed.path)
            if re.match(r"^/[A-Za-z]:[/\\]", path):
                path = path[1:].replace("/", "\\")
            return {
                "kind": "cwd",
                "value": path,
                "uri": uri,
                "host": parsed.hostname or "",
                "startSeq": start_seq,
                "endSeq": end_seq,
            }
        if identifier != b"133" or not body:
            return None
        marker, _, detail = body.partition(b";")
        if marker == b"P":
            name, separator, value = detail.partition(b"=")
            if separator and name == b"Integration" and value in {
                b"none", b"basic", b"full",
            }:
                return {
                    "kind": "integration",
                    "value": value.decode("ascii"),
                    "startSeq": start_seq,
                    "endSeq": end_seq,
                }
            property_kinds = {
                b"Context": "context",
                b"ProfileId": "profile",
                b"Lifecycle": "lifecycle",
            }
            kind = property_kinds.get(name)
            if separator and kind is not None and len(value) <= 4096:
                return {
                    "kind": kind,
                    "value": value.decode("utf-8", errors="replace"),
                    "startSeq": start_seq,
                    "endSeq": end_seq,
                }
            return None
        names = {b"A": "prompt", b"B": "command", b"C": "output", b"D": "finished"}
        kind = names.get(marker)
        if kind is None:
            return None
        event: dict[str, Any] = {
            "kind": kind,
            "marker": marker.decode("ascii"),
            "startSeq": start_seq,
            "endSeq": end_seq,
        }
        if marker == b"D":
            exit_value = detail.split(b";", 1)[0]
            try:
                event["exitCode"] = int(exit_value) if exit_value else None
            except ValueError:
                event["exitCode"] = None
        return event


__all__ = [
    "OscMetadataParser",
    "ShellIntegrationLaunch",
    "prepare_shell_integration",
    "remote_shell_integration_files",
    "shell_kind",
]
