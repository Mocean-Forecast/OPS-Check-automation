#!/bin/bash
# Install (or remove) the four launchd jobs: 07:30 run, 07:45 watchdog, 11:00 recheck, 11:15 watchdog.
#   scripts/install-launchd.sh            install / reinstall
#   scripts/install-launchd.sh --remove   remove
# A job missed while the Mac sleeps runs as soon as it wakes. Waking it up is pmset's job (README).
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
mkdir -p "$AGENTS" "$REPO/logs"

for src in "$REPO"/deploy/launchd/*.plist; do
  name="$(basename "$src")"
  label="${name%.plist}"
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  rm -f "$AGENTS/$name"
  if [ "${1:-}" = "--remove" ]; then echo "removed $label"; continue; fi
  sed "s#__REPO__#$REPO#g" "$src" > "$AGENTS/$name"
  launchctl bootstrap "$DOMAIN" "$AGENTS/$name"
  echo "installed $label"
done
[ "${1:-}" = "--remove" ] || launchctl list | grep opscheck || true
