#!/bin/zsh
# Photographs the window in macapp/ against the demo queue, into docs/img.
#
# Nothing here is staged by hand. tools/schermate.py writes the same demo queue the
# README's terminal pictures are drawn from, this points the window at it, and the window
# saves a PNG of itself and quits. One description of the demo, two programs drawing it,
# so a change to either shows up in both after one command.
#
#     ./tools/schermate-app.sh
#
# The queue is written into a throwaway state directory, so your own ~/.rada is neither
# read nor touched.
set -euo pipefail

cd "$(dirname "$0")/.."
APP="$PWD/macapp/Rada.app"
OUT="$PWD/docs/img"
DEMO="${TMPDIR:-/tmp}/rada-demo-shots"

[[ -x "$APP/Contents/MacOS/Rada" ]] || { echo "==> building the app first"; macapp/build.sh >/dev/null }
mkdir -p "$OUT"
rm -rf "$DEMO"

# The demo state, and the machine it is meant to be running on.
eval "$(python3 tools/schermate.py --home "$DEMO")"
# The window would otherwise ask whichever rada is on PATH, which on a machine with an
# older one installed is a picture of a different program.
export RADA_BIN="$PWD/bin/rada"

# Through `open`, not by running the executable inside the bundle.
#
# Both work until they do not: after a few runs, the executable started by path came up
# with no window at all, sat in its event loop and had to be killed, while the same
# bundle opened by LaunchServices was fine. LaunchServices is how a Mac application is
# started, --env is how it is given an environment, and -W waits for it to quit.
#
# Dark, always. The system decides the appearance on its own schedule, and a set of
# pictures where two are light and two are dark is a set that has to be retaken.
shot() {
  local name=$1; shift
  echo "==> $name"
  open -W -n \
    --env "RADA_HOME=$RADA_HOME" \
    --env "RADA_BIN=$RADA_BIN" \
    --env "RADA_FAKE_BUDGET=$RADA_FAKE_BUDGET" \
    --env "RADA_FAKE_MEMORY=$RADA_FAKE_MEMORY" \
    -a "$APP" --args --shot "$OUT/$name.png" --appearance dark "$@"
}

shot 01-queue
shot 02-waiting     --select 7dc146f1
shot 03-held        --select c5e0d418
shot 04-force       --select 7dc146f1 --sheet force
shot 05-left-behind --select 0c77b41e

for f in "$OUT"/0*.png; do
  printf '    %s  %s\n' "$f" "$(du -h "$f" | cut -f1)"
done
