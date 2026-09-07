# A read-only witness for #33, and the measurement that says it needs help

*2026-09-07, seven boots of `forgix_m9`. Not a bench: no cue schedule, no
held-out set, no accuracy claimed. One question — can a scoring run tell whether
its own 'L' press did anything?*

[`../20260907-lockrate/`](../20260907-lockrate/) gave #33 a rate (the exposure
lock fails on 32 boots of 33), [`../20260907-cure/`](../20260907-cure/) showed
there is nothing on this board that fixes it, and
[`../20260907-hold/`](../20260907-hold/) found the fault pointing the other way
as well — `cam_image_auto_mask(CAM_AUTO_ALL)` fails to switch a loop back ON 31
times in 56. All three used a probe binary. None of it helps a run in m9, which
is where 'L' actually gets pressed.

`cam_exposure_lock_check()` cannot simply be called there: it writes two manual
exposures into the sensor and leaves the second one behind, so the frames around
it are exposed by the instrument. This directory is about what m9 got instead,
and about the honest limit of it.

1. **A read-only witness on every 'L' press**, reading exposure, gain and white
   balance off the die a few frames later and again eight frames after that. On
   a static desk it moved **once in 26 windows**. It is nearly blind, and that is
   a property of the scene, not a bug.
2. **So the useful comparison is across the mask write, not within a window.**
   That fired on 11 of the 17 windows that had a previous press to compare
   against, and it is what makes the log worth reading.
3. **A `'K'` hotkey for the question that does answer**, running the write-based
   check on demand. It costs the frames around it, so it is a key and not
   something the boot path does.
4. **A verdict of `held` is not good news.** On 3 of the 8 `'K'` presses that
   could report it, the die was still sitting on the check's own `0400` twelve
   frames later, through `cam_image_defaults()` and through an 'L'. The rest of
   that run is exposed by a keypress.

## 1. Two samples eight frames apart say almost nothing

The witness takes a sample 4 frames after the press and another 8 frames after
that, and asks which of the three registers differs. Movement is conclusive: a
loop the mask asked to freeze that is still revising is a lock that did not take.
Stillness is not, and the reason is the wall
[`../20260907-awb/`](../20260907-awb/)'s stage D hit — a converged loop on an
unchanging room sits exactly as still as a locked one.

That is not a theoretical caveat here. Across 26 windows on 7 boots:

```
  windows where something moved inside the window   1
  windows where nothing moved                      25
```

The one exception is `wit06`'s first press, where the gain read `07` and then
`05`. Everything else, including presses that set **all three loops free**, held
its value for the whole eight frames. A tracking AE loop pointed at a still desk
reads the same `00c4` twice.

So the within-window test is kept because it is the only conclusive one, and it
is documented as usually silent rather than usually passing. Silence is not a
lock that took.

## 2. What does carry information is the value across the mask write

The registers do move — just not within one mask's window. White balance reads
`10` free and `18` locked, exposure `030b` or `00c4` free and `0400` after a
check. Those changes land *at* the write.

So the witness carries the previous press's last sample forward and compares it
to the next press's first, per loop, and only for loops that were standing still
in that earlier window — a loop already revising would differ whatever the write
did. That line fired on 11 of the 17 windows that had a predecessor.

```
  wit06, press at frame 57
    exposure 00c4 00c4, gain 06 06, wb 18 18
    did not move: exposure gain white-balance
    changed across the mask write, after standing still under the previous one:
      exposure gain white-balance
```

It is a weaker claim than the within-window one and is printed as a separate
line saying a different thing: the write reached those loops. Whether the loop
then *ran* is what the eight frames were supposed to answer, and mostly do not.
The two samples it spans are a whole inter-press gap apart, so a loop that
started revising for scene reasons somewhere in that gap looks identical.

## 3. `'K'`, and why it is not on the boot path

`'K'` runs `cam_exposure_lock_check()` — mask everything, write `0x0080`, read
it back, write `0x0400`, read it back — and then re-runs `cam_image_defaults()`.
It is the only instrument that answers on a still scene, because it supplies the
change the room will not.

