#!/usr/bin/env python3
"""Stage two of the hook: decide, and if the job is heavy, rewrite the command.

Reached only for commands that matched the cheap pattern in rada-gate. Everything here
is written so that any failure prints an empty decision, which means the command runs
exactly as the model wrote it.

The rewrite is the delicate part, and it has one property worth stating plainly, because
getting it wrong silently is easy: the original command is written to a file byte for
byte and is never re-parsed by a shell, and the rewritten line is a single line whose
tail is a comment. Shell operators in the original, ampersands, pipes, redirections,
command substitutions, quotes, and newlines, therefore cannot escape into the rewritten
command. tools/prova.py tests exactly this.

What the rewrite costs the user, stated because it should not be discovered later: the
permission rules Claude Code applies are matched against the rewritten command, not the
original. Wrapping a command breaks the prefix its permission rule was written for. This
is why installation asks for one allow rule for the wrapper, and why gating is opt-in
rather than the default for a machine with narrow Bash permissions.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NOTHING = "{}"


def out(obj=None):
    print(json.dumps(obj) if obj else NOTHING)
    sys.exit(0)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        out()

    if payload.get("tool_name") != "Bash":
        out()
    cmd = (payload.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        out()

    # Stage one matched against the whole payload, which includes the description the
    # model wrote. Match the command itself before doing anything, so a job is never
    # queued because of a word in its description.
    pats = [p for p in os.environ.get("RADA_PATTERNS", "").split("|") if p]
    if pats and not any(p in cmd for p in pats):
        out()
    if re.search(r"\brada\b\s+run|rada-gate", cmd):
        out()

    try:
        from rada import store
        from rada.setup_claude import wrapper_path, gating_enabled
    except Exception:
        out()

    if not gating_enabled():
        # Advisory mode: say nothing, change nothing. The user has not opted in.
        out()

    # With one session open there is nobody to coordinate with, and queueing only adds
    # a wait before doing what the job was always free to do. Record that this session
    # is alive, and stand aside unless somebody else is here or work is already in
    # flight. A failure to decide leaves the old behaviour in place: gate as before.
    try:
        from rada import sessions
        sessions.note(payload.get("session_id"))
        if not sessions.contended(payload.get("session_id")):
            out()
    except Exception:
        pass

    try:
        store.ensure_home()
        store.sweep_pending()
        nonce = f"{int(time.time())}{os.getpid()}{os.urandom(3).hex()}"
        path = store.pending_path(nonce)
        with open(path, "w") as f:
            f.write(cmd)
    except Exception:
        out()

    # One line. The tail is a comment, so nothing in it is ever executed, and it exists
    # so the transcript and the permission prompt show what is really going to run.
    show = re.sub(r"\s+", " ", cmd).replace("\x00", "").strip()[:140]
    new = f"{wrapper_path()} run --ticket {nonce}  # rada: waiting for memory, then: {show}"
    if "\n" in new or "\r" in new:
        out()

    updated = dict(payload.get("tool_input") or {})
    updated["command"] = new
    desc = updated.get("description") or ""
    updated["description"] = (f"{desc} (queued by rada)").strip()

    out({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": updated}})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        print(NOTHING)
