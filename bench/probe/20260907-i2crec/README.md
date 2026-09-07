# The passthrough does not cost the control surface. Something else does, and it does it from cold

*2026-09-07, four boots of `forgix_cam_i2crec`. Not a bench: no cue schedule,
no enrolment, no held-out set, no accuracy. This directory exists to answer one
question that [`../20260907-i2cpass/`](../20260907-i2cpass/) left, and the
answer is that the question was built on something that is not true.*

| log | what it was for |
|---|---|
| [`nothing-broke.log`](nothing-broke.log) | one fire, 36 fires, 36 null writes. **Nothing broke.** The recovery ladder never ran |
| [`bisect.log`](bisect.log) | stage A taken apart: the register dump, writing `0x07`, the high addresses, the low ones. All four harmless |
| [`dead-from-cold.log`](dead-from-cold.log) | the handle **dead before a single fire**, and the run halting because of it. The short log is the finding |
| [`stagea-whole.log`](stagea-whole.log) | 20260907-i2cpass's stage A reproduced entire — 36 fires, 256 register reads after each. Handle alive throughout |

## What was being asked

`20260907-i2cpass/` fired `0x07` bit 0 for the first time, found `0x48` holding
what comes back, read `36 4c` at the OV3640's product-ID addresses — and then
its stage B could not run, because the manual exposure writes had stopped
moving the picture: `luma 133 133 133 at 0x00010, 133 133 133 at 0x00200`. It
concluded that firing the bit costs the control surface, did not adopt `0x48`,
and listed recovery as the next thing to find.

This binary was built to find that recovery. The instrument is the same handle
check that directory ended on, and it is the same shape as every other rule in
this series: **two exposures four decades apart must part the picture by more
than repeated captures at one exposure wobble.** Parting against wobble, no
constant. It runs before each phase's cost is incurred as well as after, which
is the one thing `20260907-i2cpass` did not do.

## The answer: there was nothing to recover

```
                                       lo luma       hi luma      part  wobble
  baseline, nothing fired               6   6   6 | 182 182 182    176      0
  after ONE fire at 0x300A              6   6   6 | 183 183 183    103      0
  36 fires of 0x07 = 0x01               6   6   6 | 182 182 181    176      1
  36 writes of 0x07 = 0x00              5   5   5 | 179 179 180    174      1
  read all 128 registers, 3 times       6   6   6 | 181 181 180    175      1
  fire at the 5 HIGH addresses          6   6   6 | 184 184 184    178      0
  fire at the 7 LOW addresses           6   6   6 | 184 184 184    178      0
  STAGE A, WHOLE AND UNSHORTENED        4   4   4 | 144 143 143    140      1
```

The last row is the one that settles it. It is not a summary of stage A or a
sampled version of it: twelve addresses in the order that file lists them,
three visits ascending-descending-ascending, a full 256-register read after
every single fire. The handle is alive on the other side of it.

**So `20260907-i2cpass`'s reading is wrong.** Firing `0x07` bit 0 does not cost
the manual exposure surface. It does not cost it once, thirty-six times, at low
sensor addresses, at high ones, or stacked with 3456 register reads the way the
original ran it.

## What actually happens, and it is worse than a bad bit

The third boot found the handle **dead before anything was fired at all**:

```
handle at rest    133 133 133 | 133 133 133   part 0  wobble 0  DEAD
```

Zero fires. `133` is the same byte `20260907-i2cpass`'s stage B reported, and
the picture sits exactly where the auto loop settled it — `cam_image_auto_mask`
is called, and the exposure writes that follow do nothing. Three boots of this
binary had a handle and the fourth did not, off an identical recipe. The fourth
boot's board came back on a reflash alone, with no power cycle and no ladder
rung.

This is not new. `20260907-manexp/` recorded one flat exposure run it could not
explain and left it open. That is the same thing, and this is its second and
third sighting. **The blocker on the manual exposure surface is a
boot-to-boot condition in `cam_image_auto_mask()`, not the passthrough** —
`0x30` is write-only, so there is nothing to read back, and the handle is the
only check available.

The ladder is left in the binary and now starts one rung lower than it did,
with "apply the mask a second time, no reset at all" below the three resets and
`cam_begin()`. It has never run: every boot since it was written has either had
a handle or been the boot that halted before the rung existed.

## What this does and does not license

**It unblocks stage B.** The precondition stage B needed — a live handle on
both sides of a fire — is what these logs show, four boots running, and
`20260907-i2cpass` never had it. Stage B can now be run as written.

**It does not adopt `0x48`.** Nothing here is a causal test. `0x48` still rests
on stage A alone: A1, A2, three runs, and a product ID that matches a real part
number. That is one stage short of the standard, exactly as it was yesterday,
and `firmware/cam.h` still does not mention it.

**It does not explain the flat boots.** Knowing what does not cause them is
worth having and is not the same as knowing what does.

## What to do next, in order

1. **Run stage B.** `forgix_cam_i2c` already contains it, gated behind the
   handle check that kept failing. No code change is needed — flash it and see
   whether anything in `0x3400`–`0x35FF` tracks an exposure this board sets. If
   its handle check fails again, that is now a known and separate fault, and
   the run should be repeated rather than read.
2. **Then decide whether the flat boot is worth chasing.** It costs the manual
   exposure arm about one boot in four, and every probe in this series depends
   on that arm. A second application of the mask is the cheapest candidate and
   the ladder is already pointed at it.
3. **Then the AWB gains**, which is what all of this is for.

## Reproduce

```sh
# power-cycle first: 0x06 and the rest of the ArduChip survive a reflash
uhubctl -l <the hub with the 2e8a device> -p <its port> -a cycle --delay 3
cmake --build firmware/build --target forgix_cam_i2crec
uv run --script host/bootsel.py --flash firmware/build/forgix_cam_i2crec.uf2
uv run --script host/mon.py --out /tmp/i2crec.log
# power-cycle again before flashing m9 back
```

A boot that reports the handle DEAD at rest is the interesting one and is not a
failed run. Keep the log.