It is a hotkey for the reason `cam_image_defaults()` now carries at length: a
build that perturbs every acquire makes every bench after it incomparable with
every bench before it. On the same argument the boot path stays bit for bit
where it was, and a run that wants the answer spends the frames deliberately and
prints the boundary.

```
  camera    : #33 lock check at frame 14 - dragged (wrote 0400, die said 030b).
              Frames either side of this line were taken under an exposure no
              rule asked for.
```

`wit02` answered `dragged` at frame 14 and `held` at frame 57. `wit04` answered
`held` and then `dragged`. Two boots that each gave both answers inside about 20
seconds, which is lockrate's within-boot instability showing up in a scoring run
rather than in a probe.

**Two `'K'` presses on one boot are not two independent trials.** Once the die is
parked on the check's exposure (below), the AE loop is not running, so the next
press's writes hold trivially and it reports `held` for a reason that has nothing
to do with the mask. `wit05` is exactly that: `held` at 14, parked, `held` at 57.

## 4. `held` leaves the sensor on the check's exposure

`0x0400` is a number this run invented. Nothing a light meter would land on. So
if the die is *still* reading `0400` after `cam_image_defaults()` has run and
twelve frames have gone by, the AE loop is not running — a live one would have
moved off it. That is an equality against a value the run wrote itself a moment
earlier, not a constant fitted to anything, and it is the one thing the witness
can state outright on a still desk.

```
  AND THE DIE IS STILL ON THE CHECK'S OWN EXPOSURE 0400, 12 frames on. The AE
  loop is not running: cam_image_defaults() did not get it back, and every frame
  from here is exposed by that keypress rather than by the room.
```

It fired on 3 of the 8 `'K'` presses in builds that could print it, and a fourth
is visible by hand in `wit02`: exposure `0400` at frames 61, 69 and 84, through
an 'L' press, to the end of the run.

This is 20260907-hold's unlock fault arriving where it costs something. The
restore `cam_image_defaults()` performs is the same `cam_image_auto(true)` every
"camera free" arm in this repo relies on.

## The runs

Seven boots, and **they are not seven runs of one binary** — the instrument was
being built while it was being read. `wit00` has no cross-press comparison,
`wit01` adds it, `wit02` adds `'K'`, `wit03` adds the parked-exposure line,
`wit04` onward fix the key named on the witness line. Read the column, not the
total.

| boot | schedule | `'K'` verdicts | moved in-window | across the write | parked |
|---|---|---|---|---|---|
| [`wit00`](wit00.log) | L L L | — | 0 / 3 | *not built yet* | *not built yet* |
| [`wit01`](wit01.log) | L L L | — | 0 / 3 | 2 / 2 | *not built yet* |
| [`wit02`](wit02.log) | K L K L | dragged, held | 0 / 4 | 2 / 3 | *not built yet* |
| [`wit03`](wit03.log) | K L K L | held, held | 0 / 4 | 1 / 3 | 0 / 2 |
| [`wit04`](wit04.log) | K L K L | held, **dragged** | 0 / 4 | 1 / 3 | 0 / 2 |
| [`wit05`](wit05.log) | K L K L | held, held | 0 / 4 | 2 / 3 | 2 / 2 |
| [`wit06`](wit06.log) | K L K L | held, held | 1 / 4 | 3 / 3 | 1 / 2 |

Every boot ran `host/demo.py "a book" --frames 90` with the presses scheduled by
`--enrol`, on the same desk, over about forty minutes. No sample came back
unreadable, which is now 0 in 26 windows here on top of 0 in 144 checks in
lockrate — the passthrough itself is not the flaky part of any of this.

## What this does not say

- **Not a rate for anything.** Seven boots, one scene, one evening, on five
  different builds. The 1-in-26 is the number this directory exists to record;
  everything else in the table is there to show the instrument behaving.
- **Not that 'L' is now safe to use.** It says a run can find out afterwards
  whether 'L' meant anything, sometimes. It does not make the lock take.
- **Nothing about the third white-balance value.** `0x332b` reads `10` free and
  `18` locked in 20260907-hold, and reads `00` in several samples here, always
  shortly after a `'K'`. Not chased, not claimed, and one more reason no address
  on this die gets a name.
- **Not a reason to put the check on the boot path.** The opposite: §4 is what
  putting it there would do to every run in the archive.
