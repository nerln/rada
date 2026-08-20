# The mark

A berth, and the queue outside it.

One bar on the right is the quay. Two circles wait to its left, and only the near one is
lit. That is the whole tool in three shapes: several jobs want the same room, one of them
is going in, and the rest are outside until there is space.

`docs/img/mark.svg` is the drawing. `macapp/Tools/make-icon.swift` is the same drawing in
Core Graphics, which builds the application icon; the site header and the favicon carry it
inline. Change the SVG and change those two with it.

Two things were tried and rejected.

An anchor. It says "boats" and nothing else, every marine product on earth uses one, and
at sixteen pixels it is a smudge with a crossbar.

Three circles instead of two. It reads as a loading indicator, which is a picture of
something happening rather than of something waiting.

## Colour

| | | |
|---|---|---|
| Ink | `#0B1013` | deep water at night, and the page |
| Signal | `#4FBE8F` | the job that is going in, and one control per screen |
| Paper | `#E9EFEF` | the quay, and the words |
| Lantern | `#D4685C` | one thing only: what a person has stopped |

The greys carry a little blue and green. A neutral grey next to the signal reads as dead.

Signal green is the harbourmaster saying go. It marks the job that starts and the one
control worth pressing, and nothing else. On a screen where everything is green, nothing
is.

## The name

Lowercase, always: `rada`, never `Rada` in running text. The application bundle is
`Rada.app` because macOS capitalises application names, and arguing with that only
produces an odd-looking Dock.

A *rada* is the sheltered water outside a port where ships wait at anchor for a berth.
That is the whole metaphor and it is worth keeping straight: rada is the water, not the
harbourmaster. Nothing here commands a ship, and nothing here sinks one.
