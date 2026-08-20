# 2026-08-21 — the acquire's doubt, carried past the banner

Two runs verifying [#26](https://github.com/kazunori279/fpga-open-vocab/issues/26).
Not a bench.

## What the failure was

On 2026-08-20 the room went dark mid-soak. `ft_acquire()` printed three separate
warnings about it — a ramp that never left the floor, `mean RGB 7 0 7`, and a
note that the exposure never moved — and then **twelve consecutive runs, roughly
2,400 frames, scored a black picture anyway.** Runs 8–20 of
[`../20260820-usb-p2/`](../20260820-usb-p2/) are the evidence and stay marked
void.

Nothing was broken except the reach of the warning. `usb_soak.sh` redirects each
run to `--out` and prints three grepped lines; the banner it would have had to
re-read was nine lines long and scrolled past on run 8. **A warning that only
exists in a banner is not something a run can be thrown out by afterwards.**

And the line it most needed to read said the opposite of what happened:

```
camera    : live 128x128 RGB565, ... exposure settled after 41 frames
```

That was the loop running out of its 40-frame bound, not settling. It also
counted one frame too many — 41 for a loop that captured 40 — on every stuck run
this firmware has ever produced.

## What changed

- `ft_acquire()` now distinguishes its two exits: `exposure settled after N
  frames` when the convergence test broke the loop, `EXPOSURE NEVER SETTLED
  after N frames` when the bound did. The count is the frames actually captured.
- A third warning branch, for a ramp that was still climbing when the bound
  ended it — neither dark nor stuck, but the background will be measured from a
  mid-ramp frame that the frames after it will not match.
- `ft_acquire_doubt()` returns a short phrase naming the doubt, or NULL. `m9`
  prints it in the `stopped :` summary, where the log is still being read.
- `usb_soak.sh` greps for it, along with #25's `enrolment:` and #9's
  `lastwords:` flags.

**It still does not refuse.** The argument at `FLOOR` in `frame.c` stands: a
genuinely dark room whose correct exposure is the cold reading is a legitimate
scene, and refusing would be firmware deciding it knows the lighting better than
the person standing in it. What changed is that the doubt now survives the
frame.

## `settled.log` — the healthy path

```
camera    : exposure ramp 87 90 92 94 96 98
camera    : live 128x128 RGB565, id 0x82, 16.0 MHz, expose 37 ms, read 16 ms, exposure settled after 6 frames
            mean RGB 94 110 94  (tuned camera on a neutral scene: about 115 107 105)
```

No `scene:` line in the summary. Six numbers in the ramp, six in the count.

## `never-settled-forced.log` — **a deliberately crippled build**

Read the name. The room was lit and the camera was fine; this image had the
convergence test disabled (`if (false && ...)`) so the loop could not exit early.
It is here because it is the only way to exercise the bound-exhausted path
without a dark room, and because the branch it takes is chosen by conditions
that were already in the code and already known to print correctly — the 08-20
log has the `mean RGB < 16` branch firing for real.

```
camera    : exposure ramp 64 62 62 64 68 75 87 97 103 107 110 112 113 115 116 118 119 121 124 125 125 126 126 126 126 126 126 126 126 126 126 126 126 126 126 126 126 126 126 126
camera    : live 128x128 RGB565, ... EXPOSURE NEVER SETTLED after 40 frames
            mean RGB 128 125 127  (tuned camera on a neutral scene: about 115 107 105)
            ^ the exposure was still moving after 40 frames, so this frame was taken mid-ramp
```

Forty numbers in the ramp and forty in the count — the old code printed 41 here.
And at the end of the run, which is the whole point:

```
stopped   : 8 frames, 8 good, capture overlapped with the compute, configuration C
            ...
            usb: 0 outages, 0 ms off the bus, 0 re-attaches (#9)
            scene: THE ACQUIRE WAS NOT CONFIDENT IN THE FRAME THIS RUN STARTED FROM -
            the ramp was still moving when the bound ended it. Every score above is
            against that picture; read the exposure ramp in the banner before
            keeping any of them (#26)
```

The doubt phrase is wrong for *this* scene, of course — the exposure had in fact
settled at 126 and only the disabled test kept the loop going. That is the cost
of forcing the path, and it is why this log is named the way it is.

The soak harness's grep is anchored to the summary's twelve-space indent, so it
catches this line and not `ft_acquire()`'s own "tuned camera on a neutral
scene:" note.
