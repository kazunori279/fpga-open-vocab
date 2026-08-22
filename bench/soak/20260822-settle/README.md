# The 2026-08-22 settle sweep, and the hand that reproduced it

Five `forgix_cam_probe` runs in thirty-five minutes, chasing
[#27](https://github.com/kazunori279/fpga-open-vocab/issues/27) — the
repeat-capture matrix that stopped matching its 2026-08-03 record. Four of the
five did not reproduce the fault; **the fifth put a hand over the lens and
reproduced it on the first try.**

| log | what it was | matrix | settle 0 | scene mean RGB |
| --- | --- | --- | --- | --- |
| `cam_probe-20260822-1327.log` | first run, board up for days | matches 08-03 | 3/3 up, 3/3 down | 112 149 123 |
| `cam_probe-20260822-1334-vbuscycle.log` | after `bootsel.py --power-cycle` | matches 08-03 | 3/3, 3/3 | 111 148 122 |
| `cam_probe-20260822-1343-ev.log` | first EV axis, **void — see below** | matches 08-03 | 3/3, 3/3 | 111 148 123 |
| `cam_probe-20260822-1350-ev2.log` | EV axis, fixed | matches 08-03 | 3/3, 3/3 | 120 154 124 |
| `cam_probe-20260822-1401.log` | **lens covered by hand** | **fault, 5 of 7 rows CONSTANT** | **0/3, 0/3** | **9 7 8** |

## The covered-lens run, which is the whole point of this directory

`1401` is the same firmware, the same board and the same desk as `1350` eleven
minutes earlier. The only thing changed was a hand over the lens. It reads:

```
scene     : mean RGB 9 7 8
  #  recipe      crc32    first px  verdict
  0  as-was      c80a8564 08 01     CONSTANT  <-- NOT what 2026-08-03 measured
  ...
  5  settle300   7a96e6ca 10 82     a picture
  6  everything  99f0aadd 18 82     a picture

  settle_ms  up      down
  0..250     0/3     0/3
  300        0/3     1/3
  400        3/3     3/3
  -> reliable from 400 ms in both directions, with 1 partial row below it.
```

**The threshold is a function of scene brightness.** At mean 133 it is at or
below 0 ms; at mean 8 it is 400 ms. Same firmware, same wiring, eleven minutes
apart. That is the measurement #27 was missing, and it is not a hardware fault
at all — it is the sensor's own integration time, showing through an acquire
that never waited for it.

**The EV rows confirm the mechanism by reading null.** In the lit room EV moved
the frame mean and left the threshold alone; here all three EV rows read
`first ok 400 ms` and a mean of 7–8. With no light in the room the
auto-exposure is already pinned at its longest integration, so the one lever
that lengthens exposure has nothing left to lengthen. An EV knob that stops
mattering exactly when the room goes dark is what an exposure-bound threshold
looks like.

This is also the first time the sweep and the fault have been in the same run —
`08-20`'s two faulting runs predate the sweep (added in `7d55a67`), so every
earlier threshold number was measured in a room where the fault was absent.

## What is ruled out

**A cold power cycle does not cause it.** `--power-cycle` drops VBUS on the
board's hub port, so the camera module loses 3V3 with it. The run straight after
that is `1334-vbuscycle` and it is indistinguishable from the run before. So the
state #27 is about is not "the sensor has just been powered".

**The EV register cannot emulate it.** `CAM_REG_EV_CONTROL` raises the
auto-exposure target, which is the one lever on the board that could lengthen an
integration without anybody touching the desk. At EV 0, 1 and 3 the settle sweep
reads 3/3 at 0 ms in all three rows and the frame mean moves by two counts. The
lever exists and is far too short — a hand over the lens moves the threshold by
400 ms, and the largest EV step moves it by nothing.

## The instrument bug the first EV run found, which is why `1343` is void

`1343` printed **mean RGB 109 149 120 for EV 0, 1 and 3 alike** — a clean null
result from an axis that had never been varied. Each settle trial calls
`cam_begin()`, which writes `CAM_REG_SENSOR_RESET`, which is exactly what makes
the trials independent — and it also puts EV back to its default. The setting
under test was being wiped by the reset that the test design depends on.

Fixed by re-applying EV **inside** every trial, after the reset and before the
capture. That costs a `cam_wait_idle()` of quiet between reset and trigger which
the plain sweep does not spend, so this table's absolute thresholds sit below
the other table's. It is constant across the three rows, which is what the table
compares. `1350` is the run with the fix, and there EV does move the mean.

Keeping `1343` because a null result from an un-varied axis is the exact shape of
a false negative, and one that would have read as "exposure is not the variable".

## The correlation that pointed at the hand

Every probe run has always measured the ambient brightness — it is the first row
of the image-controls table — but it sat sixty lines below the sweep and nobody
put the two beside each other. Across every archived run:

| run | scene mean RGB | mean | fault |
| --- | --- | --- | --- |
| 08-22 14:01 **covered** | 9 7 8 | **8** | **present** |
| 08-20 19:30 | 73 67 26 | 55 | **present** |
| 08-20 18:40 | 77 78 44 | 66 | **present** |
| 08-21 05:57 | 91 111 68 | 90 | absent |
| 08-22 13:34 | 111 148 122 | 127 | absent |
| 08-22 13:43 | 111 148 123 | 127 | absent |
| 08-22 13:27 | 112 149 123 | 128 | absent |
| 08-22 13:50 | 120 154 124 | 133 | absent |

**Perfect rank separation, no counterexample.** The three darkest scenes have it
and the five brightest do not, and the boundary sits somewhere between mean 66
and mean 90.

The last row of that table was written before the covered run existed. It is the
row that made the prediction and the covered run is the row that tested it, so
the seven observational rows and the one intervention are not the same kind of
evidence — the hand is the one that counts.

The mechanism that would explain it: auto-exposure integrates longer in a dark
room, the sensor needs one whole integration of not being triggered before it
writes its first frame, and 50 ms of quiet is enough for a bright frame and not
for a dark one. That fits every observation #27 records — including m9's forty
triggers at 50 ms never escaping the constant fill, and `settle300` escaping it
on the first try.

**#27 says "not darkness" and it is right about the claim it was arguing
against.** `08 01` is the ArduChip's empty-FIFO fill, not a dark picture, so the
blank is not underexposure. But *"the image is dark"* and *"the exposure is
long"* are different claims and only the first was ruled out.

Two observed runs is not a measurement, and both of them were the same evening.
So the prediction was written down first and then tested: **cover the lens and
run the probe.** It took ten seconds and the fault came back.

## What this means for the fix

**No `sleep_ms(N)` is the fix.** Any constant is a number measured in some
particular room, and this directory now contains two rooms whose correct
constants differ by 400 ms — with no reason to believe 400 is the end of the
range rather than the darkest desk anybody has tried. A 400 ms sleep would also
cost every bright-room capture 400 ms it does not need.

`ft_acquire()` has to **wait for a non-constant frame with a bounded timeout**:
trigger, check for the ArduChip's `c80a8564` empty-FIFO fill, re-trigger, and
give up with an error after a ceiling well above any plausible integration. That
turns the room's brightness into latency instead of into a blank frame, and it
turns a silent wrong answer into a reportable one.

`cam_frame_is_constant()` already exists and is what the sweep counts with, so
the check is written. What is not written is the retry loop in the acquire path
and a decision about what the ceiling should be.

The scene mean is now printed on the sweep's own table so no future run has to
be reconstructed out of a different section.
