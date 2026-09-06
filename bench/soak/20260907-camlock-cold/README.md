# The cold run #30 asked for, second attempt — written before the first frame

*2026-09-07, Monday. Board powered down at 05:24 JST and left unpowered; the
session starts no earlier than 06:00. Scene unchanged from yesterday — the blank
wall about 30 cm from the lens. Nothing is shown to the camera at any point.*

**Everything in this file was committed before any run was launched.** That is
the entire point of this directory, and it is the second directory to make the
claim: [`../20260906-camlock-cold/`](../20260906-camlock-cold/) was aborted after
one pair when the instrument turned out to be broken, and its design was never
spent.

## The design is incorporated by hash, not by copy

The hypothesis, the protocol, the three pre-registered tests, the discard rule,
the timing limit and the two original amendments are the ones in
`../20260906-camlock-cold/README.md` **as of commit `de22117`**, together with
Amendment 3 in the same file at that commit. They are not restated here. A git
hash cannot drift and a copy can, and the thing being protected is precisely
that the design did not move once numbers existed.

`run.sh` in this directory is a copy of that directory's with three differences,
all of them recording and none of them design: `DIR` points here; each run's
line in `session.log` now carries `relit=N`, the number of times #33's ramp
rescue fired during that boot; and the `frames=` field has been fixed. Nothing
about the order, the arms, the number of frames requested or the discard rule
differs. `diff` the two files.

The third one is a bug found at 05:30 today, before the first frame, and it is
written here rather than folded in quietly. `frames=` was
`grep -cE '^ *[0-9]+ '`, which matches no frame line — they all begin with the
word `frame` — and instead matched the three indented timing lines under
`stopped :`. 20260906 recorded `frames=3` for a run of 602 and nobody caught it,
because that session was being abandoned for other reasons. It now reads the
board's own `602 frames, 602 good`, and prints `NO stopped LINE` when a run
never reached its summary. Nothing was scored off the old field; it is
provenance, and the discard rule is the greps below it.

In one line, so this file is readable on its own: **eight pairs, sixteen runs of
600 frames, `free` and `lock` alternating with the leading arm swapping every
pair, scored by `tools/probe_camlock.py` on `common` and `margin`.** The primary
test is a one-sided Mann–Whitney U on `common`, locked against free, over all
eight pairs.

## What is different today, and all of it is a precondition rather than a change

**1. The board was warm and was deliberately cooled.** Yesterday's session left
it running, and at 05:21 today it was still enumerated on hub port 1 and looping
frames — powered for about twenty-one hours, which is the opposite of the
condition the hypothesis needs. VBUS was dropped at **05:24** and the board left
unpowered.

This looked like the weaker cold start of the two, and at 05:35 — still before
the first frame — the bus trace said otherwise. 09-06's header claims the board
had been idle since 08-25; `host/usb_watch.py`, which has been polling every hub
port once a second since 08-16, records the board's port holding
`0103 power enable connect [2e8a:000f Raspberry Pi RP2350 Boot ...]` on every
heartbeat from **2026-08-27 19:57 to 2026-09-06 06:04**. That is nine days
**powered and enumerated in the bootloader**, not twelve days on a shelf. The
missing `/dev/cu.usbmodem` was read as a missing supply. See Amendment 4 in that
file and [`../usb_watch-20260825-20260907.log.gz`](../usb_watch-20260825-20260907.log.gz).

So on VBUS this session is the **colder** of the two, not the warmer: forty
minutes of genuine power-down against nine days of never having been powered
down at all. On the sensor the two are closer, because BOOTSEL leaves the FPGA
unconfigured and `cam_begin()` unrun, so 09-06's sensor had been unclocked for
nine days even while the board had not been off for a minute. Which of those two
#33 is a property of is exactly what is not known, and it is the reason both
numbers are written here instead of one summary word.

What the hypothesis needs is a sensor that is still re-deciding, which a genuine
power-down supplies; what it also wants is a cold room at dawn, which 06:00 JST
in September supplies about as well as 06:41 did. If the session shows nothing,
"not cold enough" is available as an excuse and is therefore written down *now*,
before it can be reached for later — and it is now a weaker excuse than it was
an hour ago.

