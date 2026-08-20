"""Registering and unregistering the Claude Code integration.

Two rules borrowed from the way the rest of this machine's tools install themselves: back
up any file before editing it, and make every step idempotent so running install twice is
harmless. A third rule is specific to rada: uninstalling must leave a machine that behaves
exactly as if rada had never been there, because a scheduler people cannot get rid of is a
scheduler people will not try.
"""
import json
import os
import shutil
import time

from . import __version__, store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE = os.path.expanduser("~/.claude")
SETTINGS = os.path.join(CLAUDE, "settings.json")
CONFIG = os.path.join(store.HOME, "config.json")

DEFAULT_HEAVY = """\
# One extended regular expression per line. A Bash command matching any of them is
# queued by rada instead of starting immediately. Lines starting with # are ignored by
# the reader only if you delete them; keep this file to patterns, one per line.
torch
transformers
tensorflow
jax
diffusers
llama
ollama
whisper
xcodebuild
cargo build
cargo test
swift build
gradle
ffmpeg
blender
docker build
docker compose
make -j
ninja
webpack
vite build
next build
npm run build
yarn build
pnpm build
pytest
unity
accelerate
deepspeed
vllm
"""


def wrapper_path():
    return os.path.join(REPO, "bin", "rada")


def gate_path():
    return os.path.join(REPO, "bin", "rada-gate")


def config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except Exception:
        return {}


def set_config(**kw):
    store.ensure_home()
    c = config()
    c.update(kw)
    with open(CONFIG, "w") as f:
        json.dump(c, f, indent=1)
    return c


def gating_enabled():
    return config().get("mode", "gate") == "gate"


