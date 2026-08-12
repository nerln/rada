"""rada's MCP server: the admission queue, in the tool list of every session.

JSON-RPC 2.0 over stdio, written by hand on the standard library, the same shape as
plancia's. Nothing to install means nothing that breaks when a dependency moves.

Why it exists, which is also why the tool descriptions are written the way they are. A
gate nobody knows about does nothing. The Bash hook catches heavy shell commands, but an
agent that is about to start something expensive some other way, an MCP tool that builds
a project inside its own server, a long compile it is about to launch, a model it is
about to load, has no way of learning that the queue exists unless something tells it. A
tool appears in the list on its own. So every description below has to say the thing the
reader does not know: that other sessions are running here, and that memory is shared.

Two differences from the CLI, both deliberate.

The wrapper runs the job; this does not. An agent has its own way of running commands,
and a tool that takes a command line and executes it would be a second shell with none of
the permission machinery around the first one. So this server hands out a berth and
trusts the caller to run its own work and give the berth back.

The wrapper blocks; this does not. `rada run` sits in a loop until it is admitted. A tool
call that blocks for ten minutes holds this server's only thread and tells the caller
nothing while it waits. So `rada_ask` answers at once, with the position and the reason,
and the caller asks again with the same ticket. The polling is the agent's turn-taking,
not a timer in here: between calls this process does nothing at all.

Everything else is the CLI's code. The ticket is built by cli.Waiter so its shape cannot
drift, the decision is sched.decide, the ordering is sched.order, the numbers are
mem.snapshot. This file decides nothing on its own.
"""

import argparse
import json
import os
import sys
import time
import traceback

from . import mem, sched, sessions, store
from .cli import Waiter, parse_bytes

PROTOCOL = "2025-06-18"
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}
try:
    from . import __version__ as VERSION
except Exception:
    VERSION = "0"

#: Tickets and berths this process is holding. Not shared state and not a source of
#: truth: it is only the list of what to give back if the session ends without a
#: rada_release. sched.reap covers the case where we are killed instead.
MINE = set()


