"""The judge: a language model that reads the work context and orders the queue.

Why a model and not a rule. Two jobs are queued. One is a test run in a repo the user has
been editing all morning; the other is a nightly re-index that nobody is waiting for.
Arrival time cannot tell them apart, a static priority number goes stale the day after it
is set, and asking the user to annotate every command is the reason nobody uses
schedulers. A model reading the project name, the working directory and the command
itself gets this right most of the time, and most of the time is worth having.

Why it is kept on a short leash. The same model, reading text it does not control, can be
talked into anything, and a model that is simply wrong is indistinguishable from one that
has been steered. So its output is not an instruction. It is a ranking, converted into a
bonus of at most CAP points in sched.py, which age overtakes in CAP * TAU seconds. It
cannot grant memory, cannot mark a job mandatory, cannot trigger a reservation, and
cannot remove a ticket. If it is slow, missing, or returns nonsense, the queue falls back
to arrival order and nothing waits on it.

The judge runs as `claude -p`, so there is nothing to install and no API key to manage:
anyone who has Claude Code has the judge. It is invoked only when more than one job is
waiting, at most once per JUDGE_TTL, by whichever waiter has been queued longest.
"""
import json
import os
import re
import shutil
import subprocess
import time

from . import store

TTL = 180.0            # a verdict is good for this long
TIMEOUT = 90.0         # a judge that has not answered by now is not going to
MIN_QUEUE = 2          # nothing to judge below this
MAX_TICKETS = 12       # never ask about more than this many at once
MODEL = os.environ.get("RADA_JUDGE_MODEL", "haiku")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYS_PROMPT = os.path.join(REPO, "rada", "judge.sys")

PROMPT = """<<<QUEUE
{queue}
QUEUE

Order the ids above."""

SCHEMA = json.dumps({
    "type": "object",
    "required": ["order", "why"],
    "additionalProperties": False,
    "properties": {"order": {"type": "array", "items": {"type": "string"}},
                   "why": {"type": "string"}},
})

# Only these reach the judge. Everything else in the environment is a way for the
# machine's state to influence a process whose one job is to sort a list.
KEEP_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TMPDIR",
            "TERM", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
            "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

# A denylist behind the allowlist. `--tools ""` already removes everything, and this
# costs one argument, so it stays as cover for that flag being renamed or misparsed by
# some future build.
NO_TOOLS = ("Bash,Read,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task,Glob,Grep,"
            "TodoWrite,SlashCommand,Skill")


