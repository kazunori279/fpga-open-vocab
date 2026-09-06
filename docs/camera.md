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

## The way round it, which the driver has half-built

The ArduChip can pass an I²C transaction through to the sensor die, where the
real AEC/AGC registers live and **are** readable.

| reg | type | what | used today |
|---|---|---|---|
| `0x0A` | RW | I²C device address | **yes** — `cam.c` writes `0x78` |
| `0x0B` | RW | I²C register address, upper 8 bits | no |
| `0x0C` | RW | I²C register address, lower 8 bits | no |
| `0x07` bit[0] | RW | write 1 to **initiate an I²C direct read** | no |

`cam_begin()` writes the device address and stops there, so the passthrough is
configured and never fired. Completing it is the only route to a genuine
readback of what the exposure loop is doing, and it is what
[#33](https://github.com/kazunori279/fpga-open-vocab/issues/33)'s first item
was asking for. Which sensor registers to read then depends on the die, which
`0x40` identifies (this module reads `0x82`).

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
only one of those three that tests the across-boots claim. Nothing about it is
unverified any more. What is left is wiring it into `m9.c`.

Its mean RGB is 6 63 63, dark and green. Whether the scoring chain produces
anything worth comparing on a frame that is nothing like a photograph is a
different question from whether the frame is fixed, and no probe has asked it.

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

1. ~~**Add a simulated-data mode**~~ — **probed and verified on 2026-09-07, and
   it is `0x06` bit[7], not `0x05`.** What is left is wiring it into `m9.c` and
   running the scoring chain on it, which separates camera drift from every
   other drift in one bench.
2. **Finish the I²C passthrough** (`0x0B`, `0x0C`, `0x07` bit[0]). It is the only
   readback of the exposure loop that exists, and #33 has been waiting on it.
   [`20260907-camlock-cold/`](../bench/soak/20260907-camlock-cold/) gave it a
   sharper question than it had: locking exposure and gain did not reduce the
   walk, and three of eight locked runs moved their level by 14 to 18 after the
   freeze, which the AWB drift in `cam.h:245` does not account for.
3. ~~**Write explicit exposure and gain**~~ — **probed on 2026-09-07 and both
   groups respond**, so `'L'` can lock rather than hope. What is left is doing
   it, and the two open items above the section are part of the job: the loops
   do not hand exposure back, and one run in four saw no response.
4. **Try the sensor-only power cycle** (`0x02`) against #32's cold-boot count.