def compile_patterns():
    """Join heavy.txt into the one-line alternation the shell stage reads.

    The gate runs before every Bash command in every session, so it may not fork to
    build its own pattern. Doing it here costs nothing, because installing happens once.
    """
    src = os.path.join(store.HOME, "heavy.txt")
    dst = os.path.join(store.HOME, "heavy.re")
    try:
        with open(src) as f:
            pats = [ln.strip() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")]
    except Exception:
        return None
    joined = "|".join(pats)
    try:
        with open(dst, "w") as f:
            f.write(joined + "\n")
    except Exception:
        return None
    return joined


def patterns_stale():
    src = os.path.join(store.HOME, "heavy.txt")
    dst = os.path.join(store.HOME, "heavy.re")
    try:
        return os.path.getmtime(src) > os.path.getmtime(dst)
    except OSError:
        return not os.path.exists(dst) and os.path.exists(src)


def _backup(path):
    if os.path.exists(path):
        b = f"{path}.rada-backup-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, b)
        return b
    return None


def _load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return None


def install():
    out = []
    store.ensure_home()
    out.append(f"rada {__version__}")

    heavy = os.path.join(store.HOME, "heavy.txt")
    if not os.path.exists(heavy):
        with open(heavy, "w") as f:
            f.write(DEFAULT_HEAVY)
        out.append(f"  wrote the heavy-command patterns to {heavy}")
    else:
        out.append(f"  kept your existing patterns in {heavy}")
    compile_patterns()
    out.append("  compiled them to heavy.re, which the hook reads without forking")

    for p in (wrapper_path(), gate_path(), os.path.join(REPO, "bin", "rada-gate.py")):
        try:
            os.chmod(p, 0o755)
        except OSError:
            pass

    os.makedirs(CLAUDE, exist_ok=True)
    s = _load_settings()
    if s is None:
        out.append(f"  ! {SETTINGS} is not valid JSON; not touching it")
        return out
    b = _backup(SETTINGS)
    if b:
        out.append(f"  backed up settings to {os.path.basename(b)}")

    hooks = s.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    # Drop any earlier rada entry so reinstalling does not stack them up.
    for group in list(pre):
        group["hooks"] = [h for h in group.get("hooks", [])
                          if not str(h.get("command", "")).endswith("rada-gate")]
        if not group["hooks"]:
            pre.remove(group)
    pre.append({"matcher": "Bash",
                "hooks": [{"type": "command", "command": gate_path(), "timeout": 10}]})
    with open(SETTINGS, "w") as f:
        json.dump(s, f, indent=2)
    out.append("  registered the PreToolUse hook for Bash")

    set_config(mode=config().get("mode", "gate"))
    out.append("")
    out.append("Heavy commands are now queued. Claude Code will ask permission the first")
    out.append("time it sees the wrapper, and the prompt shows the real command in a")
    out.append("comment. To stop being asked, add this to your permissions:")
    out.append("")
    out.append(f'    Bash({wrapper_path()} run:*)')
    out.append("")
    out.append("Be aware of what that rule means: it allows the wrapper to run whatever")
    out.append("command the hook saved for it, so a heavy command that your other rules")
    out.append("would have stopped will no longer be stopped. Add it only if your Bash")
    out.append("permissions are already broad. `rada mode advise` turns gating off and")
    out.append("leaves rada as a queue you invoke by hand with `rada run -- ...`.")
    return out


def uninstall():
    out = []
    s = _load_settings()
    if s is None:
        out.append(f"! {SETTINGS} is not valid JSON; not touching it")
    else:
        b = _backup(SETTINGS)
        if b:
            out.append(f"backed up settings to {os.path.basename(b)}")
        pre = (s.get("hooks") or {}).get("PreToolUse") or []
        for group in list(pre):
            group["hooks"] = [h for h in group.get("hooks", [])
                              if not str(h.get("command", "")).endswith("rada-gate")]
            if not group["hooks"]:
                pre.remove(group)
        if not pre:
            (s.get("hooks") or {}).pop("PreToolUse", None)
        if not s.get("hooks"):
            s.pop("hooks", None)
        with open(SETTINGS, "w") as f:
            json.dump(s, f, indent=2)
        out.append("removed the PreToolUse hook")
    out.append(f"your queue state and learned footprints are still in {store.HOME};")
    out.append(f"delete that directory to remove them too")
    return out


def doctor():
    out = [f"rada {__version__}", f"  repo         {REPO}"]
    ok = True

    for label, p in (("wrapper", wrapper_path()), ("gate", gate_path()),
                     ("gate stage 2", os.path.join(REPO, "bin", "rada-gate.py"))):
        good = os.path.isfile(p) and os.access(p, os.X_OK if label != "gate stage 2" else os.R_OK)
        ok &= good
        out.append(f"  {'v' if good else 'x'} {label:<12} {p}")

    s = _load_settings()
    if s is None:
        out.append(f"  x settings    {SETTINGS} is not valid JSON")
        ok = False
    else:
        hooked = any(str(h.get("command", "")).endswith("rada-gate")
                     for g in (s.get("hooks") or {}).get("PreToolUse", [])
                     for h in g.get("hooks", []))
        out.append(f"  {'v' if hooked else 'x'} hook         "
                   f"{'registered' if hooked else 'not registered, run `rada install`'}")
        ok &= hooked

    mode = config().get("mode", "gate")
    out.append(f"  - mode         {mode}"
               + ("" if mode == "gate" else "   (commands are not queued automatically)"))

    heavy = os.path.join(store.HOME, "heavy.txt")
    out.append(f"  {'v' if os.path.exists(heavy) else 'x'} patterns     {heavy}")
    if patterns_stale():
        out.append("  x compiled     heavy.txt changed since the last install; "
                   "run `rada install` to recompile it")
        ok = False

    if shutil.which("claude"):
        out.append(f"  v judge        claude found at {shutil.which('claude')}")
    else:
        out.append("  x judge        the claude command is not on PATH; "
                   "the queue will use arrival order only")

    try:
        # The same swept view `rada status` prints. Counting the raw file here reported
        # a berth belonging to a session that closed an hour ago as a running job, which
        # is the one number somebody checking an installation should not have to doubt.
        from rada.cli import swept
        d, left = swept(store.read())
        line = (f"  v state        {len(d['tickets'])} waiting, {len(d['leases'])} running,"
                f" {len(d.get('learn', {}))} learned")
        if left:
            line += f", {len(left)} left behind by sessions that have gone"
        out.append(line)
    except store.SchemaMismatch as e:
        out.append(f"  x state        {e}")
        ok = False
    except Exception as e:
        out.append(f"  x state        {e}")
        ok = False

    out.append("")
    out.append("all good" if ok else "something above needs attention")
    return out
