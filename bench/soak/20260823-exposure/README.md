# Why every frame in this repository is under-exposed

*2026-08-23. One bright room, one board, one binary per block, back-to-back
boots. `firmware/frame.c` `ft_acquire()`.*

The operator's report was "the room is bright and the captured images are too
dark, and this has been happening in past benches as well." It is not the room,
it is not the lens, and it is not a missing register. **`ft_acquire()`'s settle
loop declared the exposure converged while the sensor's AEC was still climbing**,
and whether it did so was a coin flip on each boot.

m9's own banner names the target: `tuned camera on a neutral scene: about
115 107 105`. The 08-23 lit-room check read **75 83 72**.

## The measurement

Six consecutive boots, same binary, same bright room, seconds apart. The numbers
are the per-frame mean luma of the ramp, and `settled after N` is the loop's own
verdict.

| ramp | settled | verdict |
| --- | --- | --- |
| 66 77 86 95 103 111 116 117 119 120 | 10 | arrived |
| 70 81 90 99 107 115 117 119 121 | 9 | arrived |
| **74 77 78 80 82 84** | **6** | **still climbing** |
| 77 88 97 106 112 113 115 116 | 8 | arrived |
| **78 80 83 85 86 88** | **6** | **still climbing** |
| **81 83 85 87 89 91** | **6** | **still climbing** |

Three of six stopped at the loop's six-frame floor thirty counts short, and said
`settled` while doing it.

## Why

The test was `|luma - was| <= 2`, three times in a row, floor of six frames. The
three short ramps climb at **exactly two counts a frame**, so every single step
satisfies the window: `stable` reaches three on frame three and the loop leaves
the moment the floor lets it. The other three happened to open with steps of nine
or eleven, which reset the counter and forced the ramp to finish.

Same code, same lighting, opposite outcomes, decided by which side of the
tolerance the AEC's step size fell on that boot. This is the M8b/M8c race in
`frame.c`'s own comments, one layer down and with a worse symptom: it does not
fall back to the flash test vector, so there is no warning. It hands back a real
photograph of the right scene, thirty counts under, and scores it.

**A wider or narrower window cannot fix it.** The plateau is not quiet either —
the long ramps hunt over 147..154 once they arrive. Two counts per step is
simultaneously too loose for the climb and too tight for the top; the two
populations overlap on step size.

They separate on something else: **a climb is monotone and a plateau oscillates.**
The test is now a trend across a six-frame window — the sum of the last three
against the sum of the first three — which is the same two counts a frame of
drift, measured where drift accumulates instead of where it hides. The three
short ramps move that statistic by 6. A hunting plateau moves it by 0 to 2.

## After

Six more boots, same room, `frame.c` with the windowed test:

| ramp | settled | mean RGB |
| --- | --- | --- |
| 79 90 100 108 116 125 127 129 131 132 132 132 132 | 13 | 135 132 131 |
| 78 81 82 85 87 89 90 92 94 94 95 95 95 | 13 | 95 95 94 |
| 82 84 86 88 90 92 93 95 96 96 96 96 | 12 | 96 97 96 |
| 76 78 81 83 84 86 88 90 91 91 91 91 | 12 | 91 94 88 |
| 75 85 96 105 112 120 128 130 132 133 131 125 | 12 | 128 123 125 |
| 81 83 85 87 89 91 92 94 95 96 96 96 96 | 13 | 96 97 96 |

**Zero of six exit mid-climb.** Every run ends on a tail that is flat to within
one count, and none of them trips `EXPOSURE NEVER SETTLED`. The cost is three to
five extra frames of boot.

What is left is not this bug. The AEC still arrives at two different operating
points — four runs plateau near 95 and two near 132 — and both are genuine
plateaus reached by a ramp that finished. That is the sensor choosing, and it is
the next question, not this one.

## What this was not, which took three flashes to rule out

`cam_image_defaults()` writes the three auto-enables and the white balance mode
and nothing else. It has never written `CAM_REG_BRIGHTNESS_CONTROL` (0x22) or
`CAM_REG_EV_CONTROL` (0x25), and no host flag reaches them, so the obvious
reading is that every bench ran with exposure compensation unset and the fix is
to set it.

Three m9 builds at EV 1, 2 and 3 read 142 138 137, 112 111 109 and 155 149 152 —
which looks like a working knob with a best value at 2, and is not. Re-running
the EV 2 binary four more times without touching anything gave 78 81 77, 94 96 89,
77 81 76 and 120 120 118. **The spread within one build covers the whole spread
between builds**, so those three numbers measured the coin flip above and not the
register. The EV writes were reverted; `cam.c` is unchanged.

`cam_probe.c`'s image-control sweep cannot settle this either, and
`cam_probe-20260823-bright.log` in this directory is why. It walks its controls
cumulatively with no reset between rows, and on that log `EV back to 0` reads
*brighter* than `EV +2` — the AEC was still converging underneath the sweep, so
every row carries the drift of the rows before it. It is the right instrument for
"does this register do anything" and the wrong one for "what should the number
be".

## Does this invalidate the archive? No, and here is the number

The first question after a fix like this is whether every recognition figure in
`bench/cue/` has to be thrown away. Replaying the `exposure ramp` line out of all
39 archived logs and applying the new predicate to the ramp each one recorded:

**33 of 39 reported `settled` while still climbing.** The four that did not are
the ones that spent the whole 40-frame budget, and two of those are the same run
(`20260816-172256` and its `fake_d` copy).

That is the point. The fault is **near-universal, so it is a constant and not a
variable**, and a constant cannot explain a spread. Joining the terminal ramp
luma against `probe_ceiling.py`'s columns over the 27 scored benches:

| | ceiling `best` | `lost` |
| --- | --- | --- |
| all 27 benches | r = **−0.099** | r = **+0.027** |
| the book pair, 20 runs | r = **−0.324** | r = **+0.078** |

Nothing, and what little there is points the wrong way. The brightest book bench
on record (`133552`, luma 138) lost 37 points to #19; the darkest usable one
(`073335`, luma 41) reached a 99.4% ceiling and lost 3.3. The book pair's 1.000
run sits at luma 132 and its 0.579 run at luma 123.

So: the archive is under-exposed, the numbers in it were measured on
under-exposed frames, and **exposure is not the hidden variable behind #19, #18
or the 42-point ceiling spread**. It was worth ruling out and it is ruled out.
What the fix buys is future benches that are not doing this, and one fewer
per-boot coin flip in anything measured from here on.

## Files

| file | what it is |
| --- | --- |
| `cam_probe-20260823-bright.log` | the full `forgix_cam_probe` report in this room, including the cumulative image-control sweep |

The ramps above are `host/demo.py "a book" --frames 6 --leave-running`, read off
the `camera :` lines. Nothing here is a recognition number and none of it belongs
in `bench/cue/`.
