#!/bin/zsh
# Builds Rada.app.
#
# No .xcodeproj: SwiftPM produces the executable and the bundle is assembled around it
# here. Less elegant than a project file, and in exchange everything stays in git as text
# and it rebuilds from a terminal without opening Xcode.
set -euo pipefail

cd "$(dirname "$0")"
APP="Rada.app"
CONF="${1:-release}"

echo "==> building ($CONF)"
swift build -c "$CONF" --disable-sandbox

BIN="$(swift build -c "$CONF" --show-bin-path)/Rada"
[[ -x "$BIN" ]] || { echo "executable not found: $BIN"; exit 1 }

echo "==> assembling the bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Rada"
cp Resources/Info.plist "$APP/Contents/Info.plist"
# The icon is the same mark as the site, generated rather than committed: a clone has
# the drawing and the code that renders it, not a binary nobody can review.
[[ -f Resources/Rada.icns ]] || ./make-icon.sh >/dev/null
cp Resources/Rada.icns "$APP/Contents/Resources/Rada.icns"
printf 'APPL????' > "$APP/Contents/PkgInfo"

# Ad-hoc signature: enough to run locally. Without it, recent macOS refuses to launch an
# unsigned bundle even when you compiled it yourself. No entitlements, because the app
# asks the system for nothing: it runs `rada` and draws what it answers.
codesign --force --deep --sign - "$APP" 2>/dev/null || \
    echo "   (signing failed: the app still starts if you right-click > Open the first time)"

echo "==> done: $(pwd)/$APP"
echo "    open it with:  open $APP"
