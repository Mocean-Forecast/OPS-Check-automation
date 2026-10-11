#!/bin/bash
# Wrapper that launchd calls on the laptop (phase 1, D5/D7).
#   scripts/scheduled-run.sh run --slot morning
#   scripts/scheduled-run.sh watchdog --slot morning
# 1. waits for the network (Wi-Fi/VPN take a moment after waking up),
# 2. mounts the shares that aren't mounted yet (SHARE_URLS in .env, password from the Keychain),
# 3. runs ops-check, keeping the Mac awake until it finishes.
# Output goes to logs/<date>.log.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
exec >>"logs/$(date +%F).log" 2>&1
echo "=== $(date '+%F %T') ops-check $*"

envval() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/'; }

# 1. network: up to 2 minutes
for _ in $(seq 1 24); do
  route -n get default >/dev/null 2>&1 && break
  sleep 5
done

# 2. shares: SHARE_URLS="smb://user@server/FTP_VESSEL_DATA smb://user@server/Raw_vessel_data2 ..."
for url in $(envval SHARE_URLS); do
  vol="/Volumes/${url##*/}"
  if [ ! -d "$vol" ]; then
    echo "mounting $url"
    osascript -e "mount volume \"$url\"" >/dev/null || echo "mount failed: $url"
  fi
done
for key in LANDING_DIR STAGING_DIR PROCESSED_DIR; do
  dir=$(envval "$key")
  if [ -n "$dir" ] && [ ! -d "$dir" ]; then echo "WARNING $key not reachable: $dir"; fi
done

# 3. run, with the Mac kept awake (-i) until ops-check exits
caffeinate -i .venv/bin/ops-check "$@"
rc=$?
echo "=== exit $rc"
exit $rc
