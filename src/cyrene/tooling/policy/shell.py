"""Shell-policy helpers."""

from cyrene.tooling import runtime_support as _implementation

__all__ = [
    "_classify_destructive_shell_command",
    "_command_is_file_deletion",
    "_guard_nonbash_shell_command",
    "_guard_shell_command_workspace_write",
    "_is_dangerous_subshell",
    "classify_destructive_shell_command",
    "command_is_file_deletion",
    "guard_nonbash_shell_command",
    "guard_shell_command_workspace_write",
    "is_dangerous_subshell",
]

classify_destructive_shell_command = _implementation._classify_destructive_shell_command
command_is_file_deletion = _implementation._command_is_file_deletion
guard_nonbash_shell_command = _implementation._guard_nonbash_shell_command
guard_shell_command_workspace_write = _implementation._guard_shell_command_workspace_write
is_dangerous_subshell = _implementation._is_dangerous_subshell

_classify_destructive_shell_command = classify_destructive_shell_command
_command_is_file_deletion = command_is_file_deletion
_guard_nonbash_shell_command = guard_nonbash_shell_command
_guard_shell_command_workspace_write = guard_shell_command_workspace_write
_is_dangerous_subshell = is_dangerous_subshell
