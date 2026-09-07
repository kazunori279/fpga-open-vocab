# The camera, and what it will not tell you

The sensor is an **[Arducam Mega](https://docs.arducam.com/Arduino-SPI-camera/MEGA-SPI/MEGA-SPI-Camera/)**
— a module with an image sensor behind an FPGA bridge Arducam calls the
ArduChip, spoken to over SPI. `firmware/cam.{c,h}` is the driver; this page is
what the vendor documentation says the part can do, checked against what the
driver actually uses.

It exists because of [#33](https://github.com/kazunori279/fpga-open-vocab/issues/33).
The board spent weeks unable to say whether its own auto-exposure loop had ever
engaged, and the reason turned out not to be an oversight in the firmware.

**Source for every register below:** the
[Mega SPI Camera Series Application Note](https://blog.arducam.com/downloads/datasheet/Arducam_MEGA_SPI_Camera_Application_Note.pdf),
September 2023, section 4 "Register Table". **This page is a reading of the
datasheet against the source, and two rows of it have since been run on the
board** — `0x05`/`0x06` and `0x31`–`0x35`, both on 2026-09-07, and in each case
the datasheet turned out to be partly wrong. Those rows say so and link their
logs. Everything else here is still unrun, and the "used today" columns mark
what the driver touches.

[← back to the README](../README.md) · [architecture](architecture.md) ·
[building](building.md) · [monitor](monitor.md) ·
[will it work for you](fit.md) · [bring-up log](bring-up-log.md)

---

## The one fact that explains #33

**Almost the entire control surface is write-only.**

| range | what | type |
|---|---|---|
| `0x20`–`0x2A` | format, resolution, brightness, contrast, saturation, EV, white-balance mode, effects, sharpness, autofocus, JPEG quality | **WO** |
| `0x30` | auto gain / auto exposure / auto white balance on-off | **WO** |
| `0x31`–`0x35` | manual gain, manual exposure | **WO** |
| `0x40`–`0x49` | sensor id, firmware date, sensor state, FPGA revision | RO |
| `0x00`–`0x0C` | test, frame count, power, memory control, data source, resets, I²C passthrough | RW |

So the board can tell the camera what to do and cannot ask it what it is doing.
Every adjustment is fire-and-forget. **Write-only is about the readback and not
about the effect** — `0x31`–`0x35` read back as nothing and
[demonstrably work](#0x310x35--exposure-and-gain-can-be-set-and-this-has-now-been-run),
which is why the only way to check any of this row is the pixels. `cam.h` already suspected this —

> One register, three switches, selected by the low bits… and there is no way to
> read back which of the three you last touched — hence `cam_probe.c`'s sweep
> rather than a query.

— and the datasheet confirms it: `0x30` is typed `WO`, which is why
`cam_read_reg(0x30)` returns `00` no matter what was written. **That is not a bus
fault and not a driver bug. There is nothing there to read.**

This is the structural half of #33. The firmware could only infer the state of
the auto-exposure loop by watching the pixels change over a ramp, which is
exactly what `ft_acquire()` does and exactly why its answer is a judgement call
that can be wrong in two directions.

## The way round it, and it works

The ArduChip can pass an I²C transaction through to the sensor die, where the
real AEC/AGC registers live and **are** readable. This has now been fired and
measured; the addresses below are in `firmware/cam.h`.

| reg | type | what | used today |
|---|---|---|---|
| `0x0A` | RW | I²C device address | **yes** — `cam.c` writes `0x78` |
| `0x0B` | RW | I²C register address, upper 8 bits | measured, not yet in `cam.c` |
| `0x0C` | RW | I²C register address, lower 8 bits | measured, not yet in `cam.c` |
| `0x07` bit[0] | RW | write 1 to **initiate an I²C direct read** | measured, not yet in `cam.c` |
| `0x48` | RO | the byte the read returned | measured, not yet in `cam.c` |

Write the die address to `0x0A`, the sensor register to `0x0B`/`0x0C`, fire
`0x07` bit 0, wait idle, read `0x48`.

**The die is an OV3640**, not whatever `0x40`'s `0x82` was meant to mean:
`0x300A`/`0x300B` return `0x36`/`0x4C` through this path, on six boots. And the
readback is real — the sixteen bits written into the WO exposure block at
`0x33`–`0x35` come back out at `0x3002`/`0x3003`, byte for byte, on two
consecutive boots, with a handle on the exposure surface held either side of the
sweep and the product ID standing as a positive control.
[`bench/probe/20260907-i2crec/`](../bench/probe/20260907-i2crec/) has the runs;
[`bench/probe/20260907-i2cpass/`](../bench/probe/20260907-i2cpass/) has the
first firing and one conclusion it got wrong.

**Firing the passthrough costs nothing.** Checked at one fire, at thirty-six,
and at 3072, with a six-rung exposure ladder either side of each.

One caveat the shipping path has to carry: the manual exposure surface comes up
deaf on most boots, with nothing fired — 32 of 33 in
[`bench/probe/20260907-lockrate/`](../bench/probe/20260907-lockrate/), and 9 of
those 33 were deaf and not deaf by turns inside ten seconds. **No register write
is known to clear it.** `0x07` bit 7 was written up as the cure on a single observation and
[`bench/probe/20260907-awb/`](../bench/probe/20260907-awb/) then walked the same
ladder on seven boots without it recovering one of them; bit 1 recovered one,
bit 6 broke capture every time it was tried, and re-running the whole of
`cam_begin()` never worked.

What that directory did establish is what the fault *is*. Ask the die instead of
the picture — write an exposure, then read `0x3002`/`0x3003` back over a few
seconds — and the deaf boots split into two shapes. On one the die drags the
value back to whatever its own AE loop had settled on, so the loop is still
running with the mask at zero and the frame is parked at mid-scale because that
is where a working AE loop parks a frame. On the other the die holds the written
value perfectly and the picture still does not move. Both look identical from
the host: a flat ladder around luma 110–120.

Neither can be fixed from here. The passthrough is a read path and there is no
measured way to write the die's own AE enable. What a run *can* do is find out,
and `cam_exposure_lock_check()` is that call — but only after it has captured a
frame. Before the first one the AE loop has nothing to revise, the write always
appears to stick, and the check returns `CAM_LOCK_UNTESTED` rather than a `held`
that means nothing.

## Registers the driver does not use, in the order they are worth something

### `0x06` bit[7] — the sensor can be taken out of the loop, and this has now been run

The ArduChip will feed the pipeline frames the sensor never saw. This is the
control the drift work has never had. Every drift measurement so far asks
whether scores move while the scene is still, and cannot separate a camera that
is re-deciding from anything else in the chain that might be. **Run the whole
scoring path on frames that cannot drift; if `common` still moves, the drift is
not the camera.** That is [#30](https://github.com/kazunori279/fpga-open-vocab/issues/30)'s
question answered without the camera in the experiment.

**This is the one row on this page that is no longer a reading of the
datasheet.** `firmware/cam_simsrc.c` ran it on 2026-09-07 —
[`bench/probe/20260907-simsrc/`](../bench/probe/20260907-simsrc/) — and the app
note is half right:

| register | app note | what the board did |
|---|---|---|
| `0x05` bit[7] | `0` camera data, `1` **simulated data** | took the write, read back `0x80`, and returned **zero good captures** |
| `0x06` bit[7] | a 32-bit counter pattern | **six captures, one distinct crc32**, not a constant frame |

So the usable register is `0x06`, not the one named for the job. Six
bit-identical frames is the property #30 needs; the live baseline in the same
boot gave six distinct ones, and the camera came back afterwards.

**The pattern is `1608eb14` at 150 MHz and at 320, under `cam_capture()` and
under `ft_pipeline()`'s split trigger/collect 265 ms apart, and after a hub
power cycle** — a reflash leaves the ArduChip powered, so the power cycle is the
only one of those three that tests the across-boots claim.

#### It is wired into `m9.c` as `'M'`, and the scoring chain is flat on it

The same day. `'M'` toggles `cam_frame_source_synth()`; the run is in
[`bench/probe/20260907-simsrc/`](../bench/probe/20260907-simsrc/) alongside the
probe logs. Two things came out of it that the probe could not have found.

**The scoring chain does not drift on a fixed frame.** With `--no-smooth` and
the background frozen, ten consecutive frames scored `book -6.03  cup -18.44` —
identical to the last decimal, no variation at all. Everything downstream of the
sensor is deterministic, so a walk seen in a soak is the camera or the scene, and
is not capture, PIO burst, the T8, the head, or the cosine. That is the control
[#30](https://github.com/kazunori279/fpga-open-vocab/issues/30) was missing.

Two things have to be right before that number means anything, and both are
enforced in the firmware rather than left to the operator:

- **The background has to be FROZEN.** A tracking background converges onto a
  frame that never changes, so z falls to zero on its own and the flatness is a
  tautology. The board reads `bg_hold` on the first synthetic frame and prints
  which state it is actually in. Note that `demo.py` sends hold ON by default, so
  `'H'` *unfreezes* — pressing it out of habit here breaks the experiment.
- **z smoothing has to be off.** With `zema` on, z converges asymptotically
  (`-5.33 → -16.07` over ten frames in the first run) and that curve is the EMA
  filling up, not drift. `--no-smooth` is what makes the column readable.

**The encoder is not degenerate on the pattern**, which was the open worry — mean
RGB 6 63 63 is nothing like a photograph. It reads `cos cup -0.146 book -0.134`:
separated from each other, and in the same range as the live cosines in the same
boot (-0.09 to -0.11). The cosines are what to judge on this arm, not the z.

The liveness check that warns when frames go bit-identical is suppressed under
`'M'` and replaced by its inverse: ten synthetic frames that are *not* identical
mean `0x06` did not take, and the run cannot be used.

**A reflash does not clear `0x06`.** One whole run was void before this was
found: the board booted already on the pattern, with the exposure ramp reading
`44 44 44` for 174 frames and a background spread of exactly ±0.0000. The
register is on the ArduChip, which neither a reflash nor an RP2354 reboot powers
down. `cam_begin()` now writes the source back to the sensor unconditionally,
right after the device-address write — one write, not a read-then-fix.

### `0x31`–`0x35` — exposure and gain can be *set*, and this has now been run

| reg | field |
|---|---|
| `0x31` / `0x32` | manual gain [9:8] / [7:0] |
| `0x33` / `0x34` / `0x35` | manual exposure [19:16] / [15:8] / [7:0] |

The `'L'` hotkey today switches the auto loops off through `0x30` and relies on
whatever value they last converged to staying put. `20260825-camlock/` found
that it does not hold the white balance. Writing an explicit value turns that
hope into a lock, and makes exposure reproducible **across** runs rather than
merely constant within one — which is the quantity `bench/` has never been able
to hold still.

**`firmware/cam_manexp.c` ran it on 2026-09-07 —
[`bench/probe/20260907-manexp/`](../bench/probe/20260907-manexp/) — and both
groups respond.** `firmware/cam.h:246` used to deny it outright and has been
corrected. In the decisive run the same eight exposure values produced the same
eight luma readings under three different visiting orders: retrace gap 0, spread
across values 228.

Three measured caveats travel with that, and none of them is in the app note:

| what | measured |
|---|---|
| **exposure saturates early** | `0x400`, `0x1000` and `0x4000` all read 233. The useful ladder is below `0x400` at room light |
| **the `0x33` nibble goes dark, not bright** | `0x10000` and `0x40000` both read 5. Either that byte is not `[19:16]` or the field is narrower than documented. Working range measured: `0x00000`–`0x0FFFF` |
| **gain is not monotone** | it peaks at `0x010`, dips through `0x100`, then saturates at `0x3ff` — the same curve on two runs at two light levels. A wanted gain has to be measured off the curve, not assumed |

And one behaviour that constrains any caller: **switching the auto loops back on
does not undo a manual write.** After a manual exposure of `0x010` the frame sat
at luma 11, and twenty captures with all three loops running left it at 11; it
had been 133. That is `cam.h:245`'s note in reverse, and it means anything that
writes these registers owns the route back as well as the route out.

One run of four saw no exposure response at all — 32 captures flat — on an
unchanged code path, after a reflash rather than a power cycle. Unexplained,
with a cheap test nobody has run. So: these registers respond, and there is one
observed way for them to appear not to.

### `0x02` — the sensor die can be power-cycled on its own

| bit | field | 1 | 0 |
|---|---|---|---|
| [2] | `cam_power_en` | normal | power off |
| [1] | `cam_pwdn` | sleep | normal |
| [0] | `cam_rst_n` | normal | reset |

Default `0x05`. [#32](https://github.com/kazunori279/fpga-open-vocab/issues/32)
wants a cold-boot count — one scene, N boots, the ramp only — and today that
costs one board power-cycle per sample and therefore a morning. If the fault
lives in the sensor's own power-on rather than the board's, this register
collects the same samples in minutes. Whether it does is itself the test.

### `0x01` — frames can be burst into the 8 MB cache

`0`–`254` means frames = value + 1; `255` means the memory is full (8 MB). The
driver captures one frame at a time. The exposure ramp could be kept as images
rather than as a row of numbers in a banner.

## Two things in the driver worth knowing about

**`0x07`'s bit names are the library's, not the hardware's.** `cam.h` has
`CAM_REG_SENSOR_RESET 0x07` and `CAM_SENSOR_RESET_ENABLE (1 << 6)`. The
datasheet says:

| bit | datasheet |
|---|---|
| [7] | reset cache (SDRAM, 8 M) |
| [6] | **reset FPGA** |
| [1] | reset I²C |
| [0] | initiate an I²C direct read |

ArduCam's own driver uses the same name for bit 6, so the code matches the
library it was transcribed from. The reset does work — `cam_begin()`'s comment
about the sensor returning to its default VGA is [#29](https://github.com/kazunori279/fpga-open-vocab/issues/29)
and was observed — so this is a naming mismatch to be aware of when reading the
datasheet next to the source, not a defect.

**The 128×128 mode code is right for this module and would be wrong for a 5MP
one.** The datasheet lists resolution `11` (`0x0b`) as 128×128 in *both* the 3MP
and 5MP columns, and `1` as 320×240. `cam_mode_128()` returns `0x0b` when the id
is below `0x85` and `0x01` when it is not. This module reads `0x82`, so it takes
the `0x0b` branch and is correct. The other branch has never run here and, read
against this table, would capture 320×240. Unverified — Arducam's `legacyMode()`
remapping is a library behaviour and the datasheet may not describe what the
library does.

## What the driver already gets right, and why it is written down

Three of these cost a debugging session each and are in `cam.h` at length. The
short forms:

- **8 MHz for register writes, whatever you use for pixels.** The datasheet
  recommends 8 MHz SCLK. A 16 MHz register *write* lands — right FIFO length,
  sensor IDLE, `CAP_DONE` on time — and produces a black frame. Only the pixels
  report it.
- **Never rewrite `CAM_REG_CAPTURE_RESOLUTION` with the value it already holds.**
  It blanks the *next* capture, silently and with every status bit correct.
- **Every wait is bounded.** [#8](https://github.com/kazunori279/fpga-open-vocab/issues/8)
  was an unbounded PIO transfer loop that spun the core until the 8 s watchdog
  rebooted the board. There is now a 2,000 µs stall deadline, and
  `CAM_XFER_STALL_US` is public because a margin quoted without its deadline is
  not a figure.

## What to do about it

In the order the value falls. The hold this list used to carry — nothing before
[#30](https://github.com/kazunori279/fpga-open-vocab/issues/30)'s session
finished, because its firmware was pinned by md5 — came off on 2026-09-07 when
that session completed and was read out:

1. ~~**Add a simulated-data mode**~~ — **done and spent on 2026-09-07. `0x06`
   bit[7], not `0x05`, and it is m9's `'M'` key.** 541 frames scored, `common`
   walk **0.00** against a live arm's 2.45
   ([`20260907-simsrc-vs-live/`](../bench/soak/20260907-simsrc-vs-live/)).
   Everything downstream of the sensor readout contributes exactly nothing to
   #30's walk. The suspect is now "at or before the sensor", and since locking
   exposure and gain did not reduce it either, **AWB is what is left** — which
   `'L'`'s second press turns off and no session has run.

   The WB bit was then followed into the die on 2026-09-07
   ([`20260907-awb/`](../bench/probe/20260907-awb/)). Sweeping 1024 addresses
   from `0x3000` with white balance free against white balance locked, three
   visits an arm, **two of them separate and hold: `0x332b` (`10` free → `18`
   locked) and `0x33ca` (`4a`/`4e` free → `41` locked).** `0x332b` came back on
   four separate boots. So the bit reaches the die and is readable there, and
   `'L'`'s AWB arm can be written as something that gets checked rather than
   hoped at. What is *not* established is that the lock holds over a session:
   that stage has never reached a board with a live handle.
2. ~~**Finish the I²C passthrough**~~ (`0x0B`, `0x0C`, `0x07` bit[0]) —
   **measured and adopted on 2026-09-07.** `0x48` returns the exposure this
   firmware wrote, byte for byte, at the OV3640's `0x3002`/`0x3003`, on two
   consecutive boots
   ([`20260907-i2crec/`](../bench/probe/20260907-i2crec/)). ~~What is left is
   putting it in the driver~~ — **in the driver on 2026-09-07.**
   `cam_sensor_read()`, `cam_sensor_read16()` and `cam_exposure_lock_check()`
   are in `cam.c`; the check writes two exposures a factor of eight apart with
   the loops masked and reads `0x3002`/`0x3003` back, so `held` and `dragged`
   are now things a run can know about itself. That turns the sharper
   question [`20260907-camlock-cold/`](../bench/soak/20260907-camlock-cold/)
   asked — three of eight locked runs moved their level by 14 to 18 after the
   freeze, which the AWB drift in `cam.h:245` does not account for — into
   something answerable by reading the die instead of the pixels.

   **And the first thing it measured was how badly the lock fails**, over 33
   boots ([`20260907-lockrate/`](../bench/probe/20260907-lockrate/)): 32 of them
   dragged at least once after warm-up, only one never dragged, and 9 answered
   differently to the same question three times inside ten seconds. #33's "on
   some acquires" understates it, and the fault is not a property of the boot.

   **The second thing it measured was itself.** The check was wired into
   `cam_image_defaults()` and said `held` on all 33 boots, because the AE loop
   only revises exposure while frames are being clocked and nothing has clocked
   one that early. It now returns `CAM_LOCK_UNTESTED` before this boot's first
   frame and the call is gone from the boot path; the caller runs it after its
   warm-up. What remains is picking those call sites in `m9.c`.
3. ~~**Write explicit exposure and gain**~~ — **probed on 2026-09-07 and both
   groups respond**, so `'L'` can lock rather than hope. What is left is doing
   it, and the two open items above the section are part of the job: the loops
   do not hand exposure back, and one run in four saw no response.
4. **Try the sensor-only power cycle** (`0x02`) against #32's cold-boot count.
