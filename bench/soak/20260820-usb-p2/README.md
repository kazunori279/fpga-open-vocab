# The 2026-08-20 instrumented USB soak, on a new port and a new cable

Twenty `m9` runs of 200 frames at 280 MHz system / 140 MHz link, 18:04 to 18:31,
with **`host/usb_watch.py` running beside them for the whole window** — the
fourth owed item on [#9](https://github.com/kazunori279/fpga-open-vocab/issues/9)
and the first time any soak in this repo has had a bus-side record at all.

Two things changed before this run, both physical: the board moved from hub port
`2-1:1` to `2-1:2`, and its USB cable was replaced. `2-1` is the Mac mini's
internal two-port USB2 hub, the one that fronts the two front-panel USB-C ports
and the only hub in this machine with per-port power switching — see the
docstring at `host/bootsel.py:33`. The neighbour left behind on `2-1:1` is a
C-Media USB PnP Sound Device.

`usb_soak.sh` is the harness, and it is not `../bench_loop.sh`: it names neither
the CDC device nor the hub port, and it looks the port up once at the top while
the board is still on the bus. The reasoning is in its header.

## What it says about #9

**Nothing dropped off the bus.** Runs 1–7 each reported

```
usb: 0 outages, 0 ms off the bus, 0 re-attaches (#9)
```

and `usb_watch-20260820-p2.log` agrees from the other side: across the whole
37-minute window every `2-1:2` transition is an expected `2e8a:0009` ⇄
`2e8a:000f` app/BOOTSEL pair belonging to a reflash or a `picotool reboot`.

The stronger half is the accident in the middle. From **18:17:35 to 18:32:19 —
fourteen minutes and forty-four seconds — there is not one port transition**,
and the board's own frame counter runs **0 → 2608 without a reboot** (run 8
opens at frame 0, run 20 closes at 2608; runs 9–20 are `demo.py` attaching to a
board that never stopped). The port reads `0103 power enable connect
[2e8a:0009]` continuously through all of it. The next transition after 18:17:35
is at 18:32:19, and that one is mine: a deliberate VBUS cycle, identifiable
because `2-2:2` — the USB3 twin of the same physical socket — moves in the same
sample.

**This does not clear the issue, and it is important not to read it as if it
did.** The 08-15 event was one occurrence in eight runs; seven clean runs plus
one long free-run is not enough to distinguish "the cable and the port fixed it"
from "it did not happen this evening". What the evening does buy is the
instrument: a recurrence from here on is attributable, which the August logs
were not.

## Runs 8–20 are void, and not for a camera fault

They are here because the frame counter in them is the #9 evidence above. Their
*scores* are worthless, and the reason is worth reading.

Run 8's banner:

```
camera    : exposure ramp 4 5 4 4 4 4 5 4 5 5 4 5 4 5 5 5 5 4 4 5 5 5 5 5 5 4 4 5 4 5 5 5 5 5 5 4 4 5 4 4
camera    : live 128x128 RGB565, id 0x82, 16.0 MHz, expose 58 ms, read 16 ms, exposure settled after 41 frames
            mean RGB 7 0 7
```

**The room went dark.** The ramp never left the floor across all forty frames,
`ft_acquire()` ran out of its bound rather than converging, and mean RGB 7 0 7
is a black picture. Compare run 1, three quarters of an hour earlier in the same
room: the ramp climbed and settled in single digits of frames.

