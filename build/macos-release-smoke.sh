#!/usr/bin/env bash
set -euo pipefail

shopt -s nullglob
dmgs=(dist-electron/Cyrene-*-mac.dmg)
if [[ ${#dmgs[@]} -ne 1 ]]; then
  echo "Expected exactly one macOS DMG, found ${#dmgs[@]}" >&2
  exit 1
fi

mount_root="${RUNNER_TEMP:-/tmp}"
mount_dir="$(mktemp -d "${mount_root%/}/cyrene-dmg.XXXXXX")"
mounted=false
cleanup() {
  if [[ "$mounted" == true ]]; then
    hdiutil detach "$mount_dir" -quiet || true
  fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

for attempt in 1 2 3; do
  if hdiutil attach "${dmgs[0]}" -nobrowse -readonly -mountpoint "$mount_dir"; then
    mounted=true
    break
  fi
  if [[ $attempt -lt 3 ]]; then
    sleep $((attempt * 5))
  fi
done
if [[ "$mounted" != true ]]; then
  echo "Unable to mount macOS DMG after 3 attempts" >&2
  exit 1
fi

app_path="$mount_dir/Cyrene.app"
backend="$app_path/Contents/Resources/python-bundle/Cyrene"
desktop="$app_path/Contents/MacOS/Cyrene"
[[ -x "$backend" ]] || { echo "Installed backend is missing: $backend" >&2; exit 1; }
[[ -x "$desktop" ]] || { echo "Installed desktop app is missing: $desktop" >&2; exit 1; }

run_and_require() {
  local success_marker="$1"
  shift
  local output
  local status
  set +e
  output=$("$@" 2>&1)
  status=$?
  set -e
  printf '%s\n' "$output"
  if [[ $status -ne 0 ]]; then
    echo "Smoke command exited with status $status" >&2
    exit "$status"
  fi
  if grep -Eq 'SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed' <<<"$output"; then
    echo "Smoke command reported failure" >&2
    exit 1
  fi
  if ! grep -Fq "$success_marker" <<<"$output"; then
    echo "Smoke command did not report success marker: $success_marker" >&2
    exit 1
  fi
}

run_and_require "Cyrene smoke test OK:" "$backend" --smoke-test

export CYRENE_USER_DATA_DIR="${RUNNER_TEMP:-/tmp}/cyrene-macos-smoke/data"
export CYRENE_CACHE_DIR="${RUNNER_TEMP:-/tmp}/cyrene-macos-smoke/cache"
export CYRENE_TEMP_DIR="${RUNNER_TEMP:-/tmp}/cyrene-macos-smoke/tmp"
run_and_require "DESKTOP_SMOKE_TEST=ok" "$desktop" --desktop-smoke-test

echo "MACOS_INSTALL_SMOKE_TEST=ok"
