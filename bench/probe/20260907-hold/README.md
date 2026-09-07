# Gain and white balance both hold. Exposure is the one that fails — and so does *un*locking

*2026-09-07, eight boots of `forgix_cam_hold`. Not a bench: no cue schedule, no
enrolment, no held-out set, no accuracy. `cam_image_auto_mask(0)` masks three
loops and every measurement made for #33 so far has been about one of them.*

`'L'` claims the camera is held still. [`../20260907-lockrate/`](../20260907-lockrate/)
showed the exposure half of that claim fails on 32 boots of 33. This directory
asks the other two, and the answer is not the one the exposure result made
likely:

1. **The die keeps the gain at `0x3001`, and a written gain stays written.** 0 of
   20 polls dragged, on all 7 boots that ran the poll. The gain lock takes.
2. **The white-balance lock holds for the full 40 seconds**, on all 7 boots that
   had a control to hold it against, including every boot whose exposure lock
   was failing at the same moment. *The register holds. The picture under it
   does not — see the 2026-09-08 addendum in §3 before using this.*
3. **What fails instead is the unlock.** Switching the loops back *on* did not
   take on 2 to 6 of 8 attempts a boot — a fault nobody has been looking for,
   pointing the opposite way from #33.

## 1. `0x3001` is where the gain lands

Stage G writes `0x055` and `0x0aa` — complements, so an address holding a
counter cannot pass by accident — with every loop masked, and sweeps 1024
addresses from `0x3000` three visits an arm, interleaved `A B B A A B` so that
a drift through the sweep cannot separate the arms. An address is kept only if
all three visits inside an arm agree *and* the two arms differ.

```
  sensor   gain 0x055     gain 0x0aa     verdict
  0x3001   55 55 55      aa aa aa      RESPONDS, and echoes the write byte for byte
```

One address out of 1024, on all 8 boots, echoing the written byte exactly. The
manual gain register `0x31`/`0x32` reaches the die and lands at `0x3001`.

`0x301b` also responded on 2 boots (`29` → `33`) — the same address
[`../20260907-awb/`](../20260907-awb/) saw tracking *exposure* in a
white-balance sweep. It is not stable across boots here and it is not claimed.
That it now follows a third thing is the argument for why none of these
addresses gets a name.

## 2. The gain lock takes, which exposure's does not

Finding where a write lands is not finding that it stays there — the AE loop
puts its own value back within a second of frames, which a read taken right
after the write walks straight past. So stage GH is the exposure check's poll,
at the address stage G found:

```
  wrote 055, die: 55 55 55 55 55 55 55 55 55 55
  wrote 0aa, die: aa aa aa aa aa aa aa aa aa aa
  0 of 20 polls came back changed
```

**140 polls over 7 boots, 0 dragged.** Four of those boots had their exposure
lock dragging at the same moment, under the same `cam_image_auto_mask(0)`. So
whatever is wrong with #33 is specific to exposure and is not "the mask does not
reach the sensor" — the mask reaches the sensor fine for gain.

## 3. The white-balance lock holds, and the control is the other arm

[`../20260907-awb/`](../20260907-awb/)'s stage D — does the WB lock hold over a
session — never ran, because its positive control needed an operator changing
the scene colour and the probe could not tell a held lock from a still scene.

Stage W removes the operator by interleaving the arms: free, locked, free,
locked, sixteen visits over 40 seconds, reading the two addresses that AWB found
move on the WB bit. The separation between adjacent arms *is* the control, and
it is re-measured every visit rather than assumed once at the start.

```
   t(s)  arm     0x332b  0x33ca  channel means
      2  free    10      44      R 134 G 131 B 134
      5  LOCKED  18      41      R 138 G 148 B  95
      7  free    10      44      R 135 G 132 B 133
     10  LOCKED  18      41      R 129 G 139 B  86
     ...
     38  free    10      43      R 133 G 131 B 133
     40  LOCKED  18      41      R 138 G 148 B  94
```

**Every locked visit on every boot read `18`/`41`** — the same pair, at second 5
and at second 40, on all 7 boots. The locked arm never wandered. The blue
channel sits near 90 locked and near 133 free, the same direction
[`../../soak/20260825-camlock/`](../../soak/20260825-camlock/) saw.

