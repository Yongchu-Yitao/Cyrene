#!/bin/sh
set -eu

sandbox_path="/opt/Cyrene/chrome-sandbox"
if [ -f "$sandbox_path" ]; then
  chown root:root "$sandbox_path"
  chmod 4755 "$sandbox_path"
fi

# Semantic App Use talks to the desktop accessibility bus. Package managers
# normally pull this in through the graphical desktop; keep install scripts
# non-invasive and merely document the runtime package when it is absent.
if ! command -v at-spi-bus-launch >/dev/null 2>&1; then
  echo "Cyrene semantic App Use requires AT-SPI2 (install at-spi2-core)." >&2
fi

# X11 current-desktop control uses xdotool for real pointer down/up and key
# injection. Wayland deliberately does not fall back to xdotool because native
# compositors reject or isolate those synthetic events.
if [ "${XDG_SESSION_TYPE:-x11}" != "wayland" ] && ! command -v xdotool >/dev/null 2>&1; then
  echo "Cyrene Remote Desktop input on X11 requires xdotool." >&2
fi
