"""The admission rule, and the fairness guarantee that the judge is not allowed to break.

The shape of the problem. Several Claude Code sessions, each unaware of the others, want
to start jobs that will not fit in memory together. Something has to order them. Ordering
by arrival is fair and stupid: it will run a two-second linter ahead of a training run
that has been waiting since breakfast, and it has no idea that one session is blocked on
a deadline while another is doing housekeeping. Ordering by a language model is
context-aware and unaccountable: a model that is wrong ten times in a row breaks no rule
while one project never runs.

So the ordering is split in two, and only one half can be wrong.

    score(t) = age(t) / TAU + judge_bonus(t),   judge_bonus in [0, CAP]

The judge moves a ticket by at most CAP points. Age earns one point every TAU seconds.
Two consequences, both provable and both tested in tools/prova.py:

  Lemma 1, the overtaking window. A ticket enqueued D seconds after another can never
  outrank it once D > CAP * TAU, whatever the judge says. With the defaults that is 90
  seconds. The judge can reorder recent arrivals; it cannot reorder history.

  Lemma 2, the mandatory set. Once a ticket has waited MANDATORY_AFTER seconds it enters
  a set that is ordered strictly by arrival time and from which the judge is excluded
  entirely. The mandatory set is always served first. A ticket that enters it is
  therefore preceded only by tickets that entered it earlier, a set that cannot grow
  after the fact.

Lemma 2 is the guarantee the user asked for, and it is stated in completions rather than
in seconds, deliberately. A wall-clock bound would be a lie: a job holding memory can run
for as long as it likes, and no scheduler that refuses to kill user processes can promise
otherwise. What rada promises is that a waiting job is passed by a bounded number of
other jobs, and that the bound is fixed the moment the job becomes mandatory.

Both lemmas are about the judge, which is the part of rada that can be wrong or be
talked into being wrong. A person is not in that category. `force` starts a job the
budget refuses and `hold` keeps a job from starting at all, and neither is bounded by
anything above: someone at the keyboard knows the editor is about to close, or that the
job they are looking at is the wrong one. What rada owes them in exchange is that both
are visible wherever the queue is, and that a held ticket keeps ageing while it waits,
so lifting a hold does not send the job to the back of the queue it never left.

The other half of the problem is that a big job never fits under ordinary load. Priority
cannot rescue it, because the gate is a byte comparison. That is what reservation is for:
when the head of the queue does not fit, rada stops admitting anything that would eat
into the head's share and lets the machine drain. Reservation is granted by age alone,
never by the judge, because a reservation throttles the whole machine and that is too
much authority to hand to a model reading untrusted text.
"""
import copy
import os
import time

from . import mem

TAU = 30.0                 # seconds of waiting worth one point of priority
CAP = 3.0                  # the most the judge may add, in points
MANDATORY_AFTER = 600.0    # after ten minutes, age alone decides
HEADROOM = 1.30            # admit only if the estimate times this fits
RESERVE_MAX = 420.0        # how long a reservation may throttle the machine
RESERVE_BACKOFF = 120.0    # first cooldown after a failed reservation, then doubling
BACKFILL_MAX_SECONDS = 90.0   # a job may slip under a reservation only if it is short
DEFAULT_NEED = 512 * 1024 ** 2
TICKET_GRACE = 15.0        # a ticket whose waiter has vanished survives this long


def _cfg(name, default):
    v = os.environ.get("RADA_" + name)
    if v is None:
        return default
    try:
        return type(default)(v)
    except Exception:
        return default


def reap(d, now=None):
    """Drop leases and tickets whose owning process is gone. Returns what was dropped."""
    now = now or time.time()
    gone = []
    for tid, ls in list(d["leases"].items()):
        if not mem.alive(ls.get("pid")):
            gone.append(("lease", tid))
            d["leases"].pop(tid, None)
    for tid, tk in list(d["tickets"].items()):
        if not mem.alive(tk.get("pid")) and now - tk.get("seen", tk.get("enq", now)) > TICKET_GRACE:
            gone.append(("ticket", tid))
            d["tickets"].pop(tid, None)
    res = d.get("reserve") or {}
    # A reservation belonging to a ticket a person has since held is a reservation for
    # a job that is not coming, and everything behind it would wait for it.
    if res.get("id") and (res["id"] not in d["tickets"]
                          or held(d["tickets"][res["id"]])):
        d["reserve"] = {}
    return gone