**2. The firmware is pinned.** `firmware/build-280/forgix_m9.uf2`, rebuilt at
05:24 today from `firmware/` at commit `1d51090`, md5
`41296ef69f431ec063ba3f0118c3cd38`. This is the build with #33's ramp rescue and
its instrumentation in, as Amendment 3 requires. Yesterday's `.uf2` on disk
predated the commit and was replaced; that it needed rebuilding at all is the
reason the md5 is written here.

The board is expected to be running this already. It will be confirmed from the
first banner rather than assumed — the instrumented build prints a `reset gate:`
line that no earlier build has — and if the line is absent the board is flashed
before pair 1 and this paragraph is amended, not quietly satisfied.

**3. `run.sh` now catches both of `ft_acquire()`'s doubts.** It grepped only for
`EXPOSURE NEVER SETTLED`; the quiet form, *the exposure never moved from its
first reading*, is the one that let `m9_cue-20260816-172256.log` be scored for
three weeks. Commit `90a2189`.

## The room light, and the one thing that can tell dawn from drift

Nothing in 09-06's design says anything about the light, and it should have. The
tertiary test is the sign of the per-arm slope of `common` against position in
the session; this session runs 06:00 to roughly 07:20 JST in September, so
**sunrise happens inside the measurement window and has the same shape as the
decay the hypothesis predicts.** The primary is safe — the arms alternate and
the leading arm swaps every pair, so a monotone drift in the room falls on both
arms equally — but the tertiary is not.

The room light is not touched during the session and the curtains stay as they
are. Today is overcast, which flattens the ramp, and that is a helpful accident
rather than a control.

**The control is that the session already carries its own photometer, and only
one arm can be it.** `last frame mean RGB` is recorded for every run. In the
`free` arm it is worthless for this — the auto-exposure loop is running and
absorbs a change in the room, so the field reads flat whether the light moved or
not. In the `lock` arm `--enrol=40:L` freezes exposure and gain at frame 40
(one press; white balance keeps running, so this is a luminance photometer and
not a colour one), and every frame after that reports the room as it actually
is. The arms alternate, so the session yields **eight fixed-exposure brightness
samples spread evenly across the eighty minutes**.

Stated now so it cannot be chosen afterwards: **if the eight `lock` mean-RGB
readings show a monotone trend across the session, the tertiary test is reported
as confounded and not as a result.** If they are flat, the tertiary stands as
pre-registered. This is a diagnostic on an existing test, not a new test, and it
changes nothing about the primary or the secondary.

## Recorded for #32, with no test pre-registered

`last mean RGB` per run, as 09-06 planned, and now also **the number of acquires
the rescue fired on**. Sixteen cold boots is the first sample of what the rescue
is worth on a cold board rather than on one that has been running all day. It is
an observation this session gets for free, not a question it was designed to
answer, and no test is pre-registered for it.

## The one number that would make this session void

If **every** run in a session is flagged by `run.sh`, there is no measurement
here at all and the discard rule cannot dig it out — that is 09-06's abort
happening again, and the response is to stop and say so rather than to score
what is left. Stated in advance because the temptation at run twelve is to keep
going.

---

# Amendment made AFTER the session started, and the reason everything above still stands

**Everything above this line was committed before any run was launched. This
section was not.** It is below the rule because the session it describes was
aborted at 06:03 after one and a bit runs, and the honest place for that is
after the boundary rather than folded into a file whose whole claim is that it
predates the numbers. The design did not change. The firmware did.

## The pinned firmware was not the appliance's operating point

The precondition above pins `firmware/build-280/forgix_m9.uf2`, md5
`41296ef69f431ec063ba3f0118c3cd38`, and says it will be confirmed from the first
banner rather than assumed. It was confirmed, and the banner said something the
md5 could not:

```
clock     : 280 MHz system, core 1.25 V
link      : configuration C, 3 forward data lines, 140.0 MHz
```

`firmware/build-280/` is a leftover from the 2026-08-15 clock sweep, one of six
build directories that differ in `FGX_SYS_KHZ` alone. It was picked yesterday
because it was the directory that happened to be to hand, and pinning its md5
recorded the mistake precisely without catching it. `firmware/CMakeLists.txt:67`
is `set(FGX_SYS_KHZ "320000")`, and the table above it ends `320/160 ... <- ships`.
**Both sessions this one is meant to sit beside ran 320**: `20260825-camlock/`
and the one pair of `20260906-camlock-cold/`.

The clock alone would be an argument for a footnote. What made it an abort is
eleven lines further up the same comment:

