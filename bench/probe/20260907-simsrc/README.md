# The ArduChip will feed the pipeline frames the sensor never saw

**2026-09-07, three boots of `forgix_cam_simsrc` and two of `m9`.** Not a bench:
no cue schedule, no enrolment, no held-out set, no accuracy. It answers one
hardware question, then spends the answer once, and the five logs here are the
whole of it.

| log | what it was for |
|---|---|
| [`discover-150.log`](discover-150.log) | the first run, which found the register. **150 MHz** — the probe took no clock then, and the appliance ships at 320, so it answered about a board nobody runs |
| [`verify-320-boot1.log`](verify-320-boot1.log) | 320 MHz, and the split trigger/collect m9 actually uses |
| [`verify-320-boot2-after-power-cycle.log`](verify-320-boot2-after-power-cycle.log) | the same again after a **hub power cycle**, which is the only thing that takes the ArduChip's own power down |
| [`m9-integration-smoothed.log`](m9-integration-smoothed.log) | the real scoring chain on it, via m9's new `'M'` key — and the one that shows why the z column is not readable with smoothing on |
| [`m9-integration-no-smooth.log`](m9-integration-no-smooth.log) | the same with `--no-smooth`. **The result** |

The question is [#30](https://github.com/kazunori279/fpga-open-vocab/issues/30)'s,
asked from underneath. Every drift measurement in this repo asks whether scores
move while the scene is still, and none of them can separate a camera that is
re-deciding from anything else in the chain that might be. A frame source that
cannot drift collapses that into one bench.

## The answer is yes, and it is `0x06` and not `0x05`

| register | wrote | read back | six captures | verdict |
|---|---|---|---|---|
| `0x05` bit[7], "data source" | `0x80` | `0x80` | **0 of 6 good** | takes the write, breaks capture |
| `0x06` bit[7], "counter pattern" | `0x81` | `0x81` | **6 of 6, one distinct crc32** | **this is the one** |

`0x06` gave `1608eb14` six times running, `flat 0` — so it is a real pattern and
not the blanking fault, which cam.h's `cam_frame_is_constant()` would have
caught as a single repeated 16-bit value. Mean RGB 6 63 63. **Six bit-identical
frames is the exact property #30 needs**, and it is the one the probe was
written to test for rather than "does the picture look synthetic", which a crc
cannot be fooled about.

The live baseline earned its place: six captures before any write, six distinct
crc32s, mean RGB 106 123 90. The camera came back afterwards — six more, six
distinct, mean RGB 137 135 137 — so neither write left the board in a state the
next run would inherit.

## Why this was a probe and not a hotkey

`firmware/cam.h`'s register block is transcribed from ArduCAM's own driver "and
not from the application note, which disagrees with it". `0x05` and `0x06` are
in the application note only: checked on 2026-09-07 against
`github.com/ArduCAM/Arducam_Mega`, `src/Arducam/ArducamCamera.c`, **the vendor
driver never reads or writes either register.** There was no code anywhere
exercising this, so nothing in the scoring path was allowed to depend on it
until a boot printed a verdict. The four outcomes were named in the probe's
header before it ran; `0x05` landed on outcome 4 and `0x06` on outcome 1.

The app note being half wrong is worth keeping. It described `0x05` as the data
source and `0x06` as a counter pattern, and on this module — fpga rev 32,
firmware 2023-03-03, sensor id `0x82` — the counter is the one that produces
frames and the data source is the one that stops them.

## The three things that were unverified are now verified

The first run left three ways this could still have been useless. All three were
closed the same afternoon, and **the pattern is `1608eb14` in every one of
them**:

| question | why it mattered | answer |
|---|---|---|
| Does it survive **320 MHz**? | the discovery boot ran at 150 and the appliance ships at 320 | **yes** — `1608eb14` |
| Does it survive **`ft_pipeline()`'s split** `cam_trigger()` / `cam_collect()`? | m9 never calls `cam_capture()`; it triggers, spends ~265 ms encoding, then collects. A source regenerated per trigger and one latched in the FIFO look identical in the serial form and do not in the split one | **yes** — `1608eb14`, six for six, with a real 265 ms between the two calls |
| Is it the same **across boots**? | if the counter is seeded by anything but the FPGA's own reset, a reference enrolled on one boot is not valid on the next | **yes** — `1608eb14` after a hub power cycle, which is what takes the ArduChip's power down; a reflash does not |

The probe now sets the clock from the same `FGX_SYS_KHZ` cache variable m9 is
built with, so this cannot silently regress to 150 again.

## What this does not say

It does not say the drift is or is not the camera. **It says the experiment that
would tell you is now possible**, which it was not this morning.

One thing to carry into that: the pattern's mean RGB is **6 63 63**, which is
dark and green. Whether a scoring chain fed a frame nothing like a photograph
produces embeddings worth comparing is a separate question from whether the
frame is fixed, and this probe does not touch it.

## Wired into m9 the same day: `m9-integration-*.log`

`'M'` toggles it. Both logs are the same two-query set (`cup`, `book`) with the
background frozen after 8 frames and `'M'` pressed at frame 14; the only
difference is z smoothing.

| log | smoothing | frames 18-27 |
|---|---|---|
| `m9-integration-smoothed.log` | on (default) | z converges `-5.33 → -16.07`, still moving at the last frame |
| `m9-integration-no-smooth.log` | `--no-smooth` | `book -6.03  cup -18.44`, **identical to the last decimal, ten frames running** |

Read the second row and not the first. **The convergence in the smoothed run is
the z EMA filling up, not drift** — the same asymptote a step input produces on
any first-order filter, and it would appear on this arm even if the chain were
perfect. It is in the table because it is the trap: the first read of that run
looked like a walk.

The flat row is the result. Everything downstream of the sensor is
deterministic, so a walk in a soak is the camera or the scene and is not
capture, the burst, the T8, the head, or the cosine.

The open question this probe left — whether the encoder does anything sane on a
frame that is not a photograph — is answered too, and the answer is yes:
`cos cup -0.146 book -0.134`, separated, and in the same range as the live
cosines in the same boot (-0.09 to -0.11). Not degenerate. **On this arm judge
the cosines, not the z**, which is why the board prints them.

### Two ways to void a run on this arm, both hit before the logs above

- **`'H'` unfreezes.** The background must be frozen or a never-changing frame
  drags the background onto itself and z decays to zero by construction. But
  `demo.py` sends hold ON by default, so `'H'` is the wrong key here. The board
  now reads `bg_hold` and prints which state it is actually in rather than
  advising a keypress.
- **A reflash does not clear `0x06`.** One run was void because the board booted
  already on the pattern: exposure ramp `44 44 44` for 174 frames, `EXPOSURE
  NEVER SETTLED`, background spread exactly `±0.0000`. Same reason a reflash
  does not re-run the across-boots test in the table above — the ArduChip stays
  powered. `cam_begin()` now clears it unconditionally at boot, which was
  verified by ending a run on the pattern and reflashing without a power cycle:
  the next boot came up live, ramp `98 100 103 …`, settled after 13 frames,
  `expose 37 ms`. That log was overwritten before it was archived; the lines are
  quoted in `firmware/cam.c` at the call site.

## Reproducing it

```
cmake --build firmware/build --target forgix_cam_simsrc
uv run --script host/bootsel.py --flash firmware/build/forgix_cam_simsrc.uf2
```

Then read the CDC port; it takes about 40 s. For the across-boots line, run
`host/bootsel.py --power-cycle` and flash again — a reflash on its own is not a
power cycle and does not test what that line claims.

The probe restores both registers itself and reports whether the camera came
back; if it says it did not, USB out for ten seconds. **Flash m9 back
afterwards** — `host/bootsel.py --flash firmware/build/forgix_m9.uf2`.
