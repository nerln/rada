"""rada: an anchorage for heavy jobs launched by parallel Claude Code sessions.

A rada is the sheltered water outside a harbour where vessels lie at anchor until the
harbourmaster assigns a berth. This package is the harbourmaster.

The version lives here and nowhere else. Every other module imports it.
"""
__version__ = "0.1.0"

# The on-disk state format. Readers that do not recognise a version must fail OPEN,
# meaning they let the job run ungated rather than crash it. Two versions of rada can
# share a machine during an upgrade, because Claude Code pins plugin versions per
# session, so this number is load-bearing.
SCHEMA = 1
