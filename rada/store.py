"""The shared state, and the lock that guards it.

Everything two sessions need to agree on lives in one JSON file under ~/.rada. There is
no daemon. A process that wants to change the state takes a lock directory, reads, edits,
writes atomically, and releases. Every read is cheap and every hold is measured in
milliseconds, so contention is not a problem even with twenty sessions.

Three rules this file exists to enforce:

1. A crashed holder must not wedge the machine. The lock records the holder's pid, and
   any process may break a lock whose holder is dead or whose age exceeds STALE.
2. An unrecognised schema version must not crash a job. Readers fail open: they report
   an empty state and the caller runs the job ungated. A scheduler that breaks jobs when
   it is confused is worse than no scheduler.
3. Writes are atomic. Write to a temporary file in the same directory, fsync, rename.
   A power cut leaves either the old state or the new one, never half of either.
"""
import errno
import json
import os
import time

from . import SCHEMA

HOME = os.path.expanduser(os.environ.get("RADA_HOME", "~/.rada"))
STATE = os.path.join(HOME, "state.json")
LOCK = os.path.join(HOME, "state.lock")
PENDING = os.path.join(HOME, "pending")
LOG = os.path.join(HOME, "rada.log")

STALE = 20.0          # a lock older than this is presumed abandoned
ACQUIRE_TIMEOUT = 15.0

EMPTY = {"v": SCHEMA, "tickets": {}, "leases": {}, "judge": {}, "learn": {}, "reserve": {}}


def ensure_home():
    for d in (HOME, PENDING):
        os.makedirs(d, exist_ok=True)


def log(msg):
    try:
        ensure_home()
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {msg}\n")
    except Exception:
        pass


def _read_raw():
    try:
        with open(STATE) as f:
            d = json.load(f)
    except FileNotFoundError:
        return dict(EMPTY)
    except Exception as e:
        log(f"state unreadable, starting empty: {e}")
        return dict(EMPTY)
    if not isinstance(d, dict) or d.get("v") != SCHEMA:
        # A different version of rada owns this file. Do not touch it and do not
        # pretend to schedule: the caller will run the job ungated.
        raise SchemaMismatch(d.get("v") if isinstance(d, dict) else None)
    for k, v in EMPTY.items():
        d.setdefault(k, v if not isinstance(v, dict) else {})
    return d


class SchemaMismatch(Exception):
    def __init__(self, found):
        self.found = found
        super().__init__(f"state written by schema {found}, this build speaks {SCHEMA}")


def read():
    """Read without locking. Good enough for status output and for fast-path checks."""
    try:
        return _read_raw()
    except SchemaMismatch:
        raise


def _write(d):
    ensure_home()
    tmp = STATE + f".tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(d, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE)


def _holder_dead(pid):
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except Exception:
        return True


def acquire(timeout=ACQUIRE_TIMEOUT):
    """Take the lock, or return False.

    The lock is a file whose contents are already correct when it appears. That matters
    more than it looks. An earlier version made a directory and then wrote the owner's
    pid inside it, which leaves a window where the lock exists but says nothing about who
    holds it. A second process reading it in that window sees no owner, concludes the
    owner is dead, breaks the lock and proceeds, and then two processes are both inside
    what they believe is mutual exclusion. That defect let two jobs start at once, which
    is the exact failure this whole program exists to prevent.

    os.link fails if the target exists, and it publishes a file that already has its
    contents, so there is no window to lose.
    """
    ensure_home()
    deadline = time.time() + timeout
    tmp = f"{LOCK}.{os.getpid()}"
    while True:
        try:
            with open(tmp, "w") as f:
                f.write(f"{os.getpid()} {time.time()}")
            try:
                os.link(tmp, LOCK)
                return True
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        except FileExistsError:
            pass
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

        pid, since = None, None
        try:
            with open(LOCK) as f:
                a, b = f.read().split()
                pid, since = int(a), float(b)
        except Exception:
            pass
        if since is None:
            try:
                since = os.path.getmtime(LOCK)
            except OSError:
                # it went away between the link and the read; try again at once
                continue
        age = time.time() - since
        # An unreadable owner is not evidence of death. Only a pid we could actually
        # read may be tested for liveness; anything else waits for the staleness clock.
        if age > STALE or (pid is not None and _holder_dead(pid)):
            log(f"breaking lock held by pid {pid} for {age:.1f}s")
            release()
            continue
        if time.time() > deadline:
            return False
        # jitter keyed on the pid, so twenty waiters do not retry in lockstep
        time.sleep(0.02 + (os.getpid() % 37) / 1000.0)


def release():
    try:
        os.remove(LOCK)
    except OSError:
        pass


class Transaction:
    """with store.Transaction() as st: ... mutate st.d ... ; it is written on exit.

    If the lock cannot be taken within the timeout, `st.ok` is False and the caller must
    treat the scheduler as unavailable and let the job run. Blocking a job because a lock
    is busy would turn a scheduler into an outage.
    """

    def __init__(self, timeout=ACQUIRE_TIMEOUT):
        self.timeout = timeout
        self.ok = False
        self.d = dict(EMPTY)
        self.mismatch = None

    def __enter__(self):
        if not acquire(self.timeout):
            log("could not acquire lock, proceeding unscheduled")
            return self
        try:
            self.d = _read_raw()
            self.ok = True
        except SchemaMismatch as e:
            self.mismatch = e
            release()
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.ok:
            return False
        try:
            if exc_type is None:
                self.d["v"] = SCHEMA
                _write(self.d)
        finally:
            release()
        return False


def pending_path(nonce):
    return os.path.join(PENDING, f"{nonce}.cmd")


def sweep_pending(max_age=86400):
    """Drop command files nobody claimed. A hook can write one and the tool call can be
    cancelled before the wrapper ever runs, so these accumulate."""
    now = time.time()
    try:
        names = os.listdir(PENDING)
    except OSError:
        return
    for n in names:
        p = os.path.join(PENDING, n)
        try:
            if now - os.path.getmtime(p) > max_age:
                os.remove(p)
        except OSError:
            pass