> #9 (the board drops off USB) and **#12 (a byte lost on the camera bus at
> 280/140 and never at 150/75)** are both open and both are unexplained
> flakiness on the fast side

280/140 is the one operating point with an open, unexplained camera-bus fault
against its name, and this is a session about the camera. Running #30's drift
measurement on #12's reproduction condition would have put two unresolved camera
issues in the same sixteen runs with no way to tell them apart afterwards. It
would also have ended pooling with 08-25, which the design calls legitimate for
the primary.

**What was done.** `firmware/build/` — the ordinary build directory, already at
`FGX_SYS_KHZ=320000`, and differing from `build-280` in that variable and
nothing else that `CMakeCache.txt` records — was rebuilt from `firmware/` at the
same commit `1d51090`. `cam.c`, `frame.c` and `m9.c` recompiled. The board was
flashed with `host/bootsel.py --flash` and verified, and VBUS was dropped again
at **06:05:28** to restart the cooldown.

**The firmware pin is therefore replaced, and this is the only thing in this
file that changed after a frame was taken:**

| | before | after |
|---|---|---|
| build dir | `firmware/build-280/` | `firmware/build/` |
| `FGX_SYS_KHZ` | 280000 — a clock-sweep leftover | 320000 — `<- ships` |
| md5 | `41296ef69f431ec063ba3f0118c3cd38` | `878d646213995e05c43c67f9b8b639d6` |
| source commit | `1d51090` | `1d51090`, unchanged |

Nothing about the hypothesis, the arms, the order, the frame count, the discard
rule, the three tests or the mean-RGB diagnostic moved. The one number the
session is now warmer by is the cooldown: 05:24:14–06:00:00 became
06:05:28–restart.

## What the aborted run is worth keeping for

[`aborted-280/`](aborted-280/) holds the whole of it: `free-1.log`, truncated at
frame 326 by the interrupt, and a two-line `session.log`. **Not scoreable and
not to be scored** — wrong clock, no `stopped :` summary, and its partner never
ran.

It is kept because its banner is the first cold-boot evidence that #33's ramp
rescue does what it was built to do:

```
camera : exposure ramp 12 12 13 13 12 12~ 12 13 13 12 13 12~ 12 12 12 12 12 12~ 14 17 21 23 27 ... 132 133 133 133 132
camera : live 128x128 RGB565, id 0x82, 16.0 MHz, expose 37 ms, read 16 ms, exposure settled after 45 frames
         reset gate: polls 425, first state 21, busy yes   defaults polls: ae 0 ag 10 awb 0 wbmode 10
         #33: the auto loops were switched back on 3 times during the ramp
         mean RGB 133 133 133
```

Eighteen frames flat at 12–13 is the #33 plateau exactly as the faulted benches
in `bench/cue/` show it. The rescue fired three times, the ramp then climbed to
133, and the run came out at **`expose 37 ms`** — the healthy signature — instead
of the 58–59 ms the plateau was heading for. One boot is one boot and this is an
observation with no test behind it, as the #32 section above says. But it is the
first one taken on a board that had been unpowered for thirty-six minutes rather
than running all day.

The `reset gate:` line is also the confirmation the precondition asked for: the
instrumented build was on the board, established from the banner and not
assumed. It was the line immediately below it that turned out to matter.

## And the `frames=` fix earned itself inside three minutes

`session.log` reads:

```
06:02:54  end    free-1  frames=326 frames, NO stopped LINE  mean RGB 133 133 133  relit=3
```

The field was corrected at 05:30 this morning, before the first frame, for
reasons that were entirely about tidiness. Under the old expression this line
would have read `frames=3` — which is also what it printed for 20260906's
complete 602-frame run, and for a run that died at frame 40. A truncated run and
a clean one would have been indistinguishable in the session log of the session
that got truncated.

---

# Results — written after the session, 2026-09-07

**Everything above the first rule predates the numbers. This section does not.**
The session ran 06:42:26 to 07:39:43, sixteen runs, no interruption.

## The session is valid and it is the cleanest one yet

Sixteen of sixteen runs completed. Every run returned **601 or 602 good frames
of 602**, `!!` appears zero times in any log, and none of `run.sh`'s discard
greps matched: no `EXPOSURE NEVER SETTLED`, no `the exposure never moved from its
first reading`, no `usb: N outages`. Every run booted `expose 37 ms` — the
healthy side of #33 in all sixteen. The void condition did not come close to
firing.

## The primary test does not reject, and the point estimate runs the other way