def committed(d):
    """Memory promised to running jobs that they have not allocated yet.

    A lease's bytes are counted twice if we are not careful: once in the reservation and
    again in the kernel's used figure the moment the job touches the memory. So what is
    still outstanding is the estimate minus what the process group has actually taken.
    """
    total = 0
    for ls in d["leases"].values():
        need = ls.get("need", 0)
        taken = 0
        pgid = ls.get("pgid")
        if pgid:
            taken = mem.group_footprint(pgid)
        total += max(0, need - taken)
    return total


def judge_bonus(d, tid, now=None):
    """Points the judge has assigned to this ticket, clamped, and expiring with the verdict."""
    j = d.get("judge") or {}
    order = j.get("order") or []
    if tid not in order:
        return 0.0
    age = (now or time.time()) - j.get("ts", 0)
    if age > _cfg("JUDGE_TTL", 180.0):
        return 0.0
    # first in the verdict gets CAP, last gets 0, linearly in between
    if len(order) < 2:
        return CAP
    rank = order.index(tid)
    return CAP * (1.0 - rank / (len(order) - 1))


def held(tk):
    """Has a person told this ticket to stay where it is?"""
    return bool((tk or {}).get("hold"))


def hold_why(tk):
    note = ((tk or {}).get("hold") or {}).get("note")
    base = "held by you: it keeps its place in the queue and does not start"
    return f"{base}, {note}" if note else base


def order(d, now=None):
    """The queue, most deserving first. Mandatory tickets lead, ordered by arrival.

    Held tickets come last, whatever their age. A ticket a person has held is one that
    is not going to start, and leaving it at the head means it reserves memory nobody
    is going to use and everything behind it waits for a job that was never coming. It
    keeps ageing where it sits, so releasing a hold returns the ticket to the position
    its arrival time earns rather than to the back.
    """
    now = now or time.time()
    tickets = d["tickets"]
    mandatory, rest, waiting_on_a_person = [], [], []
    for tid, tk in tickets.items():
        if held(tk):
            waiting_on_a_person.append(tid)
            continue
        age = now - tk.get("enq", now)
        (mandatory if age >= _cfg("MANDATORY_AFTER", MANDATORY_AFTER) else rest).append(tid)
    mandatory.sort(key=lambda t: tickets[t].get("enq", 0))
    rest.sort(key=lambda t: (-(now - tickets[t].get("enq", now)) / _cfg("TAU", TAU)
                             - judge_bonus(d, t, now), tickets[t].get("enq", 0)))
    waiting_on_a_person.sort(key=lambda t: tickets[t].get("enq", 0))
    return mandatory + rest + waiting_on_a_person


DECLARED_HEADROOM = 1.10


def _fits(need, free, declared=False):
    """Admit only with a margin over the estimate.

    The margin is wider for a number rada guessed than for one a person typed. A learned
    footprint is a peak observed at some sampling rate on some earlier run, and the true
    peak between samples is always a little higher; a declared number is what the person
    running the job believes it takes, and doubling their margin on top of their own is
    how a job that would have fitted ends up waiting for room it did not need.
    """
    return need * (DECLARED_HEADROOM if declared else HEADROOM) <= free


STUCK_AFTER = 300.0        # a job stuck this long is worth telling a person about


def forced_ready(d, tk, now):
    """Has a person told this job to go, and is its condition met?

    A forced job ignores the memory budget and the queue order. That is the point: the
    person at the keyboard knows something the scheduler does not, usually that they are
    about to close an editor, or that they would rather swap for two minutes than wait
    another hour. The fairness guarantee in this file is about the decisions rada makes
    on its own; a human override is outside it, and `rada status` says when one is in
    force so it is never a mystery why something jumped.
    """
    f = tk.get("force")
    if not f:
        return False, None
    after = f.get("after")
    if after:
        if after in d["leases"] or after in d["tickets"]:
            return False, f"forced, waiting for {after} to finish first"
        return True, "forced by you, and the job it waits for has finished"
    at = f.get("at", 0)
    if at > now:
        return False, f"forced, starting in {int(at - now)}s"
    return True, "forced by you"


