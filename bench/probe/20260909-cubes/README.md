# The LED brightness that "stopped working" was never wired up in this path

*2026-09-09, one boot of `forgix_m9`, 2,595 frames, two queries, nothing
enrolled. Not a bench: no cue schedule, no held-out set, no ground truth. It
exists because it settled an argument about the LED.*

The report was that the LED sat red and never changed brightness, and that the
prediction therefore looked broken. Both halves of that are worth separating,
because the first is true, the second is not, and the reason they came apart is
that `m9.c` has three different functions that drive the LED.

## The brightness really is constant, by construction

With nothing enrolled and no gate/class roles, the frame loop reaches
`led_map(z, thr)`, which calls `led_duty((thr > 0) ? z / thr : 0, 1.0f)`. The
second argument is the brightness and it is the literal `1.0f`. Only the hue
carries information in this path, and it encodes *something matched* against
*nothing matched* — not which class won.

The log agrees to the frame: **2,550 of 2,595 frames have a maximum channel of
exactly 255**. The 45 that do not sit between 242 and 254, which is the hue
interpolation rounding at intermediate angles, not a brightness change. There
are 38 distinct hues in the run and one brightness.

## The `b` field is the fingerprint

`printf(" b%.2f")` is inside `if (m21 || (n_gate && n_class))`, so a log either
carries a `b` on every qualifying frame or on none. This run has **zero**.
`bench/cue/m9_cue-20260908-0733.log` has 413, across 69 distinct values.

That is the whole reconciliation. The changing brightness remembered from the
bench runs was real and was `led_ref()`, which is reached only once two classes
are enrolled and which computes brightness as `de / (de + d)`. This run never
enrolled anything, so it never got there.

## The classifier was working the entire time

green cube 2,235 (86.1%), red cube 185 (7.1%), no match 175 (6.7%). The verdicts
are not scattered — they run in clean alternating segments that track what was
actually held up. There is no ground truth here, so 86% is a tally and not an
accuracy, and the two cubes were not presented for equal time.

## Files

- [`demo-cubes.log`](demo-cubes.log) — the whole run. Stopped with Ctrl-C, which
  is why the last frame is mid-scene; `--leave-running` kept the board out of
  BOOTSEL on the way out.
