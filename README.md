# rada

An anchorage for heavy jobs, so that several Claude Code sessions on one laptop stop
starting them all at once.

[Italiano](README.it.md)

## Why this exists

Four Claude Code sessions were open on a 16 GB MacBook Pro, one per project. Each of them
decided, reasonably and independently, that now was a good time to start something big:
a PyTorch model in single precision, an Xcode build, a Unity import, an ffmpeg pass. None
of them could see the others. The machine had 2992 MB of its 4096 MB swap in use and
88000 pageouts before anything visibly went wrong, and then everything stopped
responding for several minutes.

Nothing in that story is a bug in Claude Code. Sessions are isolated by design, and that
is usually what you want. It just means that on one machine, nobody is counting.

rada counts. A job that looks heavy waits in a queue until there is really room for it,
and a language model decides who goes first when several are waiting, because a model
reading the project name and the command can tell a test someone is waiting for from a
nightly re-index, and arrival order cannot.

## How it works

```
    Claude Code session                    rada
    ───────────────────                    ────
    Bash: python train.py
      │
      ├─ PreToolUse hook ────────────────► looks heavy?  ── no ──► runs untouched
      │                                        │ yes
      │                                        ▼
      │                                   save the command verbatim,
      │                                   rewrite the call to the wrapper
      ▼
    Bash: rada run --ticket 8f3a  # rada: waiting for memory, then: python train.py
      │
      ▼
    the wrapper takes a ticket ─────────► queue ──► judge orders it
      │                                        │
      │                                        ▼
      │                                   is there room, and is it your turn?
      ├─ no ──► waits, printing why, and who is holding the memory
      └─ yes ─► runs the original command, measures what it really used
```

There is no daemon. Coordination is a single JSON file under `~/.rada` guarded by a lock,
and the waiting is done by an ordinary process that Claude Code already knows how to time
out and move to the background.

## What the judge decides, and what it cannot

The judge is `claude -p` with a short prompt: the queue, and a request to order it by who
is likely to be waiting on the result. It runs only when two or more jobs are queued, at
most once every three minutes, in the process of whichever job has been waiting longest.
There is no account to configure and nothing to install.

Its answer is not an instruction. It is converted into a bonus of at most three points on
a score where waiting earns one point every thirty seconds:

    score = age / 30s + judge_bonus,   judge_bonus between 0 and 3

Two things follow, and both are tested rather than asserted.

**A job cannot be overtaken forever.** A job that arrived more than ninety seconds earlier
outranks a newcomer whatever the judge says, because ninety seconds of age is worth more
than the largest bonus the judge can give.

**A job that has waited ten minutes stops being the judge's business.** It joins a set
that is served first and ordered strictly by arrival time, and the judge is excluded from
that set entirely. From that moment the jobs that can still go before it are exactly those
already in the set ahead of it, and that group cannot grow.

So the promise is: **a waiting job is passed by a bounded number of other jobs, and the
bound is fixed the moment it becomes mandatory.** The promise is in completions rather
than in minutes on purpose. A wall-clock guarantee would be a lie, because a job holding
memory can run for as long as it wants and rada does not kill anything a person started.

If the judge is slow, missing, or answers with something that is not a permutation of the
queue it was given, its answer is discarded and the queue runs on arrival order. The queue
never waits for it.

## Memory

The number rada spends is not the number that looks available. Page cache counts as
available and is not really; the compressor holds real memory; and on Apple Silicon a
PyTorch allocation on the GPU lands in ordinary memory where nothing will refuse it. So
the budget is

    total − reserve − (wired + compressor + uncompressed anonymous)

with a reserve of 15 percent or 1.5 GB, whichever is larger, and hard stops at kernel
pressure above normal, at the kernel's own free estimate below 25 percent, and a clamp
when swap is more than three quarters full. A job is admitted only if its estimate times
1.3 fits.

The estimate comes from the job itself. rada samples the whole process group's physical
footprint while it runs and remembers the peak against a signature of the command with
numbers erased, so re-running the same script with a different learning rate inherits what
was learned. Declare it yourself with `--need 6G` when you already know.

