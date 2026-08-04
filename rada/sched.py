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

The other half of the problem is that a big job never fits under ordinary load. Priority
cannot rescue it, because the gate is a byte comparison. That is what reservation is for:
when the head of the queue does not fit, rada stops admitting anything that would eat
into the head's share and lets the machine drain. Reservation is granted by age alone,
never by the judge, because a reservation throttles the whole machine and that is too
much authority to hand to a model reading untrusted text.
"""
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
    if res.get("id") and res["id"] not in d["tickets"]:
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


def order(d, now=None):
    """The queue, most deserving first. Mandatory tickets lead, ordered by arrival."""
    now = now or time.time()
    tickets = d["tickets"]
    mandatory, rest = [], []
    for tid, tk in tickets.items():
        age = now - tk.get("enq", now)
        (mandatory if age >= _cfg("MANDATORY_AFTER", MANDATORY_AFTER) else rest).append(tid)
    mandatory.sort(key=lambda t: tickets[t].get("enq", 0))
    rest.sort(key=lambda t: (-(now - tickets[t].get("enq", now)) / _cfg("TAU", TAU)
                             - judge_bonus(d, t, now), tickets[t].get("enq", 0)))
    return mandatory + rest


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


def decide(d, tid, now=None):
    """Should ticket `tid` start right now?

    Returns a dict with `go` and, when it is False, a `why` written for a human plus the
    facts a caller needs to explain the wait. Mutates `d` only to record reservations.
    """
    now = now or time.time()
    tk = d["tickets"].get(tid)
    if tk is None:
        return {"go": True, "why": "ticket vanished, running ungated"}

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
