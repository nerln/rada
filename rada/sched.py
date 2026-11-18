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

There is one case where none of this arithmetic applies, and it is the commonest case of
all: rada is running nothing. Then there is no queue to be fair about and no memory of
rada's to protect, and a job refused for not fitting a budget is a job refused on behalf
of nobody. The head of the queue goes, and what still stops it is the kernel's own
opinion rather than rada's. See `kernel_says_stop` and the branch that uses it.

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

ALONE = 1                  # the rule below is on unless RADA_ALONE=0
ALONE_SWAP = 4             # standing aside, a job may ask for what is free plus a
                           # TOTAL/this allowance of paging. Four gigabytes on a sixteen
                           # gigabyte laptop.
ALONE_WATCH = 120.0        # how long what one of those jobs promised is held against
                           # the next one, while its memory has yet to appear
ALONE_GAP = 5.0            # seconds between two admissions that ignored the budget.
                           # Short on purpose: the bar above shrinks by itself as the
                           # first job's memory reaches `used`, so this only has to
                           # cover the moment before any of it has been allocated. Five
                           # jobs were admitted in the same second without it; thirty
                           # seconds of it made a second real job wait for nothing.
CRITICAL_PRESSURE = 4      # the kernel's own word for trouble. 2 is a warning it raises
                           # routinely on a machine whose compressor is doing its job, and
                           # the same 16 GB laptop at the same load returned 2 sixty times
                           # in a row and then 1 seventy-five times in a row.
JETSAM_FLOOR = 25          # kern.memorystatus_level under this and the kernel's own
                           # killer is the next thing to happen
DISK_FLOOR = 5 * 1024 ** 3  # swap files are made against free disk. With less than this
                            # left there is nowhere to page to, and paging that cannot
                            # happen is not paging, it is a stall.


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


def outstanding_alone(d, snap, now):
    """What the last job admitted without asking the budget promised and has not taken yet.

    A berth would have covered this, and a berth is exactly what is missing: the wrapper
    exits when its child does, and a job that puts itself in the background exits its
    child at once. Measured that way, five jobs declared at twelve gigabytes were admitted
    one after another, because none of them had allocated anything yet and the machine
    still looked empty. Waiting a few seconds between admissions does not fix it either,
    since a checkpoint load takes longer than a few seconds to reach the resident set.

    So what was promised is remembered, and whatever has since appeared in `used` is taken
    off it. Every promise inside the window, not only the last one: kept in a single slot
    it bounded two admissions and the third was measured against a bar that had forgotten
    the first. Each is forgotten after ALONE_WATCH, because a promise nobody ever
    collected must not hold the queue shut.
    """
    live = [a for a in (d.get("alone") or {}).values()
            if isinstance(a, dict) and a.get("at")
            and now - a["at"] <= _cfg("ALONE_WATCH", ALONE_WATCH)]
    if not live:
        return 0
    # One baseline for all of them, and one subtraction. Taking each promise's own delta
    # counts the same growth once per promise, so two jobs that promised six gigabytes
    # each were both written off the moment the first one's six appeared.
    promised = sum(a.get("need", 0) for a in live)
    appeared = max(0, snap.get("used", 0) - min(a.get("used", 0) for a in live))
    return max(0, promised - appeared)


