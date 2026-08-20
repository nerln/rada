#!/usr/bin/env python3
"""Render the README's terminal pictures from real output.

Nothing here is typed by hand. The script builds a demo queue in a throwaway state
directory, runs the real commands against it, and draws what they printed. Rerun it
after changing any output and the pictures follow.

    python3 tools/schermate.py

Writes docs/status.svg and docs/doctor.svg.

    python3 tools/schermate.py --home /tmp/rada-demo

writes the same demo queue into a state directory, prints the environment that goes with
it, and stops. That is how the window in macapp/ is photographed by tools/schermate-app.sh:
one description of the demo, and two programs drawing it.
"""
import html
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
RADA = os.path.join(ROOT, "bin", "rada")

CH_W, LINE_H, PAD, TOP = 8.05, 19.0, 22, 44

# A queue that shows every state worth showing: something running, something mandatory
# that has waited, a reservation, and a judge verdict with its reason.
# pid 1 rather than this process: the state written here is read again minutes later by
# the window, and sched.reap drops a ticket whose owner has gone. launchd is always
# there, and nothing in a picture is ever asked to run.
DEMO_PID = 1
# Above the kernel's maximum, so it is not a process and never will be.
DEAD_PID = 99999

NOW = 1785900000.0
DEMO = {
    "v": 1,
    "leases": {
        "a41f9c02": {"need": 2 * 1024**3, "peak": 1932735283, "pid": DEMO_PID,
                     "pgid": None, "start": NOW - 214, "sig": "s1",
                     "project": "aidirector", "show": "ffmpeg -i lecture.mov -vf scale=1280:-2 out.mp4"},
        # A berth nobody gave back. The session that took it closed while the job was
        # running, so the process is gone and the file still says two gigabytes are
        # spoken for. It is in the demo because it is the state that is hardest to
        # recognise on a real machine, and the one that makes the queue look busy when
        # it is not.
        "0c77b41e": {"need": 2 * 1024**3, "peak": 0, "pid": DEAD_PID, "pgid": None,
                     "start": NOW - 5400, "seen": NOW - 3120, "sig": "s6",
                     "project": "kart-highlights",
                     "show": "ffmpeg -i race.mov -c:v h264_videotoolbox cut.mp4"},
    },
    "tickets": {
        "7dc146f1": {"sid": "9c41f8e2", "enq": NOW - 1840, "need": 6 * 1024**3, "pid": DEMO_PID,
                     "sig": "s2", "project": "mechint", "cwd": "/Users/x/mechint",
                     "show": "python3 exp22_jspace_null.py",
                     "intent": "the null control a paper is blocked on"},
        "3b90ae55": {"sid": "2ad07b56", "enq": NOW - 260, "need": 4 * 1024**3, "pid": DEMO_PID,
                     "sig": "s3", "project": "OliveraXR3", "cwd": "/Users/x/OliveraXR3",
                     "show": "xcodebuild -scheme Olivera -destination 'generic/platform=iOS'"},
        "91c23663": {"sid": "9c41f8e2", "enq": NOW - 41, "need": 512 * 1024**2, "pid": DEMO_PID,
                     "sig": "s4", "project": "molo", "cwd": "/Users/x/molo",
                     "show": "pytest tests/ -q"},
        "c5e0d418": {"sid": "e73b1904", "enq": NOW - 620, "need": 3 * 1024**3, "pid": DEMO_PID,
                     "sig": "s5", "project": "vesuvius", "cwd": "/Users/x/vesuvius",
                     "show": "python3 reindex.py --all --workers 8",
                     "hold": {"since": NOW - 300, "note": "not while the disk is full"}},
    },
    "judge": {"ts": NOW - 22, "order": ["7dc146f1", "91c23663", "3b90ae55"],
              "why": "the experiment has waited half an hour and blocks a paper; "
                     "the test run is quick; the build is not being watched"},
    "learn": {"s1": {"n": 4, "p95": 1932735283, "max": 2040109465, "dur_p95": 240.0},
              "s2": {"n": 1, "p95": 6012954214, "max": 6012954214, "dur_p95": 700.0},
              "s4": {"n": 9, "p95": 402653184, "max": 447741952, "dur_p95": 31.0}},
    "reserve": {"id": "7dc146f1", "since": NOW - 120, "fails": 0},
}


