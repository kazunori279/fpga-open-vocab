# The white-balance bit reaches the die. The flat boots were never a cache

*2026-09-07, eleven boots of `forgix_cam_awb`. Not a bench: no cue schedule, no
enrolment, no held-out set, no accuracy. This directory was opened to ask
whether `'L'`'s AWB arm actually reaches the sensor, spent seven boots finding
out that the instrument it inherited could not tell a deaf board from a badly
aimed ladder, and answered the question on the eighth.*

> **Read [`../20260907-lockrate/`](../20260907-lockrate/) after this one.** The
> lock check built here was then run over 33 boots, which turned point 3's
> "flat boot" from an occasional fault into the normal case: 32 of 33 dragged,
> and 9 of them changed their answer inside ten seconds.

Three things came out of it:

1. **`0x332b` and `0x33ca` separate on the white-balance bit** and hold across
   three visits an arm. `0x332b` reproduced on four boots. The bit reaches the
   die.
2. **The flat-exposure boot is not a cache and `0x07` bit 7 does not clear it.**
   [`../20260907-i2crec/`](../20260907-i2crec/) claimed it did on one
   observation. Seven boots here walked that same recovery ladder and bit 7
   recovered none of them.
3. **The fault is visible at `0x3002`/`0x3003`**, and it has two shapes that
   look identical from the host.

| log | what it was for |
|---|---|
| [`run1.log`](run1.log) | cache reset applied before the mask. No handle: `134 134 134 135 134 134` |
| [`run2.log`](run2.log) | cache reset after every mask, twice. No handle either: `137 140 141 140 140 140` |
| [`run3.log`](run3.log) | the ladder rejecting a surface that was plainly answering. Two procedural faults found, both in the instrument |
| [`run4.log`](run4.log) | the one early boot with a live handle. Recovered on **bit 1**, not bit 7. Stage A found `0x332b`; stage D inconclusive |
| [`run5.log`](run5.log) | the recovery ladder made it *worse*. Rung 2 revived the surface, rung 3 broke capture, rung 4 left it flat |
| [`run6.log`](run6.log) | the write→readback table, and the first sight of the die putting its own value back |
| [`run7.log`](run7.log) | echo validation added. 4 of 14 echoed; the other ten were the AE loop, not stale reads |
| [`run8.log`](run8.log) | **the lock check.** Thirty polls, `024c` throughout, locked and free alike |
| [`run9.log`](run9.log) | **stage M.** `0x332b` and `0x33ca` separate on the mask. Handle clipped at the ceiling |
| [`run10.log`](run10.log) | **the complete stage A.** Handle `38 58 84 113 143 174`. Both registers again, and the channel means |
| [`run11.log`](run11.log) | the second flat-boot shape: the die holds the write and the picture still does not move |

## 1. What the WB bit does at the die

Run 10, with a live handle either side of the sweep. 1024 addresses from
`0x3000`, three visits an arm, arms interleaved FREE LOCK LOCK FREE FREE LOCK so
that a monotone drift through the stage cannot separate them:

```
  sensor   WB free        WB locked      verdict
  0x301b   33 32 34      2f 2f 2d      separates but is not still - not claimed
  0x30de   68 6a 68      79 7a 84      separates but is not still - not claimed
  0x332b   10 10 10      18 18 18      W1 and W2 - RESPONDS
  0x33ca   4a 4a 4a      41 41 41      W1 and W2 - RESPONDS
```

Two responded, two separated without being still, one wobbled without
separating, 1019 never moved. The two that are claimed are the two that both
separated *and* held the same value on all three visits; `0x301b` and `0x30de`
moved between visits within an arm, so a difference between arms is not
attributable to the arm.

`0x332b` `10` → `18` also came back in run 4's stage A and in stage M on runs 9,
10 and 11 — four boots, and stage M is a different mask (all loops, not just
WB), so it is not the same measurement twice. `0x33ca` came back on runs 9 and
10.

**What is not claimed.** Neither address is named. `0x301b` tracked *exposure*
in `20260907-i2crec`'s sweep, which is enough to show that a register moving
under a mask bit is not thereby a white-balance register. Nothing here is read
against a datasheet.

The frame either side, three channels:

```
  WB free     R 127  G 127  B 119   spread 8
  WB locked   R 114  G 133  B  89   spread 44
```

Locking white balance *widens* the spread and collapses blue. That is the same
direction [`../../soak/20260825-camlock/`](../../soak/20260825-camlock/) saw
when `130 127 129` went to `100 158 122`.

## 2. The flat boots, and why the old explanation was wrong

`20260907-i2crec` wrote that a boot with a deaf manual-exposure surface is a
stale cache and that `0x07` bit 7 clears it. This directory ran that ladder —
rung 0 the mask again, rung 1 `0x07` bit 1, rung 2 `0x07` bit 7, rung 3 `0x07`
bit 6, rung 4 the whole of `cam_begin()` — on seven flat boots.

| rung | what it is | recovered |
|---|---|---|
| 0 | apply the auto mask a second time | 0 of 7 |
| 1 | `0x07` bit 1, the documented I²C reset | **1** of 7 (run 4) |
| 2 | `0x07` bit 7, reset cache | **0** of 7 |
| 3 | `0x07` bit 6, reset FPGA | 0 of 7, and it **broke capture every time** |
| 4 | the whole of `cam_begin()` again | 0 of 7 |

Look at where i2crec's rung 2 actually sat: it fired after rung 0 and rung 1 had
already run, and the handle that came back alive was measured after a *third*
application of the mask. One observation of a five-step sequence, written up as
one register write.