def decide(d, tid, now=None):
    """Should ticket `tid` start right now?

    Returns a dict with `go` and, when it is False, a `why` written for a human plus the
    facts a caller needs to explain the wait. Mutates `d` only to record reservations.
    """
    now = now or time.time()
    tk = d["tickets"].get(tid)
    if tk is None:
        return {"go": True, "why": "ticket vanished, running ungated"}

    if held(tk):
        # No amount of free memory revokes this, and neither does the queue emptying.
        # It is answered before the budget is even read, because reading the budget
        # here would produce a sentence about memory for a job whose problem is not
        # memory.
        q = order(d, now)
        return {"go": False, "held": True, "why": hold_why(tk),
                "facts": {"pos": (q.index(tid) + 1) if tid in q else 1,
                          "queued": len(q), "age": now - tk.get("enq", now)}}

    ready, note = forced_ready(d, tk, now)
    if ready:
        return {"go": True, "why": note, "forced": True}
    if note:
        return {"go": False, "why": note, "forced": True, "facts": {"pos": 1, "queued": len(d["tickets"])}}

    snap = mem.snapshot()
    if snap.get("unknown_platform"):
        return {"go": True, "why": "memory accounting unavailable on this platform"}

    free = snap["budget"] - committed(d)
    need = tk.get("need") or DEFAULT_NEED
    declared = bool(tk.get("declared"))
    q = order(d, now)
    pos = q.index(tid) if tid in q else 0
    head = q[0] if q else tid
    res = d.get("reserve") or {}

    facts = {"pos": pos + 1, "queued": len(q), "free": free, "need": need,
             "budget": snap["budget"], "committed": committed(d),
             "clamped": snap["clamped"], "running": len(d["leases"]),
             "age": now - tk.get("enq", now)}

    # A reservation in cooldown is not in force.
    if res.get("until", 0) > now:
        res = {}

    if tid == head:
        if _fits(need, free, declared):
            d["reserve"] = {}
            return {"go": True, "why": "head of queue and it fits", "facts": facts}

        # Before reserving, ask whether reserving could possibly work. A reservation
        # only frees memory that rada itself handed out. If the job would not fit even
        # after every rada job on the machine had finished, then the memory is held by
        # something rada does not manage, an editor, a simulator, the browser, and
        # holding the queue shut waits for something that is not coming while everyone
        # else is blocked. This happened: a six gigabyte experiment reserved for three
        # and a half hours on a machine whose other eleven gigabytes belonged to open
        # applications, and eight unrelated jobs from other sessions queued behind it.
        drainable = snap["budget"] + committed(d)
        if not _fits(need, drainable, declared):
            d["reserve"] = {}
            facts["blockers"] = mem.top_consumers(4)
            facts["drainable"] = drainable
            facts["stuck"] = (now - tk.get("enq", now)) > STUCK_AFTER
            return {"go": False, "impossible_for_now": True, "facts": facts,
                    "why": (f"needs {mem.human(need)} and at most {mem.human(drainable)} "
                            f"could be freed by waiting for other queued jobs, so the "
                            f"rest is held by programs outside the queue; waiting "
                            f"without holding anyone else back")}

        # The head does not fit, but draining could get it there. Hold the machine open.
        if not res or res.get("id") != tid:
            d["reserve"] = {"id": tid, "since": now, "fails": (res.get("fails", 0))}
            res = d["reserve"]
        waited = now - res.get("since", now)
        if waited > _cfg("RESERVE_MAX", RESERVE_MAX):
            fails = res.get("fails", 0) + 1
            cool = RESERVE_BACKOFF * (2 ** min(fails - 1, 4))
            d["reserve"] = {"id": None, "until": now + cool, "fails": fails}
            facts["blockers"] = mem.top_consumers(4)
            facts["cooldown"] = cool
            return {"go": False, "defer": True, "facts": facts,
                    "why": (f"needs {mem.human(need)} but only {mem.human(free)} can be "
                            f"freed; giving up the reservation for {int(cool)}s so other "
                            f"jobs can run, then trying again")}
        facts["reserving"] = True
        return {"go": False, "facts": facts,
                "why": (f"first in the queue, waiting for {mem.human(need)}; "
                        f"{mem.human(free)} free so far")}

    # Not the head.
    if res.get("id") and res.get("id") != tid:
        head_need = d["tickets"].get(res["id"], {}).get("need", 0)
        short = (d.get("learn", {}).get(tk.get("sig", ""), {}).get("dur_p95", 1e9)
                 <= BACKFILL_MAX_SECONDS)
        if _fits(need, free - head_need, declared) and short:
            return {"go": True, "why": "short job, fits underneath the reservation",
                    "facts": facts}
        return {"go": False, "facts": facts,
                "why": (f"holding memory for a larger job that has waited "
                        f"{int(now - d['tickets'].get(res['id'], {}).get('enq', now))}s")}

    if _fits(need, free, declared):
        return {"go": True, "why": "fits and nothing is reserving", "facts": facts}
    return {"go": False, "facts": facts,
            "why": (f"needs {mem.human(need)}, only {mem.human(free)} free"
                    + (f"; {'; '.join(snap['clamped'])}" if snap["clamped"] else ""))}