**Added 2026-09-08.** That last sentence is in the wrong tone and it cost a day.
Blue at 90 was written here as corroboration — the means move with the register,
so the register must be doing something. It is corroboration. It is also the
frame being unusable, and this directory never said so, because a probe that
reads registers has no opinion about pictures. When #30's arm was widened to
include the white balance on the strength of §3's headline, the frame went
green: `R 133 G 153 B 79` against a free control's `R 133 G 130 B 132`, which is
the table above, reproduced through `demo.py` on a scene instead of a probe.

A gain dragged down to a stuck value reads exactly as still as a gain held at a
good one. Every stillness test in this directory passes either way, so **"the
white-balance lock holds" is a claim about `0x332b` and about nothing else.**
The arm shipped in `m9` is the gain and nothing else; see
[`../../../docs/bring-up-log.md`](../../../docs/bring-up-log.md), 2026-09-08.

## 4. The unlock is the thing that fails

The first boot voided, and the reason is the finding. The pooled rule — no value
in one arm may appear in the other — is correct and it fired, but it could not
say which arm had failed. The locked arm had read `18`/`41` on all eight visits.
It was the **free** arm that came back reading `18`/`41` on five of its eight:
`cam_image_auto_mask(CAM_AUTO_ALL)` had not switched the white-balance loop back
on, and the channel means agree — blue at 89, not 133.

So the reporting was changed after that boot and before the next, and the three
outcomes were written down first:

- no free visit differed → **VOID**, the control never fired
- some did, locked arm still → **the lock holds**, and the free visits that
  failed to differ are the *unlock's* failure rate
- some did, locked arm wandered → the lock does not hold

Over the 7 boots that carried it, the unlock failed 2, 3, 4, 5, 5, 5 and 6 times
out of 8 — **31 of 56 attempts**. Writing `CAM_AUTO_ALL` restores the loop
slightly less than half the time.

This matters beyond #33. Every arm in this repo that measures a "camera free"
condition writes `CAM_AUTO_ALL` and assumes it took, and `cam_image_defaults()`
ends by doing exactly that on every boot. A control arm that is silently still
locked half the time is a control arm that is not a control.

## The runs

| boot | exposure | gain polls dragged | WB | unlock failures | responders |
|---|---|---|---|---|---|
| [`hold00`](hold00.log) | held | *stage not built yet* | VOID | — | `0x3001` |
| [`hold01`](hold01.log) | held | 0 / 20 | holds | 2 / 8 | `0x3001` |
| [`hold02`](hold02.log) | **dragged** | 0 / 20 | holds | 3 / 8 | `0x3001` |
| [`hold03`](hold03.log) | held | 0 / 20 | holds | 5 / 8 | `0x3001` |
| [`hold04`](hold04.log) | **dragged** | 0 / 20 | holds | 5 / 8 | `0x3001` |
| [`hold05`](hold05.log) | **dragged** | 0 / 20 | holds | 6 / 8 | `0x3001`, `0x301b` |
| [`hold06`](hold06.log) | held | 0 / 20 | holds | 5 / 8 | `0x3001` |
| [`hold07`](hold07.log) | **dragged** | 0 / 20 | holds | 4 / 8 | `0x3001`, `0x301b` |

`hold00` is the run that showed stage W needed the per-visit count; its gain
poll had not been written yet and its WB verdict is the void that prompted §4.
`0x300a`/`0x300b` read `36 4c` on all 8, so the passthrough was answering
throughout.

## What this does not say

- **Not that `'L'` is safe.** Two of its three loops lock; the third does not,
  and switching any of them back on is unreliable. Both halves need handling.
- **Not that a held white balance is a usable white balance.** §3 measures
  `0x332b`, which holds, and says nothing about whether the frame under it is
  worth classifying. It is not: it is green. The two claims were conflated once
  already, on 2026-09-08, and the fix is in that day's log entry.
- **Not that the gain lock is useful yet.** `0x31`/`0x32` respond and the value
  sticks, but `../20260907-manexp/` measured the gain curve as non-monotone —
  peaking at `0x010`, dipping through `0x100`. A wanted gain still has to be
  measured off that curve.
- **Not a rate for the unlock fault.** 56 attempts on 7 boots in one scene over
  one evening. It is large enough to say the fault is real and common; it is not
  a characterisation.
- **Nothing about why exposure differs from gain.** Both are written through the
  same surface and both land on the die. Only one gets dragged back.
