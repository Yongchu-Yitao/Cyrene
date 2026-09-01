# Browser Live View & Login Takeover

[English](browser-live-view.md) ·
[简体中文](browser-live-view.zh-CN.md)

When the agent uses the browser, the chat UI shows a **live view** of the page —
what the agent sees and the actions it takes — in the right-hand panel. When the
agent hits a login wall, CAPTCHA, or 2FA, it can **hand the browser to you**: a real
window opens, you log in, and the agent resumes in the same (now authenticated)
session.

## Runtime and setup

The packaged Electron desktop app uses Electron's embedded Chromium directly.
It needs no Playwright installation. Every conversation has its own in-memory
tab manager, current page, and navigation history, while all conversations use
the shared persistent `persist:cyrene-browser` partition. Cookies and login state
therefore remain available across conversations. Browser tool calls are routed
by the originating conversation ID and share that conversation's visible tabs.
Deleting a conversation closes its tabs without clearing the shared login data.
An Electron RPC failure is reported instead of silently opening a second browser.

When Cyrene runs without Electron (source Web UI or CLI), `browser_navigate` can
fall back to a plain HTTP fetch (`httpx`). For full non-desktop automation, live
screencast, and headed login takeover, install the optional Playwright runtime:

```bash
pip install -e ".[browser]"   # installs the optional "browser" extra (Playwright)
playwright install chromium    # one-time download of the Chromium runtime
```

If Playwright (or its Chromium runtime) is missing outside Electron, the
live-view panel shows a hint and `browser_navigate` degrades to a text-only fetch;
interactive tools report that the browser runtime is unavailable.

## How the live view works

### Electron desktop

- Electron creates native browser tabs as `WebContentsView` instances and embeds
  the active tab in the Workbench browser panel.
- The Python tool layer calls a loopback, token-authenticated Electron RPC server
  for navigation, snapshots, clicks, typing, waits, network logs, screenshots,
  scrolling, and tab management.
- Electron keeps a separate tab manager for every conversation session.
  Switching conversations detaches the old native view and attaches the selected
  conversation's active tab.
- Background agents include their session ID in every browser RPC, so they can
  browse without changing or controlling the browser visible in another chat.
- Within one conversation, the user and the agent operate the same persistent
  browser tabs and profile.
- All conversation managers intentionally share the same Electron partition, so
  signing in once makes that login available to other conversations.

### Pinned Browser resources

The entire floating Browser titlebar and the minimized Browser control can be
dragged to the topbar resource shelf. PiP pinning uses direct pointer-to-shelf
rectangle hit testing, so it remains reliable above Electron's native page;
drops elsewhere still only move the window. The minimized control displays only
the current page favicon and immediately falls back to the Browser SVG when the
favicon fails to load. A click restores PiP, while movement past the drag
threshold enters the pin interaction. The minimized control shares the PiP
coordinate commit and transcript-avoidance path, so it can be freely positioned
inside the conversation area. A body-level fixed drag proxy crosses above the
conversation header's clipping and stacking contexts on the way to the shelf.
Pinning
preserves the original page and owner session; it does not move or duplicate
the tab. Later Agent turns in every session can discover the pinned Browser
resource ID.

The Browser frame itself is also represented by stable Cyrene UI components.
Through `cyrene.ui.snapshot` / `cyrene.ui.inspect`, the agent can click the
titlebar controls, drag the floating frame, and maximize or restore it (including
the titlebar double-click gesture) without depending on whichever input currently
has keyboard focus. This controls Cyrene's frame; browser-page automation remains
in the separate `browser_*` tool family.

Dropping the PiP titlebar, minimized favicon button, or pinned Browser chip on
another conversation creates a new same-URL page in that session's Browser
manager. This copies the page entry instead of transferring ownership or
control. Login state remains available through the shared partition, while
tabs, navigation history, and subsequent operations are independent.

The owner session keeps normal Browser control. Other sessions may pass the
resource ID only to `browser_snapshot` or `browser_screenshot`. Navigation,
clicking, typing, reload, history movement, uploads, and all other mutations are
rejected by the browser execution layer. The topbar context menu can show a
read-only current-page preview without hiding the original PiP view.

### Non-Electron Playwright mode

- A single **persistent browser context** is launched lazily and reused across all
  browser actions. Its profile lives on disk at `<DATA_DIR>/browser_profile`, so
  cookies / logins **survive across runs** — once you log into a site, the agent
  stays logged in next time.
- After each action (`navigate` / `click` / `type`) the backend publishes a
  lightweight `browser_frame` SSE event with metadata only (URL, title, action,
  target, and target box). Pixel data does not ride the shared SSE bus.
- A continuous **CDP screencast** streams live JPEG frames over the
  `GET /ws/browser` WebSocket as binary frames to the panel.
- The panel auto-reveals in the chat's right sidebar the moment the agent starts
  browsing, and a **Browser** tab lets you reopen it.

## Page interaction model and current boundaries

- `browser_snapshot` returns only visible, hit-testable elements in the current
  viewport and assigns short-lived refs such as `e1` and `e2`. Take a fresh
  snapshot after navigation, same-document navigation, scrolling, or substantial
  layout changes instead of reusing stale refs.
- Before an Electron click sends trusted Chromium mouse input, it resolves the
  target again, checks whether the click point is obscured, and retries up to
  three times when the target moves. It refuses to click a persistently unstable
  target.
- `browser_navigate` validates the initial HTTP(S) destination and blocks
  loopback, private, link-local, cloud-metadata, and reserved networks. This is
  not a complete browser network sandbox: subresources, script-driven
  navigation, and every Electron redirect are not all governed by a per-request
  IP policy. The browser remains intended for one local operator browsing
  trusted public content.
