# The exposure lock is not rare to lose. It fails on 32 boots out of 33

*2026-09-07, thirty-six boots of `forgix_cam_lockrate`. Not a bench: no cue
schedule, no enrolment, no held-out set, no accuracy. One question, asked the
same way every boot — with every auto loop masked off and an exposure written,
does the die keep it?*

> **Three directories follow this one and change what it means.**
> [`../20260907-cure/`](../20260907-cure/) spends the last untried cure and
> loses to a null arm. [`../20260907-hold/`](../20260907-hold/) asks the same
> question of gain and white balance, finds both hold, and finds the *unlock*
> failing instead. [`../20260907-witness/`](../20260907-witness/) carries all of
> it into `m9`, where the fault stops being a probe result.

[`../20260907-awb/`](../20260907-awb/) established that the question is
answerable at all: `CAM_REG_AUTO_CONTROL` is write-only, but the die's own
exposure registers `0x3002`/`0x3003` are readable through the I²C passthrough,
so a write that comes back changed is an AE loop that never stopped. What eleven
boots there could not give was a rate. This directory is that probe with the
sweep, the ladder and the recovery taken out — fifteen seconds a boot instead of
four minutes — run over a hub power cycle each time.

Three things came out of it, and the second one is a retraction of code written
the same afternoon:

1. **`dragged` is the normal case, not the exception.** 32 boots of 33 dragged at
   least once after warm-up; 23 dragged on all three checks; exactly one boot
   (`lr00`) never dragged at all. Issue #33's "on some acquires" is too kind.
2. **`cam_image_defaults()` cannot answer this question and no longer tries.**
   The check was wired in as that function's first statement. It answered `held`
   on 33 boots out of 33 — because nothing has clocked a frame by then.
3. **The lock is not a property of the boot.** 9 of 33 boots gave two different
   answers to the same question inside about ten seconds.

The passthrough itself never failed: `unreadable` came back 0 times, on every
check of all 36 boots.

## 1. The rate

The counts in this section and in §3 are over the 33 boots `lr00`–`lr41`.
`lr50`–`lr52` are a control on the fix and are kept out of them.

Each boot brings the camera up the shipping way, warms up 20 captures, then runs
`cam_exposure_lock_check()` three times with the loops re-enabled and 8 captures
between. `held` means the die kept both `0x0080` and `0x0400`; `dragged` means it
put its own value back.

| | held | dragged |
|---|---|---|
| check 0 | 5 | 28 |
| check 1 | 6 | 27 |
| check 2 | 4 | 29 |
| **all 99 warm checks** | **15** | **84** |

Per boot, by the pattern of the three:

| pattern | boots |
|---|---|
| dragged, dragged, dragged | 23 |
| held, held, dragged | 3 |
| dragged, dragged, held | 3 |
| dragged, held, dragged | 2 |
| held, dragged, dragged | 1 |
| held, held, held | 1 |

**One boot in 33 locked and stayed locked.** Nine gave a mixed answer, which is
the part that rules out the shape everyone assumed: this is not "some boots come
up wrong". The same board, in the same scene, ten seconds apart, answers
differently. Whatever `cam_image_auto_mask(0)` does to the ArduChip, it does not
durably stop the loop on the die.

## 2. Why the boot-path check said `held` 33 times out of 33

`cam_exposure_lock_check()` was added to `cam.c` as the first statement of
`cam_image_defaults()`, on the argument that #33's title is that this function
does not notice. Every one of the first 33 boots printed:

```
  cam_image_defaults() noticed: held        (wrote 0400, die said 0400)
```

while 32 of those same boots dragged a few frames later. A check that cannot
fail is not a check.

The reason is that the AE loop revises the exposure per frame, and at the top of
`cam_image_defaults()` this boot has not captured one. There is nothing running
to drag the write back, so it stays, and the `held` reports the absence of a
frame rather than the state of the sensor.

**The one-frame control.** Boots `lr30`–`lr41` capture exactly one frame after
`cam_image_defaults()` and ask again, changing nothing else:

| point | held | dragged |
|---|---|---|
| before the first frame | 12 | 0 |
| after one frame | 5 | 7 |
| after 20 frames (check 0) | 1 | 11 |

One capture is enough to start the loop dragging more often than not. Zero is
never enough. That is the mechanism, and it decides where the check belongs.

So `cam.c` now does two things instead of one:

- `cam_exposure_lock_check()` returns `CAM_LOCK_UNTESTED` and writes no
  registers when `cam_frames_triggered` is zero. The one case that is knowably
  meaningless is the one it refuses.
- The call is gone from `cam_image_defaults()`, which puts the boot path back
  bit for bit where it was. Noticing belongs to the caller, after its warm-up.

Boots `lr50`–`lr52` are the control on the refusal: `before the first frame:
untested (0 frames triggered)`, and `dragged` on the very next check, all three
boots.

**This also means `held` and `dragged` are not equal in weight.** `dragged` is
proof — the die overwrote a value it was handed. `held` is only the absence of
that proof inside two writes and 60 ms, and the nine mixed boots show how little
that absence is worth on its own.

## 3. What the picture says, and why it is not the verdict

After the three checks, the probe locks the loops, writes `0x0080`, settles,
writes `0x0400`, settles, and reports both lumas. Two exposures a factor of
eight apart should give two different frames if the lock took.

| | picture moved | picture flat |
|---|---|---|
| final check `held` | 4 | 0 |
| final check `dragged` | 6 | 23 |

