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

# Signed with whatever identity this machine has, and ad-hoc only as a last resort.
#
# Ad-hoc is enough to launch, and it was enough for a year. It is not enough for the
# notification about a stuck job: an ad-hoc bundle has no team identifier, so
# UNUserNotificationCenter answers "denied" at once, the app never appears in System
# Settings under Notifications, and the banner is simply never shown. Measured on this
# machine, which is why there is a fallback in rada/cli.py that goes back to osascript.
#
# RADA_SIGN_IDENTITY names an identity; otherwise the first Apple Development one on the
# machine is used, which is what Xcode would have done. No entitlements, because the app
# asks the system for nothing beyond this.
IDENTITY="${RADA_SIGN_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
  IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
             | awk -F'"' '/Apple Development|Developer ID Application/ {print $2; exit}')
fi
if [[ -n "$IDENTITY" ]]; then
  echo "==> signing as $IDENTITY"
  codesign --force --deep --timestamp=none --sign "$IDENTITY" "$APP" 2>/dev/null || {
      echo "   (that identity would not sign; falling back to ad-hoc)"
      codesign --force --deep --sign - "$APP" 2>/dev/null || true
  }
else
  echo "==> no signing identity on this machine, signing ad-hoc"
  echo "    the window works; the notification about a stuck job will not be shown,"
  echo "    and rada falls back to an osascript banner for it"
  codesign --force --deep --sign - "$APP" 2>/dev/null || \
      echo "   (signing failed: the app still starts if you right-click > Open the first time)"
fi

echo "==> done: $(pwd)/$APP"
echo "    open it with:  open $APP"
