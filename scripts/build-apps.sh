#!/bin/bash
# Builds the Dock-able launcher apps: "<APP_NAME>.app" (hot) and "<APP_NAME> (cold).app" (cold).
# Usage: scripts/build-apps.sh [destination-dir]   (default: ~/Applications)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/launcher.conf"
DEST="${1:-$HOME/Applications}"
mkdir -p "$DEST"

build_app() {
  local name="$1" mode="$2" icon="$3"
  local app="$DEST/$name.app"
  rm -rf "$app"
  osacompile -o "$app" -e "do shell script \"'$ROOT/scripts/launch.sh' $mode >> \\\"\$TMPDIR/$APP_SLUG-launcher.log\\\" 2>&1 &\""
  cp "$ROOT/scripts/icon/$icon" "$app/Contents/Resources/applet.icns"
  # Modern macOS prefers the compiled asset catalog over applet.icns, so drop it.
  rm -f "$app/Contents/Resources/Assets.car"
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$app/Contents/Info.plist" 2>/dev/null || true
  codesign --force --deep --sign - "$app" >/dev/null 2>&1
  touch "$app"
  echo "Built: $app"
}

build_app "$APP_NAME" hot "$ICON_HOT"
build_app "$APP_NAME (cold)" cold "$ICON_COLD"
