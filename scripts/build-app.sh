#!/bin/bash
# Builds "Jira Track.app" (a Dock-able launcher) that runs scripts/launch.sh.
# Usage: scripts/build-app.sh [destination-dir]   (default: ~/Applications)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Applications}"
APP="$DEST/Jira Track.app"

mkdir -p "$DEST"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"'$ROOT/scripts/launch.sh' >> \\\"\$TMPDIR/jira-track-launcher.log\\\" 2>&1 &\""
# Swap the default applet icon for ours.
cp "$ROOT/scripts/icon/JiraTrack.icns" "$APP/Contents/Resources/applet.icns"
# Modern macOS prefers the compiled asset catalog over applet.icns, so drop it.
rm -f "$APP/Contents/Resources/Assets.car"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true
codesign --force --deep --sign - "$APP" >/dev/null 2>&1
touch "$APP"
echo "Built: $APP"
