# Superseded by 20260827-empty-book, and kept for the one thing it still says

*Shot 2026-08-27 despite the directory name — the name is from the day the set
was planned and the empty round 1 originally shot, and the whole set was
re-shot the morning of the 27th before anything was read off it.*

**Do not quote a number from this directory.** Use
[`../20260827-empty-book/`](../20260827-empty-book/), which asks the same
question of the same book on the same desk two hours later, with the capture bug
below fixed.

## Why it was thrown out

`shoot.sh` ran 22 frames and started dumping at frame 4. Every round in here
reported `exposure settled after 12-14 frames`. So roughly the first five stills
of every round were taken while `ft_acquire()` was still ramping — not the same
scene as the five after them, just the sensor still deciding.

The settled operating point then differed wildly between rounds, worst on the
opened book:

| scene | round 1 | round 2 | round 3 |
| --- | ---: | ---: | ---: |
| empty | 140 | 118 | 130 |
| closed | 119 | 131 | 134 |
| open | 120 | **66** | **84** |

A factor of two on the same scene, re-staged by hand. `probe_bisect`'s trivial
luma cue separated open from closed at AUC 0.889 on these pixels, against 0.667
after the re-shoot.

The capture bug is fixed in `shoot.sh` as of the same morning: it now warms up
for 24 frames before the first `--snap-at`, and refuses the round out loud if
the sensor needed longer than that. `bench/stills/README.md` claimed the ramp
was already grep-checked at capture time; it was printed and never acted on,
and that sentence is corrected there.

## What it still says

The student's `off` ordering, which is the finding in the clean set, does not
depend on any of the above. It reads the same on both sets:

| third query | off, here | off, 08-27 |
| --- | ---: | ---: |
| `an empty desk` | 0.14 | 0.07 |
| `a bare table` | 0.21 | 0.12 |
| `nothing` | 0.57 | 1.22 |
| `a blank wall` | 1.41 | 1.30 |

Two independently captured sets, different exposures, same ranking. That is why
this directory is archived rather than deleted — it is the replication, and this
repository has twice lost the thing it later needed.

`prior-0825/` holds the eleven empty stills actually shot on 2026-08-25. They
were pulled out of `empty/` before any analysis, because round 1 is what enrols
and an empty reference from a different day's light against class references
from this morning is the confound, not a control.
