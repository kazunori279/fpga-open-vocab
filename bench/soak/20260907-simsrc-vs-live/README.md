# Is the walk the camera at all? — written before the first frame

*2026-09-07, evening. Two runs, not sixteen. **This is a smoke test and not a
bench**, and the section below says exactly what it may and may not be quoted
for.*

## Why two runs are enough for this one question

[`../20260907-camlock-cold/`](../20260907-camlock-cold/) settled that locking
exposure and gain does not reduce the common-mode walk — lock 3.24 mean against
free 2.41, one-sided p = 0.52, and the point estimate runs the wrong way. That
result reads two ways and the session could not separate them:

1. the auto loops are not the mechanism, but the camera still is; or
2. **the walk is not the camera at all**, and eight pairs were spent on the
   wrong suspect.

`m9`'s `'M'` key, wired today, takes the sensor out of the loop: the ArduChip
feeds a fixed pattern and the frame cannot change for any reason.
[`../../probe/20260907-simsrc/`](../../probe/20260907-simsrc/) showed the
scoring chain is bit-flat on it over ten frames — `book -6.03  cup -18.44`,
identical to the last decimal.

So the prediction here is not a difference of means. It is **exactly zero**:

> If the chain is deterministic, the synthetic arm's `common` walk is `0.00`,
> because `walk()` is max-minus-min of a smoothed series and every element of
> that series is the same number.

The free arm's eight published runs sit at **1.38 – 2.94**. Zero against that
range needs no statistics, no pairing and no p-value, which is the whole reason
two runs will do. **A nonzero synthetic walk is the interesting outcome**, and
it would mean something downstream of the sensor is time-dependent over three
minutes — at which point this becomes a real bench and gets designed properly.

## The protocol, fixed before any frame

Two runs of 600 frames, `host/demo.py --no-smooth --leave-running`, the same two
queries as the camlock sessions (`a closed book`, `an opened book`), scored by
`tools/probe_camlock.py` at its default `SKIP = 60`.

| order | arm | key | why this order |
|---|---|---|---|
| 1 | `live` | none | must run **first**: `--leave-running` leaves `0x06` set, and `demo.py` attaching to an already-running board does not reset it, so a live run after a synthetic one would not be live |
| 2 | `synthetic` | `--enrol=40:M` | frame 40 is inside the scorer's dropped 60, so the live head does not enter the window |

`tools/probe_camlock.py` gained a `SYNTH` regex today so the arm is named
`synthetic` rather than silently counted as `free`, which is the one arm it is
the exact opposite of. No log written before today can contain that line, so
nothing was rescored.

**Scene: unchanged and motionless throughout both runs.** This matters for the
live arm only — the synthetic arm has no sensor in it and is scene-independent
by construction, which is also why its comparison against the camlock-cold free
arm is valid across sessions and a live-to-live comparison would not be.

## What this may not be quoted for

Not a bench: no cue schedule, no enrolment, no held-out set, no accuracy. Not a
replacement for the camlock sessions and not a re-test of them — nothing here
rescores or contradicts `20260907-camlock-cold`, whose free arm is used as a
published reference and not re-measured.

**One run an arm is one run an arm.** The live number below is a sanity check
that the rig still behaves like it did this morning, not an estimate of
anything. If it lands outside the free arm's 1.38 – 2.94, the session says
nothing about the synthetic arm either and has to be repeated.
