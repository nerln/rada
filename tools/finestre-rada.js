// Samples Rada's on-screen windows every tenth of a second for N seconds.
// osascript -l JavaScript tools/finestre-rada.js 4
// Needs no permission: CGWindowListCopyWindowInfo gives owners and bounds without
// Screen Recording. Used on 18/11 to catch a window shown for a second and a half by a
// launch that was meant to show nothing; see "L'app non si apre da una sessione" in
// CLAUDE.md.
ObjC.import('CoreGraphics');
ObjC.import('Foundation');
function run(argv) {
  const secs = parseFloat(argv[0] || '5');
  const steps = Math.round(secs * 10);
  const seen = {};
  let maxWin = 0, firstWin = null, frames = 0;
  for (let i = 0; i < steps; i++) {
    const info = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly, 0))) || [];
    const big = info.filter(w => w.kCGWindowOwnerName === 'Rada' && w.kCGWindowLayer === 0 && w.kCGWindowBounds.Height > 100);
    if (big.length > maxWin) maxWin = big.length; if (big.length) frames++;
    if (big.length && firstWin === null) firstWin = (i / 10).toFixed(1);
    info.filter(w => w.kCGWindowOwnerName === 'Rada').forEach(w => seen[w.kCGWindowOwnerPID] = true);
    delay(0.1);
  }
  return JSON.stringify({framesWithWindow: frames, maxOnscreenWindows: maxWin, firstWindowAt: firstWin, pidsSeen: Object.keys(seen)});
}
