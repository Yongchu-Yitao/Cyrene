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

## Models, integrations, and budgets

- Prompts and selected context are sent to configured model services.
  Integrations may also exchange data with their configured services.
- Chat models currently require an OpenAI-compatible endpoint.
- Usage budgets are local estimates, not provider billing controls.

## Data and API lifecycle

- Data has no automatic retention period; it remains until explicitly removed
  or reset.
- The HTTP API is not yet versioned as a stable public API.

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
[Current Development Progress](../project-notes/CONTEXT_DEV_PROGRESS.md) for
known engineering risks.