- Snapshot, ref targeting, and wait DOM projection primarily cover the current
  top-level document. Iframes, cross-origin frames, shadow DOM, Canvas/WebGL, and
  complex rich-text editors do not have one recursive semantic model; use a
  screenshot or user takeover when the projected DOM is insufficient.
- `browser_network_log` reads the page's Resource Performance entries. It reports
  URL, initiator type, duration, and transfer size, not full DevTools request or
  response headers, bodies, status codes, or WebSocket messages.
- Navigation, clicking, typing, and optional Enter submission are normal browser
  tool actions and do not automatically receive the human-only confirmation used
  for file uploads. Callers must apply the user's request and permission policy
  before purchases, publishing, deletion, account changes, or other high-impact
  actions.

## Login takeover

In Electron, the browser is already a visible embedded native view, so the user
can complete login in the same tab. In non-Electron Playwright mode, login
takeover uses the following headed-window flow:

1. The agent calls the `browser_request_takeover` tool the moment it hits a login
   wall (it is prompted to do this early, before deep work on the page).
2. The shared browser **restarts headed** on the same profile, a real Chromium
   window comes to the foreground, and the agent **pauses** (reusing the standard
   "awaiting user" mechanism). The in-chat screencast pauses and the panel shows a
   takeover card — your login pixels are not streamed.
3. You complete the login in the native window and click **「我已完成登录」**.
4. The browser **restarts headless** on the same profile (now authenticated) and
   the agent resumes the round automatically.

Because the profile is persistent, the window usually only needs to appear once
per site.

> **Local only.** Playwright native-window takeover assumes the Web UI and browser
> run on the same machine. The persistent profile means takeover is rarely needed
> after the first login.

## Configuration

These are read from the config store (env keys); defaults shown. Most viewport,
headless, screencast, and locale settings apply to non-Electron Playwright mode;
`CYRENE_BROWSER_USER_AGENT` is also honored by Electron.

| Key | Default | Meaning |
|-----|---------|---------|
| `CYRENE_BROWSER_HEADLESS` | `1` | Normal mode. Set to `0`/`false` to always run headed. The takeover flow restarts headed regardless. |
| `CYRENE_BROWSER_SCREENCAST_QUALITY` | `60` | JPEG quality (1–100) for live frames. |
| `CYRENE_BROWSER_WIDTH` | `1280` | Viewport / screencast width. |
| `CYRENE_BROWSER_HEIGHT` | `800` | Viewport / screencast height. |
| `CYRENE_BROWSER_USER_AGENT` | auto | Override the browser User-Agent. By default Cyrene strips Playwright's `HeadlessChrome` token and advertises the bundled Chromium as desktop Chrome, because some sites otherwise show a false "upgrade your browser" page. |
| `CYRENE_BROWSER_LOCALE` | `zh-CN` | Browser locale passed to Playwright. |
| `CYRENE_BROWSER_ACCEPT_LANGUAGE` | `zh-CN,zh;q=0.9,en;q=0.8` | `Accept-Language` header used by the browser session. |

The profile directory is `<DATA_DIR>/browser_profile`.

For Electron development, run `npm run dev` from `electron/`. Electron starts
the Python backend through `uv run cyrene --workbench --electron-mode`;
successful startup prints `UIMODE=workbench` and `PORT=4242`.

## Permissions

Browser navigation is a **network read**, like `WebFetch` / `WebSearch`, so it does
**not** require workspace scope elevation. The only on-disk writes are the browser
profile, which lives inside `DATA_DIR` (in-workspace). Login takeover is an
explicit, user-driven action (you perform the login in the native window).

File upload is intentionally stricter. An agent click that would open a native
file chooser is intercepted inside Electron, while a user's own click remains a
normal native interaction. The agent must then call `browser_upload_files`. Every
call pauses for an explicit, human-only, single-use approval showing the receiving
origin and each file's name, size, media type, and SHA-256 digest. Approval is
bound to the browser tab, page/frame URL, input element, and exact file contents;
page, destination, or file changes cancel the upload. `full_access` and automatic
permission modes do not bypass this confirmation. The tool only populates the
file input—it never clicks a separate submit button. Approved bytes are copied to
a private, read-only temporary snapshot so later form submission cannot observe a
changed source file; the snapshot is removed after at most 15 minutes.

## Tools

| Tool | Purpose |
|------|---------|
| `browser_navigate` | Open a URL in the shared session; return readable text. |
| `browser_snapshot` | Inspect visible elements and return refs, text, hrefs, selectors, and boxes. |
| `browser_screenshot` | Screenshot the current page or a provided URL to a temp PNG. |
| `browser_click` | Click an element by CSS selector. |
| `browser_click_ref` | Click an element ref returned by `browser_snapshot`. |
| `browser_click_at` | Click viewport coordinates. |
| `browser_type` | Type into an input (optionally submit). |
| `browser_type_ref` | Type into an editable element ref returned by `browser_snapshot`. |
| `browser_upload_files` | Attach exact local files to an intercepted or referenced file input after single-use human approval. |
| `browser_wait` | Wait for SPA URL/text/selector conditions after async rendering. |
| `browser_network_log` | Return recent resource/fetch/XHR URLs visible to the page. |
| `browser_tab_list` | List Electron browser tabs and their current state. |
| `browser_tab_new` | Open a new tab when the user asks to preserve the current page. |
| `browser_tab_select` | Select an existing browser tab. |
| `browser_tab_close` | Close a specified or active browser tab. |
| `browser_scroll` | Send trusted wheel input to the page or a nested scroll container. |
| `browser_request_takeover` | Open a real window for the user to log in, pause, then resume authenticated. |
