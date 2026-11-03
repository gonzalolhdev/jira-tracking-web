#!/bin/bash
# Regenerates JiraTrack.icns and JiraTrackCold.icns from make-icon.swift
# (only needed if you change the design).
set -euo pipefail
cd "$(dirname "$0")"

make_icns() {
  local out="$1" variant="${2:-}"
  local set; set="$(mktemp -d)/$(basename "$out" .icns).iconset"
  mkdir -p "$set"
  swift make-icon.swift "$set/master.png" $variant
  for s in 16 32 128 256 512; do
    sips -z $s $s "$set/master.png" --out "$set/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$set/master.png" --out "$set/icon_${s}x${s}@2x.png" >/dev/null
  done
  rm "$set/master.png"
  iconutil -c icns "$set" -o "$out"
  echo "Built $(pwd)/$out"
}

make_icns JiraTrack.icns
make_icns JiraTrackCold.icns cold
