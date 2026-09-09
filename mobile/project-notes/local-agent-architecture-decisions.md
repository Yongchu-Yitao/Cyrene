# Local Agent architecture decisions

Status: accepted, 2026-08-02.

## ADR-01 — Mobile owns local sessions

Local sessions, messages, runs, traces, approvals, and artifacts are Android
local data. They are not Desktop conversations and do not use
`local.sessions.*`, `local.sync.*`, Desktop projects, tasks, or leases.

## ADR-02 — Mobile calls the model provider

Android owns the agent loop and LLM request. It uses a Keystore-encrypted local
copy of the Desktop canonical `{version, connections, profiles, routes}` model
graph. Mobile resolves local inference through route → profile → connection.
Desktop transfers and synchronizes configuration only; it is never the model
gateway for a local session.

## ADR-03 — Copy API keys, never OAuth tokens

`settings.models.copy` uses the paired E2EE channel and the existing
`settings:read` grant. Ordinary provider API keys may be copied. Codex OAuth
credentials and unrelated secrets never leave Desktop.

Mobile edits use the `models` payload of `settings.update` with the graph
revision for atomic writeback. UI projections may redact connection secrets, but the encrypted
runtime copy and wire payload never use a second, mobile-only model shape.

## ADR-04 — Separate signed Runtime APK

File and shell tools run in `ai.cyrene.mobile.runtime`, a separately signed APK
and UID. The main app binds through a signature permission and sends typed
requests only. The Runtime receives no provider configuration or API key.

The Runtime declares `INTERNET` solely for QEMU user-mode NAT. Network access is
available to Linux commands, while LLM credentials remain in the main app.

## ADR-05 — Real QEMU system VM and signed Alpine image

The Runtime embeds Limbo/QEMU 5.1.0 TCG and an Alpine 3.24 x86_64 guest. The RSA
signed manifest covers the kernel, bootstrap initramfs, persistent ext4 rootfs,
firmware, and host-resolver fallback. Every digest is checked before boot.

No model-provided command runs through Android `/system/bin/sh`.

## ADR-06 — One VM and one persistent filesystem

All local chats attach to one Runtime service VM. Session IDs identify request
ownership and traces; they do not create separate filesystems. Every session
uses `/workspace`, so files are mutually visible. The full ext4 Alpine root is
persistent, so both workspace files and `apk add` packages survive Runtime
restarts.

## ADR-07 — Fixed tool surface and boundaries

The v1 model tool surface is `Read`, `Write`, `Edit`, `Glob`, `Grep`, and
`Bash`. File operations reject absolute paths and `..` traversal outside the
shared workspace. Commands have deadlines and output limits, and each call
produces exactly one structured result.

## ADR-08 — Local persistence and recovery

Android SQLite is authoritative for local chat data. Foreground-service
checkpoints and append-oriented traces support recovery. No job uploads local
session state to Desktop.
