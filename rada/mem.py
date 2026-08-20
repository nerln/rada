"""Memory accounting for macOS on Apple Silicon. Standard library only, no root.

Why this file is longer than a call to psutil: on a unified-memory Mac the number that
looks like "available memory" is not the number a scheduler may spend. Page cache counts
as available and is not; the compressor holds real RAM; Metal allocations by a PyTorch
job land in ordinary anonymous memory and nothing in the graphics stack will refuse an
allocation that will not fit. The one number worth trusting is

    total - reserve - (wired + compressor + uncompressed anonymous)

which is what Activity Monitor calls Memory Used, plus a reserve, plus three hard stops
read from the kernel's own view of pressure.

One portability note that is easy to get wrong: the page size on Apple Silicon is 16384,
not 4096. Hardcoding 4096 gives numbers four times too small, which looks plausible.
"""
import ctypes
import ctypes.util
import json
import os
import re
import subprocess
import time

GiB = 1024 ** 3

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.mach_host_self.restype = ctypes.c_uint

_HOST_VM_INFO64 = 4
PAGE = os.sysconf("SC_PAGE_SIZE")


class _VMStat(ctypes.Structure):
    _fields_ = (
        [(n, ctypes.c_uint) for n in
         ("free_count", "active_count", "inactive_count", "wire_count")] +
        [(n, ctypes.c_ulonglong) for n in
         ("zero_fill_count", "reactivations", "pageins", "pageouts", "faults",
          "cow_faults", "lookups", "hits", "purges")] +
        [("purgeable_count", ctypes.c_uint), ("speculative_count", ctypes.c_uint)] +
        [(n, ctypes.c_ulonglong) for n in
         ("decompressions", "compressions", "swapins", "swapouts")] +
        [("compressor_page_count", ctypes.c_uint), ("throttled_count", ctypes.c_uint),
         ("external_page_count", ctypes.c_uint), ("internal_page_count", ctypes.c_uint),
         ("total_uncompressed_pages_in_compressor", ctypes.c_ulonglong)])


def _sysctl_int(name, default=None):
    try:
        return int(subprocess.check_output(["sysctl", "-n", name],
                                           stderr=subprocess.DEVNULL).strip())
    except Exception:
        if default is None:
            raise
        return default


try:
    TOTAL = _sysctl_int("hw.memsize")
except Exception:
    TOTAL = 8 * GiB


