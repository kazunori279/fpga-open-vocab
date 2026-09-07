# cue.py stops a run whose gain lock did not take

*2026-09-08. Not a bench: no operator, no objects, nothing staged, no accuracy
claimed. One question — does the abort path run clean on the board, and does it
stay quiet when the lock does not visibly fail?*

[`../../../host/cue.py`](../../../host/cue.py) now reads #33's die witness out of
the board's own output and stops the run when it says the lock did not take.
This directory is the check on that, and it is deliberately two halves, because
only one of them can be done on a desk that is behaving.

## The half the archive answers: it fires, and in time

The witness prints its verdict about eleven frames after the `'L'` press, and
`--lock-camera` presses on the last baseline frame. Both of
[2026-09-08's paired runs](../../cue/) were replayed through the two patterns
`cue.py` matches on:

| log | gain | abort fires |
|---|---|---|
| [`m9_cue-20260908-0615`](../../cue/m9_cue-20260908-0615.log) | `08` → `0d`, dragged | **frame 72** |
| [`m9_cue-20260908-0602`](../../cue/m9_cue-20260908-0602.log) | `06` → `06`, held | never |

The first enrolment window on that schedule opens at frame **74**. So the run
stops two frames before it teaches itself a reference under a camera nobody is
controlling — which is the whole reason this is worth wiring up rather than
noticing afterwards. 0615 was not stopped: it ran to frame 405 with the verdict
already in its log.

## The half this run answers: it stays quiet, and nothing breaks

[`nodrag.log`](nodrag.log) — 161 frames, `--lock-camera --repeat 1 --hold 22`,
empty desk throughout, `--leave-running` so the board stays on `forgix_m9`.

```
camera    : die witness on the 'L' at frame 61, sampled 8 frames apart -
            exposure 00c4 00c4, gain 00 00, wb 10 10
            did not move: exposure gain white-balance - WHICH IS NOT EVIDENCE
            EITHER WAY.
```

No banner, no abort, and [`nodrag.log.cues`](nodrag.log.cues) carries
`# camera-lock 59` with **no** `# camera-lock-dragged` line. That is the correct
negative: the witness said *nothing moved*, which
[`../20260907-witness/`](../20260907-witness/) measured as almost always what it
says — something moved inside the window on 1 of 26 windows — and silence is not
a lock that took. `cue.py` matches only the movement line for exactly that
reason.

## What this does not say

- **Not that the abort will fire when a lock fails.** It fires when the witness
  *catches* a lock failing, and the witness is nearly blind on a still scene.
  This buys back 150 seconds on the failures it can see, and sees an unknown
  fraction of them. (It said *nine minutes* when this page was written. Every
  09-08 bench reports `282 ms/frame`, so a 546-frame run is 154 s of board time
  and about three and a half minutes of the operator's — the nine came from
  m9's 851 ms/frame, which is not the rate this path runs at.)
- **Not a rate for the gain lock.** [`../20260907-hold/`](../20260907-hold/)
  measured 0 of 140 polls dragged on a still desk and 0615 dragged with the
  light moving. Two conditions, no rate.
- **Nothing about the scores in `nodrag.log`.** There was nothing on the desk
  and nobody moved when it cued. The segment table in it is an artifact of the
  instrument running, not a measurement.
- **The gain read `00` here** where 0602 read `06` and 0615 read `08`. Not
  chased. It is a different boot and a different room, and no address on this
  die gets a name.