When the job at the head of the queue does not fit even in an empty machine's budget,
rada reserves: it stops admitting anything that would eat the head's share and lets the
machine drain, allowing only short jobs to slip underneath. If the head still does not fit
after seven minutes, rada gives up the reservation with a growing cooldown, says which
processes are holding the memory, and lets everyone else run in the meantime.

## Install

```bash
git clone https://github.com/nerln/rada.git ~/dev/rada
cd ~/dev/rada
./bin/rada install
```

That registers one `PreToolUse` hook. It runs before every Bash command in every session,
so it is written to fork once and match with shell builtins: about 3 ms on top of the cost
of starting any hook at all, for commands that are not heavy.

The first time a heavy command is rewritten, Claude Code will ask permission, and the
prompt shows the real command in a comment at the end of the line. To stop being asked,
add an allow rule for the wrapper:

    Bash(/Users/you/dev/rada/bin/rada run:*)

**Read this before adding that rule.** Claude Code matches permission rules against the
rewritten command, so wrapping a command breaks the prefix its own rule was written for.
Allowing the wrapper means a heavy command that your other Bash rules would have stopped
will no longer be stopped by them. If your Bash permissions are already broad this changes
nothing you would notice. If they are narrow and you rely on them, either leave the rule out
and approve each job when asked, or run `rada mode advise`, which turns automatic queueing
off and leaves rada as something you invoke by hand.

## Using it

```bash
rada status                        # what is running, what is waiting, and why
rada watch                         # the same, refreshed
rada run --need 6G -- python train.py
rada run --note "blocking the paper deadline" -- pytest tests/
rada run --max 600 -- ./slow-build.sh     # give up waiting after ten minutes
rada doctor                        # check the installation
rada reset                         # forget the queue
```

Which commands count as heavy lives in `~/.rada/heavy.txt`, one substring per line. Edit
it and run `rada install` again to recompile it.

`RADA_FAKE_BUDGET=500M rada status` pins the budget to a number you choose, which is how
to see what the queue does on a machine smaller than yours.

## What it does not do

- It does not kill anything. A job that has started runs to completion, and a dev server
  that holds memory forever holds it forever. rada will say so instead of waiting silently.
- It does not gate work that never becomes a Bash command. An MCP tool that builds an
  Xcode project inside its own server is invisible to a hook on Bash.
- It does not know what a job needs before it has seen it once. The first run of anything
  is assumed to need 512 MB unless you say otherwise.
- It does not schedule across machines, and it has only been run on macOS on Apple
  Silicon. On anything where it cannot read memory it lets every job through.
- It does not send anything anywhere. The judge runs `claude -p` locally, on a prompt
  containing project names and command lines. If that is too much for your repository,
  `rada mode advise` and no judge is ever called.

## Prompt injection

The queue that the judge reads contains command lines, which contain text from
repositories, which may be hostile. That text is delimited, flattened to one line,
truncated, and stripped of the words that mark the queue block, and the model is told it
is data. None of that is a guarantee. The guarantee is downstream: the only thing rada
accepts from the judge is a permutation of the exact ids it asked about, worth at most
three points, expiring after three minutes, and unable to touch the mandatory set or to
trigger a reservation. A judge that has been fully talked into something can move a job
ahead of another for ninety seconds.

## Tests

```bash
python3 tools/prova.py
```

Seventy checks, a couple of seconds, no model and no real memory allocated. They cover the
rewrite refusing to leak shell operators or newlines, both fairness lemmas including a
four-hundred-round adversarial simulation, lease recovery after a crash, the lock under
four processes hammering it, the judge's output validation, reservation and backfill and
the cooldown, and two real processes contending for one berth.

Two of those tests exist because they found real defects during development: two jobs
could be admitted at once because the admission decision and the lease were in different
transactions, and the lock could be held by two processes at once because it announced
itself before it said who owned it.

## Licence

GPL-3.0. See [LICENSE](LICENSE).