def plan(d, now=None):
    """Walk the whole queue and say what would happen, in order, right now.

    `decide` answers about one ticket against the machine as it stands. Somebody looking
    at the queue is asking a different question: which of these start, given that the
    ones ahead start first and take their memory with them. Answering that by calling
    `decide` on each ticket in turn gives the wrong picture, because every ticket is told
    about the same free memory and three jobs that each fit on their own are all reported
    as starting when only one of them will.

    So the walk happens on a copy, and every ticket the copy admits becomes a running job
    in that copy before the next one is asked about. Returns a list of (id, decision) in
    queue order. The real state is not touched, which also means the reservations that
    `decide` writes while it reasons are thrown away with the copy.
    """
    now = now or time.time()
    work = copy.deepcopy(d)
    out = []
    for tid in order(work, now):
        d2 = decide(work, tid, now)
        out.append((tid, d2))
        if d2.get("go"):
            tk = work["tickets"].pop(tid, {})
            # Deliberately not grant(): that writes to the log and takes a pid, and
            # this job is imaginary.
            work["leases"][tid] = {"need": tk.get("need", DEFAULT_NEED), "pid": None,
                                   "pgid": None, "start": now,
                                   "sig": tk.get("sig", ""),
                                   "project": tk.get("project", ""),
                                   "show": tk.get("show", "")}
    return out


def grant(d, tid, pid, pgid, now=None):
    now = now or time.time()
    tk = d["tickets"].pop(tid, {})
    from . import store
    store.log(f"granted {tid} need={mem.human(tk.get('need'))} "
              f"from_ticket={bool(tk)} pid={pid}")
    d["leases"][tid] = {"need": tk.get("need", DEFAULT_NEED), "pid": pid, "pgid": pgid,
                        "start": now, "sig": tk.get("sig", ""), "cwd": tk.get("cwd", ""),
                        "project": tk.get("project", ""), "show": tk.get("show", "")}
    if (d.get("reserve") or {}).get("id") == tid:
        d["reserve"] = {}


def finish(d, tid, peak, seconds):
    """Record what the job really cost, so the next estimate is not a guess."""
    ls = d["leases"].pop(tid, None)
    if not ls:
        return
    sig = ls.get("sig") or ""
    if not sig or not peak:
        return
    e = d["learn"].setdefault(sig, {"n": 0, "p95": 0, "max": 0, "dur_p95": 0})
    e["n"] += 1
    # A cheap running high-water estimate: keep the max, and let p95 drift towards it.
    e["max"] = max(e.get("max", 0), int(peak))
    e["p95"] = int(max(peak, e.get("p95", 0) * 0.9))
    e["dur_p95"] = max(float(seconds), e.get("dur_p95", 0) * 0.9)


def estimate(d, sig, declared=None):
    """Bytes to assume this job will take."""
    if declared:
        return int(declared)
    e = (d.get("learn") or {}).get(sig)
    if e and e.get("p95"):
        return int(e["p95"])
    return DEFAULT_NEED
