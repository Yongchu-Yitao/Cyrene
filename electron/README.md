# Electron desktop host

`main.js` is the application entry point; `preload.js` exposes the Workbench
bridge. `dev-launcher.js` starts the development application.

| Directory | Responsibility |
| --- | --- |
| `browser/` | Embedded browser tabs, interaction, previews, picker, video fullscreen and browser preloads |
| `desktop/` | Main and detached windows, desktop settings and host control |
| `automation/` | App Use, platform automation providers, native helper sources and agent cursor |
| `remote-desktop/` | Remote desktop host, RDP sidecar, credentials, sharing indicator and their window assets |
| `backend/` | Python backend process and port readiness |
| `diagnostics/` | Recovery window, renderer and offline doctor |
| `shared/` | Logging and development data migration |
| `scripts/` | Packaging helpers, Linux installation hook and terminal lifecycle soak tool |
| `tests/` | Integration tests spanning Electron and the WebUI |
| `assets/` | Source assets |

Unit tests live beside their modules. Run all desktop tests with `npm test`
from this directory (or `node --test electron/*/*.test.js` from the repository).

Runtime files are explicitly listed in `package.json` → `build.files`.
Keep preloads and HTML/CSS/renderer assets with the subsystem that uses them.
Paths passed to BrowserWindow must resolve relative to the calling module.
The Python remote desktop provider receives the Electron root through
`CYRENE_ELECTRON_RESOURCES_DIR` and loads the sidecar from `remote-desktop/`.

The macOS build helper generates `automation/app-use-macos-hit-test`.
Packaged native helpers retain their external `resources/app-use/` location;
`runtime-tools/` and `.runtime-tools-cache/` remain generated directories at
this root. Do not commit generated binaries or caches.
