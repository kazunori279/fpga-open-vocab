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
| `cam_probe-20260822-1508-control.log` | still covered, an hour later | fault, same 5 rows | 0/3, 0/3 | 8 4 8 |
| `m9-20260822-1458-covered-fixed.log` | still covered, **m9 with the fix** | — | escaped at 400 ms | 8 5 8 |

`1508` is the control for the fix below: it says the covered lens still
reproduces the fault on the instrument, at the same time of day the fixed `m9`
run was getting pictures out of it.

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

## The fix, and the two wrong ones that came first

**No `sleep_ms(N)` is the fix.** Any constant is a number measured in some
particular room, and this directory contains two rooms whose correct constants
differ by 400 ms.

So the first attempt made the quiet back off — 50, 100, 200, 400, 800 — and it
did not work. Nor did the second, which took the cap to 4 s:

```
camera : exposure ramp 5+100 5+200 5+400 5+800 5+1600 5+3200 5+4000 5+4000
                       5+4000 5+4000 5+4000
camera : still a constant fill (08 01) after 11 frames, quiet up to 4000 ms
```

Twenty-five seconds of silence bought nothing, while `cam_probe` on the same
covered lens minutes either side got a picture at 400 ms every time.

**The difference is that every trial of the sweep on this page calls
`cam_begin()` first.** What the table above measures is *reset, then N ms
untriggered*, and `ft_acquire()` was reading the number off as though it
measured *N ms untriggered*. A sensor triggered while it cannot answer stays
stuck, and silence afterwards is not a reset. The matrix fits too: row 5 escapes
at `settle300` with no reset, but by then `cam_probe`'s bus-rate sweep has had
the sensor producing frames for seconds. **Cold and stuck is a third state, and
it is the one m9 boots into.**

The working recovery is the sweep's own sequence — `cam_begin()`,
`cam_image_defaults()` again because the reset discards them, then the settle
inside `cam_trigger()` where the sweep puts it, before the FIFO clear. It runs
only after a frame has come back constant, so a lit-room boot takes the
untouched vendor recipe and pays nothing:

```
camera : exposure ramp 5+100 5+200 5+400 5 6 7 7 6 6 6 7 7 7 ...
```

Three tries, escaped at 400 ms — the number this page measured — and then nine
frames scored instead of a silent fall back to the flash test vector.
`m9-20260822-1458-covered-fixed.log` is that run. The mean stays `8 5 8` and it
reports `EXPOSURE NEVER SETTLED`, which is correct: the lens really is covered.

### What fell out of getting it wrong

`cam_begin()` writes `CAM_REG_SENSOR_RESET`, which puts the sensor back to VGA,
and it does **not** clear `cam.c`'s `last_fmt` / `last_mode`. So a
`rewrite = false` recipe after a reset skips the `CAPTURE_RESOLUTION` write on
the grounds that the mode has not changed, when only the record of it survived:

```
!! FIFO length 614400, buffer is 135168
```

640 × 480 × 2 exactly. Every recipe in this page's sweep is `rewrite = true`,
which is what makes `cam_begin()` safe to call per trial there, and the comment
in `cam.c` claiming `-1` "after a reset or a `cam_begin()`" describes an
intention the code does not carry out. Filed as
[#29](https://github.com/kazunori279/fpga-open-vocab/issues/29) rather than
patched, because clearing the cache makes every post-reset capture rewrite
CAPTURE_RESOLUTION, which is the write `rewrite = false` exists to avoid, and
only the board can say whether that costs anything.

### Not measured

**A lit room, with this firmware.** The recovery is gated on a constant frame so
a working camera should never enter it, but "should" is not a run. That check
needs the lens uncovered and has not been done.

The scene mean is now printed on the sweep's own table so no future run has to
be reconstructed out of a different section.