def alone_bar(snap, d=None, now=None):
    """The most a job may ask for while rada is standing aside, in bytes.

    What is free right now, plus an allowance of paging. Both halves are needed.

    Free alone would be the budget again, and refusing a four gigabyte job on a laptop
    with three free is the complaint this rule was written to answer: macOS pages, and a
    job that has the machine to itself is what paging is for.

    The allowance alone, or a bar drawn against the size of the machine, is how the rule
    was wrong for an afternoon. It read "the machine minus what the kernel has wired",
    which took no account of the thirteen gigabytes already resident: on this laptop it
    admitted a job declared at twelve, twenty-six gigabytes of demand on sixteen
    gigabytes of memory. Measured, not reasoned about.

    So the bar moves with the machine, and it moves twice over. The resident set takes
    from it directly. The allowance shrinks as swap approaches the depth that stops the
    rule altogether, so that stop is a slope rather than a cliff a job falls off: on a
    machine with seven gigabytes already paged out, the allowance is what is left before
    half the machine is on disk, and not a fixed quarter of it.
    """
    free_now = max(0, mem.TOTAL - snap.get("used", 0))
    before_deep = max(0, mem.TOTAL // 2 - snap.get("swap_used", 0))
    allowance = min(mem.TOTAL // max(1, _cfg("ALONE_SWAP", ALONE_SWAP)), before_deep)
    bar = free_now + allowance
    if d is not None:
        bar -= outstanding_alone(d, snap, now if now is not None else time.time())
    return max(0, bar)


def oldest(d, now=None):
    """The ticket that has waited longest and could actually start, by arrival alone.

    Tickets a person has stopped are skipped, and so are tickets forced to a time that
    has not come. Without that, a job held at nine in the morning was still the oldest at
    noon, and since it is answered by its own branch long before this one, nothing else
    in the queue could be the oldest either: one held ticket turned the whole rule off
    for everybody. `order()` demotes held tickets for the same reason.
    """
    now = now or time.time()
    tickets = d.get("tickets") or {}
    live = []
    for tid, tk in tickets.items():
        if held(tk):
            continue
        if tk.get("force"):
            ready, _ = forced_ready(d, tk, now)
            if not ready:
                continue
        live.append(tid)
    if not live:
        return None
    return min(live, key=lambda t: tickets[t].get("enq", 0))


def stands_aside(d, snap):
    """Is rada about to hold the next job back? Returns (no, why).

    Both views ask this so that the window and the terminal cannot describe a rule the
    scheduler is not following. It used to be worked out again in cli.py, where it
    forgot the switch: with RADA_ALONE=0 the status line still promised that the next
    job would start straight away, and `decide` refused it.
    """
    if not _cfg("ALONE", ALONE):
        return False, "the budget decides every job, since RADA_ALONE is 0"
    running = len(d.get("leases") or {})
    if running:
        return False, (f"one job is running, so the budget decides the next one"
                       if running == 1 else
                       f"{running} jobs are running, so the budget decides the next one")
    trouble = kernel_says_stop(snap)
    if trouble:
        return False, f"nothing is running under rada, but {trouble}"
    return True, (f"nothing is running under rada, so the next job up to "
                  f"{mem.human(alone_bar(snap, d))} starts straight away, whatever the "
                  f"budget says")


def kernel_says_stop(snap):
    """The machine's own opinion of itself, as opposed to rada's arithmetic.

    Three readings, each of them the kernel speaking rather than a number rada worked out
    from a budget it invented: the pressure level at its top value, the percentage the
    kernel's own killer watches, and swap grown deep enough to rival the memory of the
    machine. Returns the reason to stop, or None.
    """
    if snap.get("unknown_platform"):
        return None
    if snap.get("pressure", 1) >= _cfg("CRITICAL_PRESSURE", CRITICAL_PRESSURE):
        return "the kernel reports critical memory pressure"
    pct = snap.get("jetsam", 100)
    if pct < _cfg("JETSAM_FLOOR", JETSAM_FLOOR):
        return f"the kernel considers only {pct}% of memory free"
    if snap.get("swap_deep"):
        return f"swap is already {mem.human(snap.get('swap_used', 0))} deep"
    free_disk = snap.get("disk_free")
    if free_disk is not None and free_disk < _cfg("DISK_FLOOR", DISK_FLOOR):
        return (f"only {mem.human(free_disk)} is left on the disk swap is made from")
    return None


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

    # A reservation in cooldown is not in force. `res` is the working copy and `stored`
    # is what is on disk, and the two have to stay distinct: emptying the working copy
    # used to be all that happened, and then the head reached the branch below, saw no
    # reservation and took a fresh one on its very next poll. The cooldown was meant to
    # give everybody else two minutes of machine and gave them one poll, three seconds.
    # It also read the failure count out of the emptied copy, so the doubling backoff
    # restarted at its first step every time.
    stored = res
    cooling = stored.get("until", 0) > now
    if cooling:
        res = {}

    # Alone, a job does not need a queue.
    #
    # Measured on a 16 GB laptop with an empty queue: a job declared at 4 GB was told to
    # wait for memory held by a browser, a music player and two editors. rada had already
    # worked out that no queued job was ever going to hand that memory back, printed
    # exactly that, and waited anyway. Waiting buys something only while rada is holding
    # memory it will give back; holding none, it was refusing a job on behalf of nobody,
    # and the same command with rada uninstalled would have started.
    #
    # So with no berth out, the oldest ticket is measured against `alone_bar` instead of
    # the budget, and what still stops it is the kernel's own opinion.
    #
    # The oldest by arrival, not the head. The head is where the judge's three points
    # land, and this admission is more authority than a reservation: it switches the byte
    # comparison off rather than reordering who faces it. The file's own docstring says a
    # reservation is too much authority to hand to a model reading untrusted text, so
    # this one is granted the way reservations are, by age alone.
    #
    # `not d["leases"]` is what bounds it to one job at a time, and it is only true
    # because the decision and the berth are taken in the same transaction. It says rada
    # is running nothing, which is not the same as the machine being idle: a session on
    # its own skips the gate entirely and those jobs take no ticket. They are counted
    # where it matters, in `used`, which is the machine's own number and is what the bar
    # is drawn against. The gap below covers the seconds before a new job's memory shows
    # up there: five jobs declared at twelve gigabytes were admitted one after another
    # in the same second, because each backgrounded itself, the berth ended with the
    # shell that started it, and nothing had allocated anything yet.
    alone_last = max([a.get("at", 0) for a in (d.get("alone") or {}).values()
                      if isinstance(a, dict)] or [0])
    if (_cfg("ALONE", ALONE) and not d["leases"] and tid == oldest(d, now)
            and now - alone_last >= _cfg("ALONE_GAP", ALONE_GAP)):
        trouble = kernel_says_stop(snap)
        bar = alone_bar(snap, d, now)
        # The same margin the budget charges. A learned footprint is a peak somebody
        # sampled, and the true peak between samples is always a little higher; standing
        # aside is a reason to be generous about the number, not to stop reading it.
        if trouble is None and _fits(need, bar, declared):
            d["reserve"] = {}
            facts["alone"] = True
            facts["alone_bar"] = bar
            fitted = _fits(need, free, declared)
            return {"go": True, "alone": True, "facts": facts,
                    "why": ("nothing else is running and it fits" if fitted else
                            f"nothing is running under rada, so there is nobody to wait "
                            f"for, and {mem.human(need)} fits in the {mem.human(bar)} "
                            f"that is free or can be paged out for it")}
        facts["alone_refused"] = trouble or (
            f"it wants {mem.human(need)} and only {mem.human(bar)} is free or can be "
            f"paged out for it")
    elif _cfg("ALONE", ALONE) and not d["leases"] and now - alone_last < _cfg("ALONE_GAP", ALONE_GAP):
        facts["alone_refused"] = (
            f"another job started {int(now - alone_last)}s ago without asking the budget, "
            f"and rada waits to see what it takes")

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
            # Only an actual reservation is given up here. Emptying the whole slot threw
            # away a cooldown that was still in force, which is the defect the cooling
            # branch below exists to prevent, reached through another door.
            if (d.get("reserve") or {}).get("id"):
                d["reserve"] = {}
            facts["blockers"] = mem.top_consumers(4)
            facts["drainable"] = drainable
            facts["stuck"] = (now - tk.get("enq", now)) > STUCK_AFTER
            stopped = facts.get("alone_refused")
            return {"go": False, "impossible_for_now": True, "facts": facts,
                    "why": (f"needs {mem.human(need)} and at most {mem.human(drainable)} "
                            f"could be freed by waiting for other queued jobs, so the "
                            f"rest is held by programs outside the queue; waiting "
                            f"without holding anyone else back"
                            + (f". It would have gone anyway with nothing else running, "
                               f"but {stopped}" if stopped else ""))}

        # The head does not fit, but draining could get it there. Hold the machine open,
        # unless it has just given up and promised everybody else a turn.
        if cooling:
            left = stored["until"] - now
            facts["cooldown_left"] = left
            return {"go": False, "facts": facts,
                    "why": (f"needs {mem.human(need)}; the machine is not being held "
                            f"open for it for another {int(left)}s, so other jobs "
                            f"can run first")}
        if not res or res.get("id") != tid:
            inherited = stored.get("fails", 0) if stored.get("blame") == tid else 0
            d["reserve"] = {"id": tid, "since": now, "fails": inherited}
            res = d["reserve"]
        waited = now - res.get("since", now)
        if waited > _cfg("RESERVE_MAX", RESERVE_MAX):
            fails = res.get("fails", 0) + 1
            cool = RESERVE_BACKOFF * (2 ** min(fails - 1, 4))
            # Whose failure it was. Without a name on it, the next ticket to reach the
            # head inherited both the wait and the failure count, and started its own
            # first reservation already four failures deep.
            d["reserve"] = {"id": None, "until": now + cool, "fails": fails, "blame": tid}
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
        # The same floor decide() charges the reserving ticket itself. Read as 0 it
        # protected the reservation with nothing, so a backfilling job could take the
        # share being held.
        head_need = d["tickets"].get(res["id"], {}).get("need") or DEFAULT_NEED
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
            work["leases"][tid] = {"need": tk.get("need") or DEFAULT_NEED, "pid": None,
                                   "pgid": None, "start": now,
                                   "sig": tk.get("sig", ""),
                                   "project": tk.get("project", ""),
                                   "show": tk.get("show", "")}
    return out


def grant(d, tid, pid, pgid, now=None, alone=False):
    now = now or time.time()
    tk = d["tickets"].pop(tid, {})
    from . import store
    store.log(f"granted {tid} need={mem.human(tk.get('need'))} "
              f"from_ticket={bool(tk)} pid={pid}")
    d["leases"][tid] = {"need": tk.get("need") or DEFAULT_NEED, "pid": pid, "pgid": pgid,
                        "start": now, "sig": tk.get("sig", ""), "cwd": tk.get("cwd", ""),
                        "project": tk.get("project", ""), "show": tk.get("show", ""),
                        "sid": tk.get("sid", "")}
    if (d.get("reserve") or {}).get("id") == tid:
        d["reserve"] = {}
    # When the berth was given without asking the budget, remember when. The next one is
    # made to wait a moment so that what this job takes has time to reach `used`, which
    # is what the bar is drawn against. Recorded here rather than in decide() because
    # decide() also answers --dry-run, and a question should not spend the allowance.
    if alone:
        promises = d.get("alone")
        if not isinstance(promises, dict):
            promises = {}
        # Drop the ones nobody is waiting on any more, so the slot cannot grow without
        # bound, and keep the rest: one slot bounded two admissions and forgot the first.
        promises = {k: v for k, v in promises.items()
                    if isinstance(v, dict) and now - v.get("at", 0) <= ALONE_WATCH}
        promises[tid] = {"at": now, "need": tk.get("need") or DEFAULT_NEED,
                         "used": mem.snapshot().get("used", 0)}
        d["alone"] = promises


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
