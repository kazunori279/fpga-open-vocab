# The 2026-08-22 settle sweep, and what it did not reproduce

Four `forgix_cam_probe` runs in twenty-five minutes, chasing
[#27](https://github.com/kazunori279/fpga-open-vocab/issues/27) — the
repeat-capture matrix that stopped matching its 2026-08-03 record. **None of the
four reproduced the fault.** That is the result, and the useful part of it is
what the four rule out.

| log | what it was | matrix | settle 0 | scene mean RGB |
| --- | --- | --- | --- | --- |
| `cam_probe-20260822-1327.log` | first run, board up for days | matches 08-03 | 3/3 up, 3/3 down | 112 149 123 |
| `cam_probe-20260822-1334-vbuscycle.log` | after `bootsel.py --power-cycle` | matches 08-03 | 3/3, 3/3 | 111 148 122 |
| `cam_probe-20260822-1343-ev.log` | first EV axis, **void — see below** | matches 08-03 | 3/3, 3/3 | 111 148 123 |
| `cam_probe-20260822-1350-ev2.log` | EV axis, fixed | matches 08-03 | 3/3, 3/3 | 120 154 124 |

## What is ruled out

**A cold power cycle does not cause it.** `--power-cycle` drops VBUS on the
board's hub port, so the camera module loses 3V3 with it. The run straight after
that is `1334-vbuscycle` and it is indistinguishable from the run before. So the
state #27 is about is not "the sensor has just been powered".

**The EV register cannot emulate it.** `CAM_REG_EV_CONTROL` raises the
auto-exposure target, which is the one lever on the board that could lengthen an
integration without anybody touching the desk. At EV 0, 1 and 3 the settle sweep
reads 3/3 at 0 ms in all three rows and the frame mean moves by two counts. The
lever exists and is far too short.

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

## The correlation the four runs did produce

Every probe run has always measured the ambient brightness — it is the first row
of the image-controls table — but it sat sixty lines below the sweep and nobody
put the two beside each other. Across every archived run:

| run | scene mean RGB | mean | fault |
| --- | --- | --- | --- |
| 08-20 18:40 | 77 78 44 | 66 | **present** |
| 08-20 19:30 | 73 67 26 | 55 | **present** |
| 08-21 05:57 | 91 111 68 | 90 | absent |
| 08-22 13:27 | 112 149 123 | 128 | absent |
| 08-22 13:34 | 111 148 122 | 127 | absent |
| 08-22 13:43 | 111 148 123 | 127 | absent |
| 08-22 13:50 | 120 154 124 | 133 | absent |

**Perfect rank separation, no counterexample, and only two of the seven are on
the fault side.** The two darkest scenes have it and the five brightest do not.

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

Two runs is not a measurement, and both of them are the same evening. The
decisive test is the cheap one and it is not automatable from here: **cover the
lens and run the probe.** If the fault appears with a hand over the camera, the
threshold is an exposure time, and no `sleep_ms(N)` constant is the fix — the
acquire has to wait for a non-constant frame with a bounded timeout, not for a
number somebody measured in a lit room.

The scene mean is now printed on the sweep's own table so no future run has to
be reconstructed out of a different section.
