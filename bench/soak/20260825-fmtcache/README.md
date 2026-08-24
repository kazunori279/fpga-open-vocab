# The 2026-08-25 format-cache runs

Three `cam_probe` runs and one `m9`, all in the same lit room inside fifteen
minutes, taken to answer the one question
[#29](https://github.com/kazunori279/fpga-open-vocab/issues/29) said only the
board could answer: does clearing `cam.c`'s format cache after a sensor reset
bring back the repeat-write blanking that the cache exists to avoid?

It does not. The matrix is unchanged, the stale-cache fault is gone, and the
same session carried the lit-room run that
[#27](https://github.com/kazunori279/fpga-open-vocab/issues/27) had left
outstanding.

## Manifest

| file | build | what it is for |
|---|---|---|
| `cam_probe-20260825-fmtcache.log` | fixed | the first run, and **it does not exercise the fix** — see below |
| `cam_probe-20260825-control-nofix.log` | fix commented out | the control: the fault, live, in this room |
| `cam_probe-20260825-fixed.log` | fixed | the same firmware with the two words back |
| `m9-20260825-fixed.log` | fixed | the whole path, 42 frames, lit room |

## The fix is one line and a comment

`cam_begin()` writes `CAM_REG_SENSOR_RESET` (`firmware/cam.c:269`), which puts
the sensor back to its default VGA. It now also says so:

```c
last_fmt = last_mode = -1;
```

at `firmware/cam.c:295`, with the declaration moved above `cam_begin()` so it
can. `cam_trigger()` reads that pair at `cam.c:334` and `cam.c:339` to decide
whether a `rewrite = false` recipe may skip the FORMAT and CAPTURE_RESOLUTION
writes; before this, it skipped them after a reset on the grounds that the mode
had not changed, when only the *record* of it had survived.

## The first run did not test anything

`cam_probe-20260825-fmtcache.log` was taken before the regression row existed,
and it is kept because deleting it would hide the mistake. The settle sweep's
premise block captures `warm` with `rewrite = true` before it captures `bare`
with `rewrite = false`, so by the time a no-rewrite capture happens the cache
has already been rewritten by hand and describes a real machine again. A fixed
build and a broken one produce identical output there. The row added at
`firmware/cam_probe.c:366` therefore runs **first**, before anything else in the
sweep touches the cache.

## What answers is the FIFO length, not the picture

The row does not look at the image:

```
#29 cache : cam_begin() then a no-rewrite capture -> FIFO 614400 B, want 32768  *** the format cache outlived the reset
```

CAPTURE_RESOLUTION sets the length the FIFO will return whether or not the
sensor has written a usable frame yet, so this row reads the same in a dark room
as in a lit one. That matters because everything else in this directory's
history is exposure-bound: #27's settle threshold is a function of scene
brightness, and a check that inherited that dependence would only be a check on
the day someone happened to turn the light on. 614400 is 640 × 480 × 2 exactly.
32768 is 128 × 128 × 2, and is what the fixed build prints:

```
#29 cache : cam_begin() then a no-rewrite capture -> FIFO 32768 B, want 32768  ok
```

The control run also carries the older symptom on the line above it, from
`cam_capture()` itself: `!! FIFO length 614400, buffer is 153600`.

## The control, and the thing that had to not change

Both runs are minutes apart in the same room — `scene : mean RGB 101 133 112`
without the fix, `100 135 113` with it — and **both match the 2026-08-03
recorded matrix exactly**:

```
  #  recipe      crc32    first px  verdict
  0  as-was      ...      a picture
  1  as-was      c80a8564 08 01     CONSTANT
  2  no-rewrite  ...      a picture
  3  flush       c80a8564 08 01     CONSTANT
  4  norw+flush  ...      a picture
  5  settle300   ...      a picture
  6  everything  ...      a picture
```

Rows 1 and 3 are the repeat-write blanking, still exactly where it was, still
only there. That is the whole point of running the control: #29 argued the fix
might turn every post-reset capture into a resolution rewrite and so spread rows
1 and 3 across the table. It did not, and rows 0, 2, 4, 5 and 6 remain pictures.
The settle sweep reads 3/3 at every value in both directions in both runs, and
3/3 at every EV, so nothing in the timing moved either.

## The lit room, which is #27's last item

`m9-20260825-fixed.log` — 42 frames, 42 good, 283 ms/frame, `mean RGB 101 102
99`:

```
camera    : exposure ramp 86 88 90 92 95 96 98 98 99 100 100 100
camera    : live 128x128 RGB565, id 0x82, 16.0 MHz, expose 37 ms, read 16 ms, exposure settled after 12 frames
```

No `+N` entries in that ramp. The recovery path added in `9e64714` is gated on a
constant frame, so a working camera in a lit room should never enter it; this is
the run that says it does not, which
[`../20260822-settle/`](../20260822-settle/) listed under *Not measured*.

It is the second such run, not the first. `m9-20260823-litcheck.log`, in that
same directory, is a post-fix lit-room run from 2026-08-23 — ramp `66 68 69 71
73 74 76`, `mean RGB 75 83 72`, no recovery entries — that was taken and never
written up. Two rooms two days apart, both clean.

The scores in the `m9` log mean nothing: nobody staged a book, so both queries
are reading an ordinary desk. What the run is here for is the ramp, the frame
count, `camera bus: worst gap 17 us against the 2000 us deadline`, and `usb: 0
outages`.

## Not measured

**A post-reset `rewrite = false` capture in the shipping path.** Every caller in
the tree still passes `rewrite = true` after a `cam_begin()`, which is why #29
was a trap for the next caller rather than a live failure. The row at
`cam_probe.c:366` is now that next caller, and it is a probe, not the appliance.

**Whether the extra CAPTURE_RESOLUTION write costs time.** It happens once per
`cam_begin()`, which `m9` does at start-up and inside `ft_acquire()`'s ramp,
and 283 ms/frame is unchanged against the 08-22 runs — but that is a
whole-frame figure, not a measurement of the write.
