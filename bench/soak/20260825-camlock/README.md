# Locking the camera, issue #30

*2026-08-25. One empty desk, one binary, nineteen runs of `host/demo.py`, about
two hours. No object was ever placed in front of the board.*

**Nothing in here is a bench.** No cue schedule, no enrolment, no held-out set,
no accuracy. These runs exist because the thing #30 intervenes on — how far the
scores of a motionless scene walk during one run — needs no staging, and the
staging is what makes a bench cost a morning. Read them with
[`tools/probe_camlock.py`](../../../tools/probe_camlock.py).

Two things came out, and only the first is settled.

## 1. Switching the white-balance loop off does not hold the white balance

This is what `cam_image_auto(false)` did when #30's off switch was first written,
and it is why the paired bench that switch was built for was never run.

`smoke-toggle.log`, one run, empty desk, all three loops off at board frame 33
and back on at 62:

| board frame | state | mean RGB |
| --- | --- | --- |
| 33 | freeze all three | 134 132 135 |
| 62 | still frozen, 29 frames later | **98 157 123** |
| 91 | tracking again | 133 132 135 |

`smoke-L.log` is the same thing seen first and by accident: frozen at frame 43 at
136 136 139, ending at 96 153 120. Overall brightness barely moves — 133 to 126 —
so it is the colour gains and not the exposure.

`attribute.log` takes the three loops one at a time, thirty frames each, in one
run with the room held still:

| frames | state | mean RGB |
| --- | --- | --- |
| 0–33 | all tracking | 134 133 135 |
| 33–62 | exposure frozen | 130 128 129 |
| 62–92 | exposure + gain frozen | 130 127 129 |
| 92–123 | **+ white balance frozen** | **100 158 122** |
| 123–150 | all tracking again | 135 133 137 |

**Only the AWB loop misbehaves.** Exposure and gain freeze cleanly and hold
neutral across sixty frames. Clearing bit 7 on the white-balance selector drops
the red and blue gains toward unity rather than latching what the loop chose, and
this sensor needs both above unity in room light.

So `'L'` freezes exposure and gain only, and `--lock-camera` means that. The
all-three state is still reachable on a second press and is kept **as a control**:
a fault that can be reproduced live is what makes the arms either side of it mean
something, which is the same argument [`20260825-fmtcache/`](../20260825-fmtcache/)
rests on.

`statecheck-1.log` and `statecheck-2.log` are the hygiene check that made the
soak below valid: each `demo.py` run re-enters `ft_acquire()`, so the lock state
resets to all-tracking between runs and the arms cannot leak into each other.

## 2. The lock caps the worst walk. It does not clear significance

Sixteen runs, 600 frames each, alternating which arm went first in each pair so
the arm is not confounded with position in the session. `common` is the walk of
(z[A] + z[B]) / 2 and `margin` the walk of z[A] − z[B], both as the range of a
centred 31-frame mean after dropping 60 frames of ramp.

| run | arm | common | margin | last mean RGB |
| --- | --- | --- | --- | --- |
| free-1 | free | 0.72 | 1.10 | 135 131 135 |
| lock-1 | exposure gain | 0.90 | 0.71 | 133 130 133 |
| free-2 | free | **6.05** | 5.81 | 130 128 141 |
| lock-2 | exposure gain | 2.45 | 2.14 | 116 114 117 |
| free-3 | free | **5.49** | 1.76 | 120 119 123 |
| lock-3 | exposure gain | 1.78 | 1.32 | 124 121 126 |
| free-4 | free | **7.46** | 3.18 | 99 99 101 |
| lock-4 | exposure gain | 1.24 | 1.09 | 139 134 138 |
| free-5 | free | 1.10 | 1.37 | 133 128 132 |
| lock-5 | exposure gain | 1.47 | 0.73 | 96 99 98 |
| free-6 | free | 2.50 | 1.22 | 133 130 137 |
| lock-6 | exposure gain | 1.72 | 0.73 | 97 99 98 |
| free-7 | free | 2.70 | 1.93 | 97 99 99 |
| lock-7 | exposure gain | 1.51 | 1.11 | 136 134 139 |
| free-8 | free | 1.85 | 0.89 | 97 97 97 |
| lock-8 | exposure gain | 2.60 | 1.10 | 138 136 140 |

`common` median 1.62 locked against 2.60 free; `margin` 1.09 against 1.57.

**The one thing that holds is a ceiling.** No locked run walked further than 2.60;
three of eight free runs walked 5.49, 6.05 and 7.46. Every locked run is inside
the free arm's own quiet range, and the lock's contribution is that it has no bad
tail rather than that it is typically better.

**And that is not significant.** Mann–Whitney on `common` gives U = 46 of 64,
z = 1.47, one-sided p ≈ 0.07 at n = 8 a side. This page has [a retraction of a
+0.10 that survived a second draw](../../stills/20260822-synth-book-crop2/README.md#retracted-2026-08-22-rkd-10-is-not-worth-010)
one directory over; a p of 0.07 on sixteen runs is not a result, it is a reason
to run more.

### The confound, stated because it was noticed after the fact

The three big free walks are runs 3, 6 and 7 of the sixteen in wall-clock order,
and every free run after position 9 is quiet. The locked arm shows no such trend —
0.90, 2.45, 1.78, 1.24, 1.47, 1.72, 1.51, 2.60 across the whole session. So the
gap lives almost entirely in the first half, when the board and the room were
still settling, and closes in the second.

That is a coherent reading — a lock can only help a sensor that is still
re-deciding — and it is exactly the kind of reading this repository does not get
to quote, because the split was chosen after seeing the numbers. It is written
down as the hypothesis for the next session and not as a finding.

**The decisive run is therefore a cold one.** Alternating pairs from a cold boot
in an unsettled room, before anything has equilibrated, is where the effect should
be if it is real. Sixteen more runs in a room that has already settled will dilute
it rather than resolve it.

### What did not replicate

`margin` was as large as `common` in the first four runs, which would have meant
the walk is not common-mode and #30's mechanism is wrong. Over all sixteen it is
not: `margin` medians are 1.09 and 1.57 against `common`'s 1.62 and 2.60, and the
two worst free runs by `common` (7.46 and 5.49) carry `margin` of only 3.18 and
1.76. The four-run reading was four runs.

## The AEC still lands on one of two plateaus, and #30 does not touch it

The `last mean RGB` column above sits near either 135 or 97 with almost nothing
between, on the same desk in the same room within one session. That is the open
question [`20260823-exposure/`](../20260823-exposure/) closed its own bug on and
left behind — "the AEC still arrives at two different operating points ... that is
the sensor choosing, and it is the next question".

`--lock-camera` makes a run internally consistent at whichever plateau that boot
picked; it does nothing about which one. Drift is within a run, so this does not
invalidate the soak above, but a lock is not a calibration and nothing here should
be read as one.

## Files

| file | what it is |
| --- | --- |
| `smoke-L.log` | the first freeze, ending green — found by accident |
| `smoke-free.log` | its unlocked control, ending neutral |
| `smoke-toggle.log` | freeze and unfreeze in one run; the recovery is the proof |
| `attribute.log` | the three loops one at a time, which named the AWB |
| `statecheck-{1,2}.log` | that the lock state resets between `demo.py` runs |
| `free-{1..8}.log`, `lock-{1..8}.log` | the interleaved soak, 600 frames each |

Every run is `host/demo.py "a closed book" "an opened book" --no-smooth
--frames N [--enrol=40:L] --leave-running`. The queries only define the axis —
the desk was empty throughout and no frame in here has been shown an object.