def _scrub(text, limit=160):
    """Flatten and shorten a command so it cannot forge queue structure in the prompt."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = t.replace("QUEUE", "Q_U_E_U_E").replace("<<<", "<").replace(">>>", ">")
    return t[:limit]


def _render(tickets, now):
    lines = []
    for tid, tk in tickets.items():
        waited = int(now - tk.get("enq", now))
        need = tk.get("need", 0) / (1024 ** 3)
        proj = _scrub(tk.get("project") or os.path.basename(tk.get("cwd", "")) or "?", 40)
        line = (f"- id={tid} project={proj} waited={waited}s "
                f"needs={need:.1f}GB command={_scrub(tk.get('show'))}")
        intent = tk.get("intent")
        if intent:
            line += f" note={_scrub(intent, 120)}"
        lines.append(line)
    return "\n".join(lines)


def should_run(d, now=None):
    now = now or time.time()
    if len(d.get("tickets", {})) < MIN_QUEUE:
        return False
    j = d.get("judge") or {}
    if j.get("running_pid") and _alive(j["running_pid"]) and now - j.get("running_ts", 0) < TIMEOUT:
        return False
    return now - j.get("ts", 0) > TTL


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def duty(d, tid, now=None):
    """True if this waiter is the one that should pay for the judge call.

    The longest-waiting ticket does it. That is deliberate: the process with the most to
    gain from a good ordering is the one that spends the tokens, and it is a real waiter,
    never an unrelated session that happens to be idle.
    """
    if not should_run(d, now):
        return False
    tickets = d.get("tickets", {})
    if tid not in tickets:
        return False
    oldest = min(tickets, key=lambda t: tickets[t].get("enq", 0))
    return oldest == tid


def _harness_dir():
    """A directory with nothing in it, for the judge to run in.

    The working directory decides which CLAUDE.md files a session reads. Running the
    judge wherever the waiting job happens to be would feed it the instructions of
    whatever project that is, which is both noise and a way for a repository to write
    part of the judge's prompt.
    """
    d = os.path.join(store.HOME, "judge")
    os.makedirs(d, exist_ok=True)
    mcp = os.path.join(d, "no-servers.json")
    if not os.path.exists(mcp):
        with open(mcp, "w") as f:
            f.write('{"mcpServers":{}}')
    return d, mcp


def ask(tickets, now=None):
    """Run the model in a closed harness. Returns (order, why) or (None, reason).

    Every flag below removes something, and each one is removing a specific way for this
    call to become more than a sorting request:

      --system-prompt-file   the judge is not a coding agent with a task appended. It
                             gets its own instructions and nothing else.
      --tools ""             no tools at all. A process that cannot call anything cannot
                             be talked into calling anything.
      --safe-mode            the CLI's own restricted profile, on top of the above.
      --setting-sources ""   no user, project or local settings, which means no hooks.
                             Without this the judge inherits rada's own PreToolUse hook
                             and every other hook on the machine.
      --strict-mcp-config    with an empty server list, no MCP servers are loaded even
      --mcp-config           if the user has some configured.
      --disable-slash-commands  queue text cannot reach a command by starting with a slash.
      --no-session-persistence  the verdict leaves no session behind to be resumed,
                             steered or read.
      --json-schema          the answer is shaped by the runtime rather than by a regular
                             expression applied to prose.
      cwd, env               a directory with nothing in it, and a short allow list of
                             variables.

    The prompt goes on standard input rather than in the argument list. Arguments are
    visible to every process on the machine through ps, and a queue of a dozen commands
    is long enough to run into argument size limits.

    Above all of it, the context is fresh every time. That is the property that makes the
    rest defensible: whatever a hostile command line achieves in one verdict, it does not
    carry into the next one, because there is no next one for it to carry into.
    """
    now = now or time.time()
    if not shutil.which("claude"):
        return None, "the claude command is not on PATH"
    if not os.path.exists(SYS_PROMPT):
        return None, "the judge's system prompt is missing from the installation"
    ids = list(tickets)
    prompt = PROMPT.format(queue=_render(tickets, now))
    cwd, mcp = _harness_dir()
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env["RADA_DISABLE"] = "1"
    # the same gate --safe-mode sets, so an argv typo does not quietly reopen hooks
    env["CLAUDE_CODE_SAFE_MODE"] = "1"
    try:
        p = subprocess.run(
            ["claude", "-p",
             "--model", MODEL,
             "--system-prompt-file", SYS_PROMPT,
             "--tools", "",
             "--disallowedTools", NO_TOOLS,
             "--safe-mode",
             "--setting-sources", "",
             "--strict-mcp-config", "--mcp-config", mcp,
             "--disable-slash-commands",
             "--no-session-persistence",
             "--output-format", "json",
             "--json-schema", SCHEMA,
             "--effort", "low",
             "--max-budget-usd", "0.05"],
            input=prompt, capture_output=True, text=True, timeout=TIMEOUT,
            cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        return None, f"judge did not answer within {int(TIMEOUT)}s"
    except Exception as e:
        return None, f"judge could not be started: {e}"
    if p.returncode != 0:
        return None, f"judge exited {p.returncode}"

    # The runtime wraps the answer. Prefer the field it validated against the schema,
    # fall back to the text field, and only then to fishing braces out of prose.
    got = None
    try:
        envelope = json.loads(p.stdout)
        if isinstance(envelope, dict):
            if envelope.get("is_error"):
                return None, f"judge reported an error: {envelope.get('subtype', '')}"
            got = envelope.get("structured_output")
            if not isinstance(got, dict):
                got = json.loads(envelope.get("result") or "null")
    except Exception:
        got = None
    if not isinstance(got, dict):
        m = re.search(r"\{.*\}", p.stdout, re.S)
        if not m:
            return None, "judge returned no JSON"
        try:
            got = json.loads(m.group(0))
        except Exception:
            return None, "judge returned malformed JSON"

    order = got.get("order")
    if not isinstance(order, list):
        return None, "judge returned no order"
    order = [str(x) for x in order]
    # The one validation that matters: it must be a permutation of what we asked about.
    # Anything else means the model invented, dropped or duplicated a job, and a verdict
    # we cannot check is a verdict we do not use.
    if sorted(order) != sorted(ids):
        return None, "judge returned an order that is not a permutation of the queue"
    why = _scrub(got.get("why", ""), 200)
    return order, why


def run_and_record(tid):
    """Called by the waiter on judge duty, outside the lock because the model is slow."""
    now = time.time()
    with store.Transaction() as st:
        if not st.ok or not duty(st.d, tid, now):
            return
        st.d.setdefault("judge", {})
        st.d["judge"]["running_pid"] = os.getpid()
        st.d["judge"]["running_ts"] = now
        tickets = dict(st.d["tickets"])

    # Ask about the oldest few only. A queue of twenty makes a long prompt, a slow
    # answer and a permutation the model is more likely to get wrong, and the jobs
    # near the back are going to be reordered again before their turn comes anyway.
    if len(tickets) > MAX_TICKETS:
        oldest = sorted(tickets, key=lambda t: tickets[t].get("enq", 0))[:MAX_TICKETS]
        tickets = {t: tickets[t] for t in oldest}

    order, why = ask(tickets)

    with store.Transaction() as st:
        if not st.ok:
            return
        j = st.d.setdefault("judge", {})
        j.pop("running_pid", None)
        j.pop("running_ts", None)
        if order:
            j["ts"] = time.time()
            j["order"] = order
            j["why"] = why
            store.log(f"judge: {' > '.join(order)} ({why})")
        else:
            # Record the failure so the next waiter does not immediately retry, and so
            # `rada status` can say why the ordering is by arrival time.
            j["ts"] = time.time()
            j["order"] = []
            j["why"] = f"unavailable, using arrival order: {why}"
            store.log(f"judge unavailable: {why}")
