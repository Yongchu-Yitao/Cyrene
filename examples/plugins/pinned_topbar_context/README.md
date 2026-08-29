# Pinned topbar context PluginPack

This Cyrene context plugin mounts the latest Workbench topbar shelf into each
new user turn through a tree-local `TurnStart` Hook.

- Files are mounted as compact resource metadata with a resolvable path; their
  bodies remain available for the Agent to read on demand.
- Selected text uses Workbench's existing Markdown materialization and is
  exposed as a pinned file.
- Conversations use Workbench's bounded, read-only summary instead of exposing
  or controlling the full peer transcript.
- Pins are read when the user sends a message, so resources added after a
  conversation was created appear on its next turn.

Validate this directory with `PluginValidate`, then install it with
`PluginInstall`. It has no application contribution, so Python reload is enough
and an application restart is not required.
