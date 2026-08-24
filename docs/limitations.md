# Current limitations

[English](limitations.md) · [简体中文](limitations.zh-CN.md)

This document records the boundaries and known limitations of the current
Cyrene beta. These are product and deployment constraints, not merely a list of
open bugs.

## Operator and security boundaries

- Cyrene is designed for one local operator. Projects are organizational
  boundaries, not separate users or security tenants.
- The Web server is local-only and is not intended for public internet
  exposure.
- Tool permissions reduce accidental actions but do not provide an operating
  system, VM, or container sandbox.
- Electron browser cookies and logins are shared across projects.
- Pinning a file to the topbar is an explicit global-sharing action: every
  session can discover its index and may read it through normal file tools.
- A Browser pinned by another session is read-only at Cyrene's tool layer, but
  all Electron Browser sessions still share the same local cookie partition.

## Models, integrations, and budgets

- Prompts and selected context are sent to configured model services.
  Integrations may also exchange data with their configured services.
- Chat models currently require an OpenAI-compatible endpoint.
- Usage budgets are local estimates, not provider billing controls.

## Data and API lifecycle

- Data has no automatic retention period; it remains until explicitly removed
  or reset.
- The HTTP API is not yet versioned as a stable public API.

## Cyrene self-control boundaries

- UI snapshot and inspect expose the current rendered surface, not hypothetical
  future screens. The agent must perform an action and take a new snapshot after
  navigation, expansion, scrolling, or opening a context menu.
- Stable semantic controls are preferred; generic DOM projection covers visible,
  operable HTML controls. Canvas/WebGL-only controls require a dedicated semantic
  adapter and raw screen coordinates are intentionally not exposed.
- Model selection, secrets, account ceremonies, destructive reset/delete flows,
  and human-only confirmations are outside typed self-management settings.
- Sending the current composer is an explicit R2 action and requires a matching
  user request or normal authorization. Stopping the current run remains R1.
- Background business services remain internal. The public agent surface controls
  visible UI and typed non-model settings rather than exposing project, chat, or
  data management APIs directly.

## Features not yet implemented

- Literature DOI and title lookup is not implemented.
- Zotero Web API two-way synchronization is not implemented.
- Experiments and Manuscripts are not implemented.

## Platform and validation constraints

- Windows source installation has an upstream SimpleXNG limitation. Use a
  pre-built app or follow the checked-in release workflow.
- Pull-request CI covers the full Python suite, WebUI build, and Electron App
  Use tests on Linux. Packaged, visual, upgrade, and credentialed integration
  checks remain release or manual gates.

See [Development](development.md) for the exact validation baseline and
[Development Status](../project-notes/README.md) for
known engineering risks.
