#!/bin/zsh
# Builds Resources/Rada.icns from the same mark the site uses.
#
# Generated rather than committed as a binary, for the reason the rest of this repository
# is text: docs/img/mark.svg can be read and reviewed, an .icns cannot. Run it when the
# mark changes.
set -euo pipefail

cd "$(dirname "$0")"
WORK="$(mktemp -d)"
ICONSET="$WORK/Rada.iconset"
mkdir -p Resources

echo "==> drawing the tiles"
swiftc -O -parse-as-library Tools/make-icon.swift -o "$WORK/make-icon"
"$WORK/make-icon" "$ICONSET"

echo "==> packing"
iconutil -c icns "$ICONSET" -o Resources/Rada.icns
echo "==> Resources/Rada.icns ($(du -h Resources/Rada.icns | cut -f1))"