def vm_stats():
    s = _VMStat()
    count = ctypes.c_uint(ctypes.sizeof(_VMStat) // 4)
    if _libc.host_statistics64(_libc.mach_host_self(), _HOST_VM_INFO64,
                               ctypes.byref(s), ctypes.byref(count)) != 0:
        raise OSError("host_statistics64 failed")
    return s


def swap():
    try:
        text = subprocess.check_output(["sysctl", "-n", "vm.swapusage"], text=True)
        n = {k: float(v) for k, v in re.findall(r"(\w+) = ([\d.]+)M", text)}
        return {k: int(v * 1024 * 1024) for k, v in n.items()}
    except Exception:
        return {"total": 0, "used": 0, "free": 0}


def pressure():
    """1 NORMAL, 2 WARN, 4 CRITICAL. Same values as dispatch's memorypressure flags."""
    return _sysctl_int("kern.memorystatus_vm_pressure_level", 1)


def jetsam_pct():
    """kern.memorystatus_level: the percentage the kernel's own killer considers free."""
    return _sysctl_int("kern.memorystatus_level", 100)


def _forced_budget():
    """RADA_FAKE_BUDGET pins the budget, in bytes.

    It exists so the test suite can create contention without allocating gigabytes, and
    it is useful by hand: setting it to a small number is the honest way to see how the
    queue behaves on a machine smaller than yours before telling someone it works.
    """
    v = os.environ.get("RADA_FAKE_BUDGET")
    if v is None:
        return None
    m = re.fullmatch(r"\s*([\d.]+)\s*([kKmMgGtT]?)[bB]?\s*", v)
    if not m:
        return None
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
    return int(float(m.group(1)) * mult[m.group(2).lower()])


def _forced_snapshot(snap):
    """RADA_FAKE_MEMORY overrides fields of a snapshot, given as a JSON object.

    It exists for the pictures under docs/, which are made by running the real commands
    and drawing what they printed. The machine that regenerates them is usually the
    machine that needed rada in the first place, and a picture whose budget line says
    three gigabytes are free while the line under it says the kernel is at pressure two
    is a picture of two different machines. Setting the numbers here keeps the sentences
    in the picture the ones rada actually writes.

    Also the quick way to reproduce a report: paste the numbers somebody sent and read
    the same output they were reading.
    """
    raw = os.environ.get("RADA_FAKE_MEMORY")
    if not raw:
        return snap
    try:
        over = json.loads(raw)
    except Exception:
        return snap
    if isinstance(over, dict):
        snap.update({k: v for k, v in over.items() if k in snap})
    return snap


def snapshot(reserve_frac=0.15, reserve_floor=1536 * 1024 ** 2):
    """Everything the scheduler needs about memory, in one cheap call.

    Returns a dict. `budget` is the number of bytes that may be handed to new jobs.
    `clamped` lists the reasons the budget was forced down, so a waiting job can be told
    why it is waiting instead of just being told to wait.
    """
    try:
        s = vm_stats()
    except Exception:
        # Not macOS, or the call failed. Fail open: report the machine as roomy rather
        # than blocking every job on a platform this module does not understand.
        forced = _forced_budget()
        return _forced_snapshot(
            {"budget": TOTAL if forced is None else forced, "used": 0, "reserve": 0,
             "pressure": 1, "jetsam": 100, "swap_used": 0, "swap_total": 0,
             "clamped": [], "unknown_platform": forced is None})

    sw = swap()
    lvl, pct = pressure(), jetsam_pct()
    wired = s.wire_count * PAGE
    comp = s.compressor_page_count * PAGE
    anon = s.internal_page_count * PAGE
    used = wired + comp + anon
    reserve = max(int(TOTAL * reserve_frac), reserve_floor)
    budget = max(0, TOTAL - reserve - used)

    clamped = []
    if lvl != 1:
        budget = 0
        clamped.append(f"kernel memory pressure level {lvl}, not normal")
    if pct < 25:
        budget = 0
        clamped.append(f"kernel considers only {pct}% free, under the 25% floor")
    if sw["total"] and sw["used"] / sw["total"] > 0.75:
        budget = min(budget, TOTAL // 16)
        clamped.append(f"swap {sw['used']/GiB:.1f} of {sw['total']/GiB:.1f} GiB, over 75% full")

    forced = _forced_budget()
    if forced is not None:
        budget = forced
        clamped = [f"budget pinned to {human(forced)} by RADA_FAKE_BUDGET"]

    return _forced_snapshot(
        {"budget": budget, "used": used, "reserve": reserve, "wired": wired,
         "compressor": comp, "anon": anon, "filebacked": s.external_page_count * PAGE,
         "pressure": lvl, "jetsam": pct, "swap_used": sw["used"],
         "swap_total": sw["total"], "clamped": clamped, "unknown_platform": False})


def available():
    return snapshot()["budget"]


# ---------------------------------------------------------------- per-process footprint

_libc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_libc.proc_pid_rusage.restype = ctypes.c_int
_U = ctypes.c_uint64
_RU_NAMES = """ri_user_time ri_system_time ri_pkg_idle_wkups ri_interrupt_wkups ri_pageins
ri_wired_size ri_resident_size ri_phys_footprint ri_proc_start_abstime ri_proc_exit_abstime
ri_child_user_time ri_child_system_time ri_child_pkg_idle_wkups ri_child_interrupt_wkups
ri_child_pageins ri_child_elapsed_abstime ri_diskio_bytesread ri_diskio_byteswritten
ri_cpu_time_qos_default ri_cpu_time_qos_maintenance ri_cpu_time_qos_background
ri_cpu_time_qos_utility ri_cpu_time_qos_legacy ri_cpu_time_qos_user_initiated
ri_cpu_time_qos_user_interactive ri_billed_system_time ri_serviced_system_time
ri_logical_writes ri_lifetime_max_phys_footprint ri_instructions ri_cycles ri_billed_energy
ri_serviced_energy ri_interval_max_phys_footprint ri_runnable_time ri_flags""".split()


class _RUsage(ctypes.Structure):
    _fields_ = ([("ri_uuid", ctypes.c_uint8 * 16)] +
                [(n, _U) for n in _RU_NAMES] + [("_pad", _U * 20)])


def footprint(pid):
    """Physical footprint of one process in bytes, or None if it is gone.

    Resident set size is the wrong number here. A PyTorch job on Metal keeps most of its
    memory in private allocations that never show up in RSS, which is exactly the case
    the scheduler exists for.

    The binding is easy to crash: proc_pid_rusage declares its third parameter as
    rusage_info_t, which is a void *, so it looks like it wants a pointer to a pointer.
    It does not. Pass the address of the struct.
    """
    buf = _RUsage()
    try:
        if _libc.proc_pid_rusage(pid, 5, ctypes.byref(buf)) != 0:   # RUSAGE_INFO_V5
            return None
    except Exception:
        return None
    return int(buf.ri_phys_footprint)


def pids_in_group(pgid):
    try:
        out = subprocess.check_output(["ps", "-o", "pid=", "-g", str(pgid)],
                                      text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    return [int(x) for x in out.split()]


def group_footprint(pgid):
    """Summed footprint of a whole process group, which is what a job actually costs."""
    total = 0
    for pid in pids_in_group(pgid):
        f = footprint(pid)
        if f:
            total += f
    return total


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def top_consumers(n=5):
    """The processes holding the memory, for telling a waiting job who is in its way."""
    try:
        out = subprocess.check_output(
            ["ps", "-Ao", "pid,rss,comm", "-r"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, rss = int(parts[0]), int(parts[1]) * 1024
        except ValueError:
            continue
        rows.append((rss, pid, parts[2].strip().split("/")[-1][:40]))
    rows.sort(reverse=True)
    return [{"pid": p, "bytes": b, "name": nm} for b, p, nm in rows[:n]]


def thrashing(window=1.0):
    a = vm_stats()
    t0 = time.time()
    time.sleep(window)
    b = vm_stats()
    dt = max(1e-6, time.time() - t0)
    return {"swapouts_per_s": (b.swapouts - a.swapouts) / dt,
            "pageouts_per_s": (b.pageouts - a.pageouts) / dt,
            "compressions_per_s": (b.compressions - a.compressions) / dt}


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}TB"
