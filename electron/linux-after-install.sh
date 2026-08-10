#!/bin/sh
set -eu

sandbox_path="/opt/Cyrene/chrome-sandbox"
if [ -f "$sandbox_path" ]; then
  chown root:root "$sandbox_path"
  chmod 4755 "$sandbox_path"
fi
