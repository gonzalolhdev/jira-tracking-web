#!/bin/bash
# Regenerates JiraTrack.icns from make-icon.swift (only needed if you change the design).
set -euo pipefail
cd "$(dirname "$0")"
SET="$(mktemp -d)/JiraTrack.iconset"
mkdir -p "$SET"
swift make-icon.swift "$SET/master.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$SET/master.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$SET/master.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
rm "$SET/master.png"
iconutil -c icns "$SET" -o JiraTrack.icns
echo "Built $(pwd)/JiraTrack.icns"