def err(msg: str) -> None:
    print(f"[rada-mcp] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# tool definitions
# --------------------------------------------------------------------------

def _s(desc, **props):
    return {"type": "object", "properties": props, "additionalProperties": False,
            "description": desc}


STR = {"type": "string"}

TOOLS = [
    {
        "name": "rada_ask",
        "description": (
            "Ask for a berth before starting anything memory-heavy: a training or "
            "inference run, a large build, a simulator, a video encode, anything that "
            "will hold gigabytes for a while. Other Claude Code sessions are running on "
            "this machine and you cannot see them; rada is how the machine avoids "
            "starting three of these at once and swapping.\n"
            "Answers immediately, never blocks. Three possible answers: go, and the "
            "berth is yours until you call rada_release; wait, with your position in the "
            "queue and what is in front of you, in which case call rada_ask again with "
            "the same ticket; or refused, when the job cannot fit even after every other "
            "queued job has finished, because then waiting would only block everyone "
            "else.\n"
            "need is what the job will take ('6G', '512M'). Say it if you know it: a "
            "declared figure is admitted on a tighter margin than one rada guesses, and "
            "rada assumes 512M for a command it has never seen. This is the admission "
            "half of `rada run`; it does not start anything, you run your own job."),
        "inputSchema": _s(
            "",
            command={**STR, "description": "the command or job you are about to run; it "
                                           "identifies the job in the queue and keys the "
                                           "footprint rada learned from earlier runs"},
            need={**STR, "description": "memory it will need, e.g. 6G or 512M"},
            note={**STR, "description": "one line on what this is for, read by the judge "
                                        "that orders the queue"},
            ticket={**STR, "description": "re-check a ticket rada_ask already gave you, "
                                          "instead of taking a second place in the queue"},
            session={**STR, "description": "your Claude Code session id"},
            cwd={**STR, "description": "the directory the job runs in"}),
    },
    {
        "name": "rada_queue",
        "description": (
            "What is running, what is waiting, where you are in the line, and how much "
            "memory is actually free. Use it while you hold a ticket to see whether you "
            "are moving, and before asking a person to wait. A queue whose position you "
            "cannot see is a black box, and an agent that cannot see it will conclude "
            "rada is broken and go around it. Same view as `rada status`."),
        "inputSchema": _s("", session=STR,
                          ticket={**STR, "description": "point at one ticket of yours"}),
    },
    {
        "name": "rada_release",
        "description": (
            "Give the berth back, the moment your job is done or you have given up "
            "waiting. Until you do, rada keeps that memory promised to you and other "
            "sessions wait for it. This is the half of the bargain rada cannot do for "
            "you: it never saw your process, so it cannot tell that it exited. If this "
            "session ends without releasing, the berth is freed when the server stops."),
        "inputSchema": _s("", ticket=STR),
    },
]


# --------------------------------------------------------------------------
# what is not exposed, and why
#
# `rada run` is not here. It executes a command line, and an MCP tool that executes a
# command line is a second Bash with none of the permission rules of the first one. The
# agent already has a way to run things; what it was missing is the permission to start.
#
# `rada force` is not here either, and this is the one that matters. Forcing overrides
# the memory budget, and sched.py is explicit that a human override sits outside the
# fairness guarantee precisely because a person at the keyboard knows something the
# scheduler does not: that they are about to close an editor, that they would rather
# swap for two minutes. An agent that can force itself past the budget is not an
# override, it is a queue with an opt-out, and every waiting job would take it. When a
# job truly cannot fit, rada_ask says so and names the programs holding the memory, so
# the agent can tell the person, who can then force it.
#
# `rada reset` wipes the shared state of every session on the machine. `rada mode`,
# `install`, `uninstall` and `doctor` edit the installation. `rada judge` calls a model
# for debugging, and `rada watch` is an infinite redraw loop. None of them are things a
# session does to itself; they are things a person does to the tool.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

class BadInput(Exception):
    """Something the caller can fix, reported without a stack trace."""


def _fmt(data) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _sid(args):
    """The caller's session id, as it claims it.

    rada makes no trust decision on this string. It labels a ticket and names a
    heartbeat file, and sessions.note sanitises it before it touches a path. Nothing is
    granted because of who you say you are: the gate is a byte comparison against free
    memory, and the queue is ordered by how long you have waited.
    """
    s = args.get("session")
    return s if isinstance(s, str) else ""


def _need(args):
    v = args.get("need")
    if not v:
        return None
    try:
        return parse_bytes(v)
    except argparse.ArgumentTypeError as e:
        raise BadInput(str(e))


def _row(tid, entry, mine):
    """The fields a ticket and a lease have in common, named for someone reading them."""
    return {"ticket": tid, "size": mem.human(entry.get("need")),
            "project": entry.get("project") or "?",
            "command": (entry.get("show") or "")[:80], "yours": mine}


def _decide(tid):
    """Ask sched whether this ticket may go, and act on the answer in the same lock.

    Granting in a later transaction than the one that decided leaves a window where two
    callers have both been told to go, which is the single race that would make rada
    pointless. cli.Waiter.wait takes the berth inside the deciding transaction for that
    reason, and so does this.
    """
    with store.Transaction() as st:
        if not st.ok:
            return {"answer": "go", "why": "scheduler unavailable, go ahead ungated"}
        sched.reap(st.d)
        if tid in st.d["leases"]:
            return {"answer": "go", "ticket": tid,
                    "why": "you already hold this berth",
                    "release_with": f"rada_release ticket={tid}"}
        tk = st.d["tickets"].get(tid)
        if tk is not None:
            # The waiter is alive and still interested. cli.Waiter.wait writes the same
            # field on every pass of its loop.
            tk["seen"] = time.time()
        d = sched.decide(st.d, tid)
        facts = d.get("facts") or {}
        if d.get("go"):
            sched.grant(st.d, tid, os.getpid(), None)
            MINE.add(tid)
            return {"answer": "go", "ticket": tid, "why": d.get("why", "admitted"),
                    "release_with": f"rada_release ticket={tid}"}
        if d.get("impossible_for_now"):
            # Refuse rather than hold a place nobody can ever reach. A ticket at the head
            # of the queue that cannot fit even after everything else drains is not
            # waiting for a berth, it is waiting for a person to close an application,
            # and while it waits it is in everyone else's way.
            st.d["tickets"].pop(tid, None)
            MINE.discard(tid)
            return {"answer": "refused", "why": d.get("why"),
                    "held_by": [{"name": b["name"], "size": mem.human(b["bytes"])}
                                for b in (facts.get("blockers") or [])],
                    "what_to_do": ("this cannot fit while those programs hold the "
                                   "memory. Tell the person: they can close something, "
                                   "or run `rada force` themselves. Your ticket has been "
                                   "dropped, so nobody is queued behind you.")}
        MINE.add(tid)
        return {"answer": "wait", "ticket": tid, "why": d.get("why"),
                "position": facts.get("pos"), "of": facts.get("queued"),
                "free": mem.human(facts.get("free")),
                "need": mem.human(facts.get("need")),
                "waiting_seconds": int(facts.get("age") or 0),
                "check_again": f"rada_ask ticket={tid}",
                "give_up_with": f"rada_release ticket={tid}"}


def call_tool(name: str, args: dict) -> str:
    if name == "rada_ask":
        sid = _sid(args)
        # A session asking for a berth is a live session, and this is the signal the
        # gate reads to decide whether queueing is worth it at all: with one session
        # open rada stands aside. Recording it here is what stops this server's jobs
        # from being invisible to everybody else's.
        sessions.note(sid)

        tid = (args.get("ticket") or "").strip()
        if tid:
            try:
                d = store.read()
            except Exception:
                d = {}
            if tid in (d.get("tickets") or {}) or tid in (d.get("leases") or {}):
                return _fmt(_decide(tid))
            # Reaped, released, or never ours. Fall through and take a fresh place
            # rather than answering about a ticket that no longer exists.
            tid = ""

        cmd = (args.get("command") or "").strip()
        if not cmd:
            raise BadInput("say what you are about to run: `command` names the job in "
                           "the queue and keys what rada learned about it")
        w = Waiter(cmd, need=_need(args), note=args.get("note"),
                   cwd=args.get("cwd") or os.getcwd(), sid=sid)
        try:
            if not w.enqueue():
                return _fmt({"answer": "go",
                             "why": "scheduler unavailable, go ahead ungated"})
        except store.SchemaMismatch as e:
            return _fmt({"answer": "go", "why": f"{e}; go ahead ungated"})
        return _fmt(_decide(w.tid))

    if name == "rada_queue":
        sid = _sid(args)
        mine_ticket = (args.get("ticket") or "").strip()
        try:
            d = store.read()
        except store.SchemaMismatch as e:
            return (f"state written by another version of rada ({e.found}); rada is "
                    "standing aside and jobs run ungated")
        now = time.time()
        snap = mem.snapshot()
        com = sched.committed(d)

        def ours(tid, entry):
            return tid in MINE or tid == mine_ticket or (
                bool(sid) and entry.get("sid") == sid)

        running = [dict(_row(tid, ls, ours(tid, ls)),
                        running_seconds=int(now - ls.get("start", now)))
                   for tid, ls in sorted(d["leases"].items(),
                                         key=lambda kv: kv[1].get("start", 0))]

        q = sched.order(d, now)
        waiting = []
        for i, tid in enumerate(q, 1):
            tk = d["tickets"][tid]
            row = _row(tid, tk, ours(tid, tk))
            row["position"] = i
            row["waiting_seconds"] = int(now - tk.get("enq", now))
            # Past MANDATORY_AFTER a ticket is ordered by arrival alone and the judge
            # cannot touch it. That is the promise worth showing, not an internal flag.
            if now - tk.get("enq", now) >= sched.MANDATORY_AFTER:
                row["ordered_by_arrival_now"] = True
            if tk.get("force"):
                row["forced_by_a_person"] = True
            waiting.append(row)

        yours = [r for r in waiting if r["yours"]]
        held = [r for r in running if r["yours"]]
        if yours:
            you = "; ".join(f"ticket {r['ticket']} is {r['position']} of {len(waiting)}, "
                            f"waiting {r['waiting_seconds']}s" for r in yours)
        elif held:
            you = "; ".join(f"you hold berth {r['ticket']} ({r['size']}), release it with "
                            f"rada_release when the job is done" for r in held)
        else:
            you = "nothing of yours is queued or running"

        out = {
            "you": you,
            "memory": {
                "budget": mem.human(snap["budget"]),
                "promised_to_running_jobs": mem.human(com),
                "free_for_a_new_job": mem.human(snap["budget"] - com),
                "machine_used": f"{mem.human(snap['used'])} of {mem.human(mem.TOTAL)}",
                "swap": f"{mem.human(snap['swap_used'])} of "
                        f"{mem.human(snap['swap_total'])}",
                "clamped": snap["clamped"],
            },
            "running": running,
            "waiting": waiting,
        }
        try:
            out["sessions"] = sessions.describe(sid)
        except Exception:
            pass
        j = d.get("judge") or {}
        if j.get("ts"):
            out["judge"] = f"{int(now - j['ts'])}s ago: {j.get('why') or '(no reason)'}"
        return _fmt(out)

    if name == "rada_release":
        tid = (args.get("ticket") or "").strip()
        if not tid:
            raise BadInput("which berth: pass the ticket rada_ask gave you")
        with store.Transaction() as st:
            if not st.ok:
                return ("could not take the lock, so nothing was released yet; the berth "
                        "is freed when this session's server stops")
            had = tid in st.d["leases"] or tid in st.d["tickets"]
            # peak=0 on purpose. finish() records what a job really cost so the next
            # estimate is not a guess, and this server never saw the process: it cannot
            # measure a peak, and a made-up number would poison the estimate for every
            # session that runs the same command afterwards. So the lease is dropped and
            # nothing is learned.
            sched.finish(st.d, tid, 0, 0.0)
            st.d["tickets"].pop(tid, None)
        MINE.discard(tid)
        return (f"{tid} released" if had else
                f"{tid} was not held by anyone; nothing to release")

    raise BadInput(f"unknown tool: {name}")


def release_all():
    """Give back whatever this process still holds. Called when stdin closes.

    Not a timer and not a background thread: it runs once, on the way out. If we are
    killed instead, sched.reap frees these the moment any other rada notices the pid is
    gone, which is the same path a crashed `rada run` takes.
    """
    if not MINE:
        return
    try:
        with store.Transaction(timeout=3.0) as st:
            if not st.ok:
                return
            for tid in list(MINE):
                sched.finish(st.d, tid, 0, 0.0)
                st.d["tickets"].pop(tid, None)
    except Exception:
        pass
    MINE.clear()


# --------------------------------------------------------------------------
# JSON-RPC loop
# --------------------------------------------------------------------------

def respond(rid, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req: dict) -> None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if rid is None:  # notification: no answer
        return

    if method == "initialize":
        asked = params.get("protocolVersion")
        respond(rid, {
            "protocolVersion": asked if asked in SUPPORTED else PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "rada", "version": VERSION},
            "instructions": (
                "Other Claude Code sessions share this machine's memory with you. Before "
                "starting anything that will hold gigabytes, call rada_ask and wait for "
                "a berth, then rada_release when the job is done. Ordinary work needs "
                "none of this."),
        })
    elif method == "ping":
        respond(rid, {})
    elif method == "tools/list":
        respond(rid, {"tools": TOOLS})
    elif method == "resources/list":
        respond(rid, {"resources": []})
    elif method == "resources/templates/list":
        respond(rid, {"resourceTemplates": []})
    elif method == "prompts/list":
        respond(rid, {"prompts": []})
    elif method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            text = call_tool(name, args)
            respond(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except BadInput as exc:
            respond(rid, {"content": [{"type": "text", "text": f"Error: {exc}"}],
                          "isError": True})
        except Exception as exc:
            # Anything that goes wrong in rada lets the work happen. A gate that blocks a
            # job because it is confused is worse than no gate, and that rule does not
            # stop applying because the caller is a model instead of a shell.
            err(traceback.format_exc())
            respond(rid, {"content": [{"type": "text", "text": (
                f"rada failed ({exc}); it is not gating anything, go ahead")}],
                "isError": True})
    else:
        respond(rid, error={"code": -32601, "message": f"unhandled method: {method}"})


def main() -> int:
    store.ensure_home()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except Exception:
                continue
            try:
                if isinstance(req, list):
                    for item in req:
                        handle(item)
                else:
                    handle(req)
            except Exception:
                err(traceback.format_exc())
    finally:
        release_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
