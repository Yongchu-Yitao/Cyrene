#!/usr/bin/env bash
set -euo pipefail

shopt -s nullglob
packages=(/workspace/dist-electron/Cyrene-*-x64.rpm)
if [[ ${#packages[@]} -ne 1 ]]; then
  echo "Expected exactly one RPM package, found ${#packages[@]}" >&2
  exit 1
fi

dnf install -y "${packages[0]}" dbus-daemon xorg-x11-server-Xvfb xorg-x11-xauth
[[ "$(stat -c '%u:%a' /opt/Cyrene/chrome-sandbox)" == "0:4755" ]]

export CYRENE_USER_DATA_DIR=/tmp/cyrene-rpm-smoke/data
export CYRENE_CACHE_DIR=/tmp/cyrene-rpm-smoke/cache
export CYRENE_TEMP_DIR=/tmp/cyrene-rpm-smoke/tmp
mkdir -p "$CYRENE_USER_DATA_DIR" "$CYRENE_CACHE_DIR" "$CYRENE_TEMP_DIR" /run/dbus

backend_output=$(/opt/Cyrene/resources/python-bundle/Cyrene --smoke-test 2>&1)
printf '%s\n' "$backend_output"
grep -Fq 'Cyrene smoke test OK: v0.7.6' <<<"$backend_output"
grep -Eq 'numpy=[0-9]+\.[0-9]+' <<<"$backend_output"

if [[ ! -S /run/dbus/system_bus_socket ]]; then
  dbus-daemon --system --fork
fi

set +e
output=$(timeout 180s dbus-run-session -- \
  xvfb-run -a /opt/Cyrene/cyrene --no-sandbox --desktop-smoke-test 2>&1)
status=$?
set -e
printf '%s\n' "$output"
if [[ $status -ne 0 ]]; then
  if [[ -f "$CYRENE_TEMP_DIR/cyrene_error.log" ]]; then
    sed -n '1,240p' "$CYRENE_TEMP_DIR/cyrene_error.log" >&2
  fi
  echo "Installed RPM desktop smoke test exited with status $status" >&2
  exit "$status"
fi
if grep -Eq 'SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed' <<<"$output"; then
  echo "Installed RPM desktop smoke test reported failure" >&2
  exit 1
fi
if ! grep -Fq 'DESKTOP_SMOKE_TEST=ok' <<<"$output"; then
  echo "Installed RPM desktop smoke test did not report success" >&2
  exit 1
fi

echo "LINUX_RPM_INSTALL_SMOKE_TEST=ok"
