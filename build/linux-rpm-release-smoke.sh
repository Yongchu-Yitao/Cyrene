#!/usr/bin/env bash
set -euo pipefail

shopt -s nullglob
packages=(/workspace/dist-electron/Cyrene-*-x64.rpm)
if [[ ${#packages[@]} -ne 1 ]]; then
  echo "Expected exactly one RPM package, found ${#packages[@]}" >&2
  exit 1
fi

dnf install -y "${packages[0]}" xorg-x11-server-Xvfb xorg-x11-xauth
[[ "$(stat -c '%u:%a' /opt/Cyrene/chrome-sandbox)" == "0:4755" ]]

export CYRENE_USER_DATA_DIR=/tmp/cyrene-rpm-smoke/data
export CYRENE_CACHE_DIR=/tmp/cyrene-rpm-smoke/cache
export CYRENE_TEMP_DIR=/tmp/cyrene-rpm-smoke/tmp

set +e
output=$(timeout 90s xvfb-run -a /opt/Cyrene/cyrene --no-sandbox --desktop-smoke-test 2>&1)
status=$?
set -e
printf '%s\n' "$output"
if [[ $status -ne 0 ]]; then
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