One-sided Mann–Whitney U on `common`, locked against free, eight against eight,
as pre-registered:

| | lock | free |
|---|---|---|
| `common` walk, median | 2.36 | 2.52 |
| `common` walk, mean | **3.24** | **2.41** |
| `common` walk, range | 1.25 – 5.73 | 1.38 – 2.94 |
| U (lock < free) | **32.0** | |
| **p, one-sided** | **0.52** | |

`uv run --script bench/soak/20260907-camlock-cold/score.py` regenerates every
number in this section from the sixteen logs beside it.

**Not significant, and the mean is higher with the lock on, not lower.** #30
predicts locking the auto loops reduces the common-mode walk. On the cold run
the issue asked for, it does not.

The secondary — `margin` should not move — is also flat: lock 2.43 against free
1.88, one-sided p = 0.75, two-sided p = 0.57. There is no separation to
attribute to anything, so the docstring's "if locking moves `margin` more, the
mechanism is not the one in the issue" is not triggered either. Nothing moved.

## Two things the pre-registration did not cover, recorded and not resolved

Both are observations. Neither has a test behind it and neither should be given
one after the fact.

**1. The lock arm is bimodal and the free arm is not.** Three lock runs sit at
5.23, 5.49 and 5.73 `common` walk with per-frame sd 2.0–2.3; the other five sit
at 1.25–2.60 with sd 0.75–1.18. Every free run is inside 1.38–2.94. The three
wide ones are the three whose level moved between the freeze and the last frame
— lock-3 137 → 155, lock-5 131 → 117, lock-7 131 → 116, against ≤6 levels of
movement on the other five. What that means is not established here.

**2. The photometer is neither monotone nor flat, so the dawn diagnostic does
not resolve as written.** The eight `lock` last-frame readings in session order
are 130, 129, **154**, 132, **115**, 132, **113**, 131. The rule above says
monotone → tertiary confounded, flat → tertiary stands. This is a third shape
the rule did not anticipate, and picking either branch for it now would be
choosing afterwards, which is what the section exists to prevent. **The tertiary
is therefore reported as neither, pending an operator who was in the room.**

One thing to check before reading those readings as the room: `#33: the auto
loops were switched back on 1 time during the ramp` appears in **lock-4 through
lock-8 and not in lock-1, lock-2 or lock-3.** Whether the rescue can fire after
the frame-40 freeze, and so whether the freeze held for the whole of those five
runs, is not answerable from these logs.

### Settled from the source, later the same day

It is answerable from `firmware/`, and the answer is no. **The rescue cannot
fire after the freeze, in any run, structurally.** `ft_acquire()` is called
exactly once, at `firmware/m9.c:2940`, as boot check 3 — one call site in the
whole binary, ahead of check 4, which is the wait for a query set on USB CDC.
`relit` is a local of `ft_acquire()` and can only be incremented inside its
ramp loop, which is bounded by forty frames and `FT_RAMP_BUDGET_US`. The `'L'`
press arrives over that same CDC, in the frame loop, which cannot begin until a
query set has arrived — so the ramp is over before frame 0 of the run exists.

**Those five lines are boot-time events and say nothing about the lock arm's
validity.** They do not divide the arm, and the three wide runs are not the
three that show them.

What does not follow is that the freeze held. `firmware/cam.h:245` is explicit
that it cannot be assumed — "the register takes a switch, not a number — so a
lock can only ever mean *stop deciding*, never *use this number*", and
"**STOP DECIDING IS NOT HOLD WHAT YOU DECIDED**". But the drift measured there
on 2026-08-25 was attributed to the **AWB** loop dropping the colour gains
towards unity, and `CAM_LOCK_STEPS[1]` is `CAM_AUTO_WB` — this arm leaves AWB
free on purpose, precisely to miss that failure. That measurement also has
overall brightness "barely moving, 133 to 126", where the three wide runs here
moved luminance by 14 to 18.

So the known switch-off drift does not account for what these logs show, and
nothing else in the source does either. **Recorded as an open question, not
resolved, and not to be given a mechanism from eight runs.** It is the question
`docs/camera.md`'s first two items were already the way to answer.

## What this does not say

It does not say the camera's auto loops are harmless, and it does not close #30.
It says the intervention #30 proposed, run cold, on a motionless scene, with the
instrument fixed and every mechanical check clean, **did not reduce the walk it
was proposed to reduce.**