The die and the picture agree in direction: every boot the die called `held`
moved, and 23 of 29 it called `dragged` were flat, at the mid-scale luma
`../20260907-awb/` named as the AE loop's own parking spot. The six that dragged
and still moved are the manual write taking effect for long enough to be seen
before the loop pulls it back.

**But the picture column has a defect and it is worth stating.** `settled()`
gives up after 30 captures without three in a row agreeing, and that bound was
hit on 7 boots — all 7 of them in the "moved" column, none in the "flat" column.
A picture that is still hunting is a picture that has not finished moving, so
"moved" is partly a report that the frame would not sit still. The die's answer
does not have this problem, which is why the die's answer is the verdict and
this table is a cross-check.

## 4. The runs

`lr00`–`lr20` are the rate. `lr30`–`lr41` add the one-frame control. `lr50`–`lr52`
are the control on the `untested` refusal, and are the only three taken against
a `cam_image_defaults()` that no longer runs the check.

| boot | before | 1 frame | warm 0 | warm 1 | warm 2 | picture |
|---|---|---|---|---|---|---|
| [`lr00`](lr00.log) | held | — | held | held | held | moved 105→119 |
| [`lr01`](lr01.log) | held | — | dragged | dragged | dragged | flat 120 |
| [`lr02`](lr02.log) | held | — | dragged | dragged | dragged | moved 106→136 † |
| [`lr03`](lr03.log) | held | — | dragged | dragged | dragged | moved 93→128 † |
| [`lr04`](lr04.log) | held | — | dragged | held | dragged | flat 119 |
| [`lr05`](lr05.log) | held | — | dragged | dragged | dragged | flat 32 |
| [`lr06`](lr06.log) | held | — | held | held | dragged | flat 120 |
| [`lr07`](lr07.log) | held | — | dragged | dragged | dragged | flat 114 |
| [`lr08`](lr08.log) | held | — | dragged | dragged | held | moved 92→127 † |
| [`lr09`](lr09.log) | held | — | dragged | dragged | held | moved 88→125 † |
| [`lr10`](lr10.log) | held | — | dragged | dragged | dragged | flat 119 |
| [`lr11`](lr11.log) | held | — | dragged | dragged | dragged | moved 16→108 |
| [`lr12`](lr12.log) | held | — | dragged | dragged | dragged | flat 52 |
| [`lr13`](lr13.log) | held | — | dragged | dragged | dragged | moved 91→127 † |
| [`lr14`](lr14.log) | held | — | dragged | held | dragged | flat 120 |
| [`lr15`](lr15.log) | held | — | dragged | dragged | dragged | flat 37 |
| [`lr16`](lr16.log) | held | — | dragged | dragged | dragged | flat 36 |
| [`lr17`](lr17.log) | held | — | held | held | dragged | flat 34 |
| [`lr18`](lr18.log) | held | — | dragged | dragged | dragged | flat 51 |
| [`lr19`](lr19.log) | held | — | held | dragged | dragged | flat 115 |
| [`lr20`](lr20.log) | held | — | dragged | dragged | dragged | moved 99→135 † |
| [`lr30`](lr30.log) | held | dragged | dragged | dragged | dragged | flat 77 |
| [`lr31`](lr31.log) | held | held | held | held | dragged | flat 135 |
| [`lr32`](lr32.log) | held | dragged | dragged | dragged | dragged | flat 119 |
| [`lr33`](lr33.log) | held | held | dragged | dragged | dragged | flat 115 |
| [`lr34`](lr34.log) | held | held | dragged | dragged | dragged | flat 38 |
| [`lr35`](lr35.log) | held | held | dragged | dragged | dragged | moved 49→175 |
| [`lr36`](lr36.log) | held | dragged | dragged | dragged | dragged | flat 115 |
| [`lr37`](lr37.log) | held | held | dragged | dragged | dragged | flat 81 |
| [`lr38`](lr38.log) | held | dragged | dragged | dragged | held | moved 85→123 † |
| [`lr39`](lr39.log) | held | dragged | dragged | dragged | dragged | flat 80 |
| [`lr40`](lr40.log) | held | dragged | dragged | dragged | dragged | flat 114 |
| [`lr41`](lr41.log) | held | dragged | dragged | dragged | dragged | flat 116 |
| [`lr50`](lr50.log) | untested | dragged | dragged | dragged | dragged | flat 64 |
| [`lr51`](lr51.log) | untested | dragged | dragged | dragged | dragged | flat 129→130 |
| [`lr52`](lr52.log) | untested | dragged | dragged | dragged | held | moved 89→125 † |

† `settled()` hit its 30-capture bound on at least one of the two lumas. See §3.

All 36 boots read sensor id `0x82` and firmware `2023-03-03, fpga rev 32`.

## What this does not say

- **Not what to do about it.** The passthrough is a read path only — there is no
  measured write path to the die — so nothing here suggests a cure. It gives a
  run the ability to know, on its own boot, that its camera is not locked.
- **Not that `held` means locked.** §2. It means this call did not catch the loop
  in the act.
- **Not a cold-boot count.** Every boot here is a hub power cycle with the same
  10-second gap, in one unattended scene, over about an hour. #32's cold-boot
  question is a different one and is untouched.
- **Nothing about gain or white balance.** The check writes exposure and reads
  exposure. `cam_image_auto_mask(0)` masks all three loops, and the two that were
  not measured may behave differently.