Rung 3 is worse than useless. It broke capture on every boot it was reached on —
72 consecutive `!! FIFO length 614400, buffer is 32768` in run 3 — and rung 4
then left the board flatter than it found it. Run 5 is the clean example: rung 2
produced `5 4 7 19 43 84`, a surface unmistakably answering, and rungs 3 and 4
demolished it before anything could be read.

## 3. What the fault actually is

The picture was the wrong instrument. Write an exposure with every loop locked
and then read `0x3002`/`0x3003` back over a few seconds, and the flat boots
split in two.

**Shape one — the loop never stopped.** Run 8:

```
  locked         wrote 0080, die: 024c 024c 024c 024c 024c 024c 024c 024c 024c 024c   dragged
  locked         wrote 0400, die: 024c 024c 024c 024c 024c 024c 024c 024c 024c 024c   dragged
  AE free        wrote 0080, die: 024c 024c 024c 024c 024c 024c 024c 024c 024c 024c   dragged
```

`0x024c` is what the AE loop settled on with the mask free, thirty seconds
earlier. The die takes the write and puts its own value back, and it behaves
identically locked and free — so the lock did nothing. A frame pinned near
mid-scale whatever you write to it is not a broken exposure path; it is a
working AE loop that was told to stop and did not, and mid-scale is exactly
where a working AE loop parks a frame.

**Shape two — the loop stopped and the picture did not care.** Run 11 held
`0080` and `0400` through all thirty polls and the ladder still read `78 78 78
78 78 78`. The registers take the value, the exposure is not applied.

Both look the same from the host: a flat ladder around 110–120. Neither is
fixable from here — the passthrough is a read path and there is no measured way
to write the die's own AE enable.

## 4. Three faults in the instrument, which cost seven boots

Worth writing down, because each one produced a confident wrong verdict first.

**The ladder was aimed at a scene that had gone.** Six exposures doubling from
`0x00010` to `0x00200` span a factor of 32 and the sensor's floor-to-ceiling
range at one gain is about 16, so no placement of those six fits inside it. Run
4's clean `21 41 75 122 176 222` was that fixed ladder happening to straddle the
range in September daylight. Runs 5 and 6 sat too low — `5 4 7 19 43 84`, and
`5 → 4` fails H1. Run 9 sat too high — `51 95 146 180 180 180`, and `180 → 180`
fails H1 just as hard.

The rungs are now placed against the die's own AE choice, read at
`0x3002`/`0x3003` with the loop free, and stepped by three halves rather than
two so the span is under 8. The top rung *is* the AE choice, so the ceiling is
out of reach by construction. H1 and H2 are untouched and read exactly the
numbers they always did. Run 10's handle: `38 58 84 113 143 174`, wobble 1,
retrace 0.

**A 16-bit read off this passthrough is two transactions and they tear.** Run 6
wrote `0x0400` and read `040b`; it wrote `0x2000` and read `200b`. High byte
fresh, low byte still holding `0b` from `0x030b`. Reads are now repeated until
two agree, and a readback that does not echo the low sixteen bits of what was
written is discarded rather than used. Run 6's placement had picked `0x00008`
precisely *because* its readback was stale and therefore matched the target
exactly — the worst reading in the table won the comparison.

**A wobble of 0 makes H2 unsatisfiable.** Run 10 held its handle through stage A
and then failed H2 on a retrace of 1 against a wobble of 0. Both numbers were
honestly measured and the verdict is still an artefact: the wobble came from
three captures on the way up and the retrace compared that against a *single*
capture on the way down, so H2 was asking the descent to match to a precision
the ascent had never been asked to demonstrate. A wobble of 0 asserts noise
below one count of an 8-bit mean. The descent is now repeated the same way the
ascent is and both passes feed the wobble. H2's text is unchanged.

## What this licenses

- `'L'`'s AWB arm can be written as a lock that is **checked at `0x332b`**
  rather than hoped at.
- Any future claim that a boot is "bad" can be tested at `0x3002`/`0x3003`
  before the run rather than argued about afterwards.
- The `0x07` bit 7 cache reset must **not** go in the shipping path. It was
  going to, on the strength of one run.

## What this does not license

- Naming `0x332b` or `0x33ca`. They respond; that is all.
- Any statement about whether the WB lock **holds** over a session. Stage D has
  never reached a board with a live handle, and its positive control — the free
  arm having somewhere to go — has failed every time it has been read. It
  probably needs an operator changing the scene colour, which is #31's problem.
- Any claim about *why* a given boot comes up in shape one rather than shape
  two. Eleven boots is not a rate.

## What to do next, in order

1. **Get stage D to a verdict.** It needs a handle that survives stage A and a
   scene the WB loop can be seen working on. Run 10 got the first; nobody has
   arranged the second.
2. **Count the two shapes across boots** with the lock check alone — it runs in
   about fifteen seconds and needs no handle, so it can be run twenty times
   where the full probe cannot.
3. **Put `cam_sensor_read()` in `cam.c`** and have `cam_begin()` verify the
   exposure lock took, which is now a thing that can be verified.

## Reproduce

```sh
cmake --build firmware/build --target forgix_cam_awb
uv run --script host/bootsel.py --flash firmware/build/forgix_cam_awb.uf2
uv run --script host/mon.py --out /tmp/awb.log --idle 300 --wait 60
```

Power-cycle the hub between boots — discover the port, it moves:

```sh
uhubctl                       # find the port the 2e8a device is on
uhubctl -l <hub> -p <port> -a cycle -d 3
```

Restore the board afterwards with
`uv run --script host/bootsel.py --flash firmware/build/forgix_m9.uf2`.
