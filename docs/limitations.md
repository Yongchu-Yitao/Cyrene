# Current limitations

[English](limitations.md) · [简体中文](limitations.zh-CN.md)

## Operator and security boundaries

- Cyrene serves one person on one computer. Keep the web server local. Projects organize that person's work and share the OS account’s access. Use OS accounts, virtual machines, or containers when users or programs need isolation.
- Tool permissions govern the actions the agent can take. Desktop browser cookies and logins are shared across projects.
- Pinning a file makes it discoverable and readable through file tools in every conversation. A pinned page stays under the original conversation’s control. Other conversations have read-only access through snapshots and screenshots.
- Before opening a URL, the browser blocks direct access to loopback, private, link-local, cloud metadata, and reserved addresses. Page resources, script navigation, and desktop browser redirects can still reach these addresses.
- A file upload needs separate user approval. Purchases, publishing, deletion, and account changes follow the user’s request and ordinary tool permissions, including when a click or pressing Enter completes the action.

## Models, integrations, and budgets

- Prompts and selected context are sent to configured model services.
  Integrations may also exchange data with their configured services.
- Chat models currently require an OpenAI-compatible endpoint.
- Budgets use local usage estimates. Your provider determines the actual charge.

## Data and API lifecycle

- Data stays on disk until you delete it or reset the installation.
- The HTTP API is not yet versioned as a stable public API.

## Cyrene self-control boundaries

- UI snapshot and inspect show the currently rendered interface. The agent must perform an action and take a new snapshot after
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

- Literature lookup by DOI or title
- Two-way synchronization with the Zotero Web API
- Experiments and Manuscripts

## Platform and validation constraints

- Windows source installation has an upstream SimpleXNG limitation. Use a
  pre-built app or follow the checked-in release workflow.
- Pull-request CI covers the full Python suite, WebUI build, and Electron App
  Use tests on Linux. Packaged, visual, upgrade, and credentialed integration
  checks remain release or manual gates.
- Python and Electron browser tests primarily cover protocols, state machines,
  security checks, and isolated JavaScript behavior. Many use fake pages, mock
  locators, or source contracts; complex live sites, OAuth/CAPTCHA, cross-origin
  iframes, shadow DOM, and native window flows on every platform still require
  release or manual integration testing.
- Browser snapshot, ref targeting, and wait behavior primarily cover the current
  top-level DOM. Iframes, shadow DOM, Canvas/WebGL, and complex editors do not
  have a unified recursive semantic model. The network log shows resource timings. Use developer tools for a full request trace.

See [Development](development.md) for the exact validation baseline and
[Development Status](../project-notes/README.md) for
known engineering risks.
