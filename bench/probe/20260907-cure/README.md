# The last untried cure for #33 is not one. `0x02` reaches the die and does not help

*2026-09-07, eighteen boots of `forgix_cam_cure`. Not a bench: no cue schedule,
no enrolment, no held-out set, no accuracy. One question — can anything the
board can write put the exposure lock back?*

[`../20260907-lockrate/`](../20260907-lockrate/) left #33 looking closed and
uncurable: the die keeps overwriting the exposure the mask was supposed to stop
it writing, on 32 boots of 33, and the I²C passthrough is a read path so nothing
can reach the die's own AE enable. That argument is sound about the passthrough
and says nothing about `0x02`, which is on the ArduChip, is writable, and can
put the sensor die through reset, sleep or a power cycle on its own. It had
never been tried.

It has now, 54 times an arm, and the answer is no:

| arm | what it does to `0x02` | held |
|---|---|---|
| **N** | nothing — the re-bring-up with the write left out | **10 / 54** |
| P | sleep: set bit 1, wait, restore | 8 / 54 |
| R | reset: clear bit 0, wait, restore | 6 / 54 |
| C | power cycle: clear bits 2 and 0, wait, restore | 6 / 54 |

**The null arm is the best of the four.** No intervention beats doing nothing,
which is the rule this probe was written against, fixed before the first boot:
*an arm is a cure if it comes back `held` more often than N does, and it has to
do it across boots.* None does. Per boot, an arm beat N on 5 of 54 arm-boots and
N beat an arm on 13; on 13 of the 18 boots every arm scored zero together.

## Why the arms are real, and how the first boot nearly got away with saying so

The first boot printed `N=0/3 R=0/3 P=0/3 C=0/3` and that result was worth
nothing, because nothing in it showed the `0x02` write reaching anything. Four
arms that all do nothing also agree perfectly. The run was thrown away and a
positive control added ahead of the arms: hold each line *down* — do not pulse
it — and take a picture.

```
  0x02 positive control - each line held down, not pulsed:
    reset held      0x02 reads 04, picture did not come back
    sleep held      0x02 reads 07, picture did not come back
    power off held  0x02 reads 00, picture did not come back
    3 of 3 changed the picture
```

3 of 3 on all 17 boots that carry the control. The register takes the value, and
each of the three lines individually stops the camera returning a frame. These
are real interventions on the real die. They just do not fix the lock.

## What the interleave is for

The fault moves on its own — nine of lockrate's 33 boots gave two different
answers to the same question inside ten seconds — so a before-and-after pair
around one intervention proves nothing. An arm that does nothing at all will
still "cure" a boot about one time in seven.

So all four arms run inside one boot, twelve slots, each arm three times, in
positions `N R P C  C P R N  R N C P`. Anything monotone through the boot — the
scene, the die warming up, the ArduChip's own temperature — moves all four arms
together and cannot separate them. N is measured on the same board, in the same
scene, in the same minute as the arm it is the control for, and nothing here is
compared against a number from another run.

**N is a re-bring-up, not an idle wait.** A sensor that has been reset or
power-cycled has lost its register state, so every arm re-runs `cam_begin()` and
`cam_image_defaults()` afterwards, and N does too. The only difference between N
and the other three is the write to `0x02`.

That makes N interesting on its own account. `../20260907-awb/` found that
re-running `cam_begin()` recovered 0 of 7 deaf boots; N is the same question at
54 observations, and it comes back `held` 10 times — which is not a recovery
rate, it is the background rate. Lockrate measured 15 `held` in 99 checks (15 %)
with no bring-up in between at all. This probe's 30 in 216 (14 %) is the same
number. **Re-running the bring-up does not change the odds either.**

## The runs

Every boot: bring up, warm up 20 captures, ask once (`before`), run the control,
run the twelve slots. `held` is the die keeping both `0x0080` and `0x0400`.

| boot | before | N | R | P | C |
|---|---|---|---|---|---|
| [`cure00`](cure00.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure01`](cure01.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure02`](cure02.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure03`](cure03.log) | **held** | 3 | 2 | 3 | 0 |
| [`cure04`](cure04.log) | dragged | 1 | 2 | 2 | 1 |
| [`cure05`](cure05.log) | **held** | 1 | 0 | 1 | 1 |
| [`cure06`](cure06.log) | dragged | 2 | 1 | 1 | 2 |
| [`cure07`](cure07.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure08`](cure08.log) | dragged | 1 | 0 | 1 | 0 |
| [`cure09`](cure09.log) | dragged | 0 | 0 | 0 | 1 |
| [`cure10`](cure10.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure11`](cure11.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure12`](cure12.log) | dragged | 1 | 0 | 0 | 0 |
| [`cure13`](cure13.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure14`](cure14.log) | dragged | 0 | 1 | 0 | 0 |
| [`cure15`](cure15.log) | dragged | 0 | 0 | 0 | 1 |
| [`cure16`](cure16.log) | dragged | 0 | 0 | 0 | 0 |
| [`cure17`](cure17.log) | dragged | 1 | 0 | 0 | 0 |
| | | **10** | **6** | **8** | **6** |

`cure00` is the run without the positive control and is kept because it is the
run that showed the control was needed. Its four zeros are not evidence of
anything and are not in the totals above — the totals are over `cure00`–`cure17`
inclusive at 54 slots an arm, and `cure00`'s arms happen to contribute zero to
all four.

No slot's capture died in 216 slots. `0x02` at boot read `0x05` — the documented
default — on all 18.

## What this does not say

- **Not that `0x02` does nothing.** It plainly does: three lines, three dead
  cameras, 17 boots out of 17. It does not touch *this*.
- **Not that #33 is uncurable**, only that the two candidates anyone had are
  spent: `0x07` bit 7 (0 of 7 in `../20260907-awb/`) and now all three lines of
  `0x02`. What is left is not on this board's register map.
- **Not a cold-boot count.** #32 asked whether `0x02` could stand in for a board
  power cycle and collect its samples in minutes. This probe answers a different
  question with the same register and does not answer that one; what it does add
  is that the sensor really can be cycled on its own, so the sampling idea is
  still live.
- **Nothing about gain or white balance.** Exposure written, exposure read.
