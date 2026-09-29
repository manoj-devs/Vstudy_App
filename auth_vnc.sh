#!/bin/sh
set -eu

PROFILE_DIR="${VSTUDY_PROFILE_DIR:-/data/vstudy_chrome_profile}"
DISPLAY_NUM=:99
export DISPLAY="${DISPLAY_NUM}"

cleanup() {
    kill "${CHROME_PID:-}" "${FLUXBOX_PID:-}" "${XVFB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Stop only Chromium instances that use the VStudy profile, if any.
pkill -f "/usr/lib/chromium/chromium.*--user-data-dir=${PROFILE_DIR}" 2>/dev/null || true
sleep 2

# Remove Chromium singleton locks only after confirming the profile is not in use.
if ! ps -eo args | grep -E "[/]usr/lib/chromium/chromium.*--user-data-dir=${PROFILE_DIR}" | grep -v grep >/dev/null 2>&1; then
    rm -f "${PROFILE_DIR}/SingletonLock" "${PROFILE_DIR}/SingletonSocket" "${PROFILE_DIR}/SingletonCookie"
fi

mkdir -p "${PROFILE_DIR}"

Xvfb "${DISPLAY_NUM}" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
sleep 2

fluxbox >/tmp/fluxbox.log 2>&1 &
FLUXBOX_PID=$!
sleep 1

chromium \
  --no-sandbox \
  --disable-dev-shm-usage \
  --user-data-dir="${PROFILE_DIR}" \
  --profile-directory=Default \
  --start-maximized \
  --no-first-run \
  --no-default-browser-check \
  --disable-notifications \
  --disable-popup-blocking \
  "${VSTUDY_URL:-https://vstudy.saveetha.com/}" >/tmp/chromium-auth.log 2>&1 &
CHROME_PID=$!

exec x11vnc \
  -display "${DISPLAY_NUM}" \
  -localhost \
  -forever \
  -shared \
  -rfbport 5900 \
  -rfbauth /data/vnc.passwd \
  -noxdamage