# The machine the demo runs on is stated rather than measured. Whoever regenerates these
# pictures is usually doing it on the machine that needed rada in the first place, and a
# budget of nine gigabytes over a line saying the kernel is at pressure two describes no
# machine at all. Nine gigabytes against 4.2 in use on a sixteen gigabyte laptop is what
# the arithmetic in mem.py gives, so the two lines agree with each other.
MACHINE = {"used": int(4.2 * 1024 ** 3), "pressure": 1, "jetsam": 78,
           "swap_used": int(0.4 * 1024 ** 3), "swap_total": 6 * 1024 ** 3, "clamped": []}
BUDGET = "9G"


def demo_env(home):
    return {"RADA_HOME": home, "RADA_FAKE_BUDGET": BUDGET,
            "RADA_FAKE_MEMORY": json.dumps(MACHINE)}


def capture(args, home, env_extra=None):
    env = dict(os.environ, RADA_HOME=home, RADA_DISABLE="1")
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, RADA] + args, capture_output=True, text=True,
                       env=env)
    return (p.stdout + p.stderr).rstrip("\n")


def svg(lines, title):
    """Draw monospaced lines on a dark card. One colour rule: anything rada emphasises."""
    width = max(len(l) for l in lines + [title]) + 4
    w = int(width * CH_W) + 2 * PAD
    h = TOP + int(len(lines) * LINE_H) + PAD
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="ui-monospace,SFMono-Regular,Menlo,'
        f'DejaVu Sans Mono,monospace" font-size="13">',
        f'<rect width="{w}" height="{h}" rx="10" fill="#12161c"/>',
        '<circle cx="24" cy="22" r="5.5" fill="#ff5f57"/>',
        '<circle cx="42" cy="22" r="5.5" fill="#febc2e"/>',
        '<circle cx="60" cy="22" r="5.5" fill="#28c840"/>',
        f'<text x="{w/2}" y="27" fill="#7d8590" text-anchor="middle" '
        f'font-size="12">{html.escape(title)}</text>',
    ]
    for i, line in enumerate(lines):
        y = TOP + int(i * LINE_H) + 13
        colour = "#c9d1d9"
        if line.strip().startswith("!") or " ! " in line:
            colour = "#f0883e"
        elif line.strip().startswith("v "):
            colour = "#3fb950"
        elif line.strip().startswith("x "):
            colour = "#f85149"
        elif line.startswith("rada ") or line.strip().startswith(("running", "waiting")):
            colour = "#58a6ff"
        elif line.strip().startswith("judge"):
            colour = "#a371f7"
        out.append(f'<text x="{PAD}" y="{y}" fill="{colour}" '
                   f'xml:space="preserve">{html.escape(line)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def write_home(home):
    """Put the demo queue in a state directory and return it.

    The timestamps are written relative to now, so the ages in the picture are the ages
    the story describes rather than whatever today is.
    """
    os.makedirs(os.path.join(home, "pending"), exist_ok=True)
    shift = time.time() - NOW
    d = json.loads(json.dumps(DEMO))
    for t in d["tickets"].values():
        t["enq"] += shift
        if t.get("hold"):
            t["hold"]["since"] += shift
    for l in d["leases"].values():
        l["start"] += shift
        if l.get("seen"):
            l["seen"] += shift
    d["judge"]["ts"] += shift
    d["reserve"]["since"] += shift
    with open(os.path.join(home, "state.json"), "w") as f:
        json.dump(d, f)
    return home


def main():
    if "--home" in sys.argv:
        home = write_home(os.path.expanduser(sys.argv[sys.argv.index("--home") + 1]))
        for k, v in demo_env(home).items():
            print(f"export {k}={shlex.quote(v)}")
        return

    os.makedirs(DOCS, exist_ok=True)
    home = write_home(tempfile.mkdtemp(prefix="rada-shot-"))

    status = capture(["status"], home, demo_env(home))
    with open(os.path.join(DOCS, "status.svg"), "w") as f:
        f.write(svg(status.split("\n"), "rada status"))

    with open(os.path.join(home, "heavy.txt"), "w") as f:
        f.write("torch\nffmpeg\nxcodebuild\npytest\n")
    subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0, %r); "
                    "import os; os.environ['RADA_HOME']=%r; "
                    "from rada.setup_claude import compile_patterns; compile_patterns()"
                    % (ROOT, home)], check=False)
    doctor = capture(["doctor"], home)
    doctor = doctor.replace(os.path.expanduser("~"), "~").replace(home, "~/.rada")
    with open(os.path.join(DOCS, "doctor.svg"), "w") as f:
        f.write(svg(doctor.split("\n"), "rada doctor"))

    for n in ("status.svg", "doctor.svg"):
        p = os.path.join(DOCS, n)
        print(f"  {p}  {os.path.getsize(p)} bytes")


if __name__ == "__main__":
    main()