`ft_acquire()` refuses only two things — the wrong FIFO length, and a frame that
is *exactly* constant. A dark scene has sensor noise, so it is neither, and the
run started. Every score in run 8 reads `led 0/255 h0.00`. As the room kept
darkening the frames did eventually go exactly constant, and from run 9 onward
m9's per-frame guard fires on nearly every frame (`no usable frame off the
camera`). **The per-frame guard worked; the ramp is what let the run begin.**

So m9 scored twelve consecutive runs on a picture it could not expose, having
printed its own failure to converge and then proceeded anyway. That is filed
separately — it is a policy question about what `ft_acquire()` should do when
its `FLOOR`/`rose` test fails, and `frame.c:1500` argues on the record for
reporting rather than refusing.

## `cam_probe-20260820-1840.log` is a different finding

After the soak the camera would not come up at all, so `forgix_cam_probe` went
on the board to find out whether the module had died. It has not: sensor id
`0x82` agrees between bit-bang and PIO at every rate from 0.5 to 16 MHz, all
thirteen image controls return sensible mean RGB, and the closing f128 capture
is a real picture.

What the probe did flag is its own repeat-capture matrix, against what
2026-08-03 recorded in the comment at `firmware/cam_probe.c:250`:

```
  #  recipe      2026-08-03             2026-08-20
  0  as-was      7f04a4ea a picture     c80a8564 CONSTANT   <--
  1  as-was      c80a8564 CONSTANT      c80a8564 CONSTANT
  2  no-rewrite  4f90cd14 a picture     c80a8564 CONSTANT   <--
  3  flush       c80a8564 CONSTANT      c80a8564 CONSTANT
  4  norw+flush  859365a2 a picture     c80a8564 CONSTANT   <--
  5  settle300   2fb19c18 a picture     cfcd557b a picture
  6  everything  18628f40 a picture     f3639156 a picture
```

The picture crcs differ between the columns because the scene does; `c80a8564`
does not, and it is the same all-black buffer both days.

Rows 2 and 4 have `rewrite = false` and run with `last_fmt`/`last_mode` already
set, so they write **no registers at all** — and neither do rows 5 and 6. Four
rows, identical bus traffic, and the only thing separating the two that work
from the two that do not is `sleep_ms(300)` before the trigger. So this is not
the 08-03 redundant-write fault; it is the sensor needing a contiguous stretch
of *not being triggered* after `cam_begin()` before it writes its first frame,
and each trigger appears to restart it. Once it starts, it stays started: the
twenty-six captures in the image-controls section that follow, and the closing
f128, all use plain `CAM_RECIPE_VENDOR` with `settle_ms = 0` and all produce
pictures.

Darkness does not explain these rows. `08 01` is an exact constant fill, which
is what the ArduChip FIFO returns when no frame has been written; a dark room
returns noise. The image-control means in the same log are 77–121, so the light
was on.

### `cam_probe-20260820-1930.log` says the same thing again, and it is deterministic

Fifty minutes later, same board, same room, and a scene that had moved:

```
  #  recipe      1840                   1930
  0  as-was      c80a8564 CONSTANT      c80a8564 CONSTANT
  1  as-was      c80a8564 CONSTANT      c80a8564 CONSTANT
  2  no-rewrite  c80a8564 CONSTANT      c80a8564 CONSTANT
  3  flush       c80a8564 CONSTANT      c80a8564 CONSTANT
  4  norw+flush  c80a8564 CONSTANT      c80a8564 CONSTANT
  5  settle300   cfcd557b a picture     b30b32b3 a picture
  6  everything  f3639156 a picture     287a8d52 a picture
```

The two rows that work hash differently because the scene did; the five that do
not are bit-identical. Between the two probes, `m9` was flashed and started
twice — once straight after a VBUS cycle — and failed both times with
`still a constant fill (08 01) after 41 frames`. So this is the state the
camera is in, not a run that went badly, and it is what stopped #9's owed
item 2 from being verified end to end: no frame means no run, no run means
`usb_watch()` never executes, and `usb_watch()` is the only caller of
`lastwords.c`'s write path. Tracked as
[#27](https://github.com/kazunori279/fpga-open-vocab/issues/27).

The threshold between m9's 50 ms inter-frame gap and the probe's 300 ms has not
been measured, and until it is, the size of any fix is a guess.

**It still has not been, and 2026-08-21 says why it is harder than it looks.**
That morning the matrix went back to the 2026-08-03 shape — rows 0/2/4 pictures
again — so the state described above was simply absent and there was nothing to
threshold; a settle sweep returned 3/3 at every value from 0 to 400 ms. The two
faults are separate and only one is present on a given day. See
[`../20260821-lastwords/`](../20260821-lastwords/).
