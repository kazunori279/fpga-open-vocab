# The cold run #30 asked for — written before the first frame

*2026-09-06, Sunday, from 06:41 JST. Board idle since 2026-08-25 and found in
BOOTSEL with no `/dev/cu.usbmodem`, so this is a genuine cold start. Desk empty,
nothing shown to the camera at any point.*

**Everything above the results heading was committed before any run was
launched.** That is the entire point of this directory.
[`../20260825-camlock/`](../20260825-camlock/) reached a coherent reading — the
lock helps while the room is still settling and its benefit closes in the second
half — and could not quote it, because the half-and-half split was chosen after
seeing the numbers. This session exists to give that reading a chance to be wrong
in advance.

## The hypothesis, stated as a prediction

A camera lock can only help a sensor that is still re-deciding. So the benefit of
freezing exposure and gain should be **largest in an unsettled room and should
decay as the session warms**, which is why this run starts at dawn on a cold
board rather than at any convenient hour.

If that is right, this session should show a locked-versus-free gap that is
present early and gone late. If the gap is flat across the session, or absent,
the 08-25 reading was the shape of eight numbers and not a mechanism.

## The protocol, fixed in advance

Identical to 08-25's so the two sessions can be pooled, and nothing about it is
chosen once numbers exist.

- **Eight pairs, sixteen runs**, 600 frames each. Not "until it looks clear".
- Arms **alternate**, and which arm leads **swaps every pair**, so the arm is not
  confounded with position within a pair.
- Every run is `host/demo.py "a closed book" "an opened book" --no-smooth
  --frames 600 --leave-running`, with `--enrol=40:L` added on locked runs. The
  queries only define the axis. The desk is empty throughout.
- `demo.py` re-enters `ft_acquire()` each run, so the lock state resets between
  runs and the arms cannot leak into each other. 08-25 verified this with
  `statecheck-{1,2}.log`; it is not re-verified here.
- Scored by `tools/probe_camlock.py`, `common` and `margin`, both the range of a
  centred 31-frame mean after dropping 60 frames of ramp.

**Discards.** A run is dropped only for a mechanical failure named at the time it
happens — a USB outage, a camera bus stall, an `EXPOSURE NEVER SETTLED`. A run is
never dropped for its value. A dropped run is re-run at the end of the session,
not in place, and is listed below with its reason.

## The tests, fixed in advance

| | test | why this one |
| --- | --- | --- |
| **Primary** | Mann–Whitney U on `common`, locked vs free, one-sided (locked < free), all eight pairs | Exact replication of 08-25's test, which gave U = 46/64, p ≈ 0.07 |
| **Secondary** | The same test restricted to **pairs 1–4** | The cold window. 08-25's post-hoc split was its first half of eight, so this is that split, pre-registered |
| **Tertiary** | Sign of the per-arm slope of `common` against position in session | The decay the hypothesis actually predicts, and the thing a two-group test cannot see |

The primary is the one that counts. The secondary is the hypothesis under test and
is underpowered at four a side by construction — the smallest one-sided p it can
reach is 1/70 ≈ 0.014 — so a null there is not evidence of absence and will not be
reported as one.

**What would make the 08-25 reading survive:** the secondary significant with the
tertiary showing a negative free-arm slope and a flat locked one. **What would
retire it:** a flat gap across the session, or no gap.

Pooling with 08-25 is legitimate for the primary and only the primary, since the
protocol is identical. It is not legitimate for the secondary, because 08-25's
window was defined after the fact.

## Timing, and the limit it puts on this

600 frames is about four to five minutes, so eight pairs is roughly eighty
minutes and the session ends near 08:10. **The cold window is therefore only
pairs 1–4.** Runs cannot be shortened to fit more into it: the walk statistic is
a range over 31-frame windows, and a 300-frame run would shrink it for reasons
that have nothing to do with the lock.

This is a real ceiling on the design and is stated here rather than discovered
later. If the effect needs a colder room than pairs 5–8 see, this session can
only ever measure it in half of itself.

## Also recorded, for issue #32

`last mean RGB` per run, because the AEC's boot-time branch is now its own issue
and sixteen cold boots on an unmoved desk is exactly the sample it wants. No test
is pre-registered for it — it is an observation this session gets for free, not a
question it was designed to answer.

## Two amendments, both made before the first soak run

Recorded as amendments rather than edited into the sections above, because the
point of those sections is that they did not move.

**1. The first acquire after the power cycle had a dead AEC.** `bootsel.py`
fell back to power-cycling hub port 1 to get the board out of BOOTSEL, and the
very next run read `mean RGB 18 18 10` with the ramp going
`13 13 13 13 13 13 14 14 14 14 15 15 ... 15` — two counts over forty frames, and
`EXPOSURE NEVER SETTLED`. The room was lit and the lens was clear. Re-running
immediately gave `17 88 97 106 115 123 127 128 130 131 133 134 135 132 128`,
settled after 15, `mean RGB 130 128 128`. So the sensor comes up with its AEC
stopped on the first acquire after a cold power cycle and is fine on the second.

The failed run is kept as `dark-1.log`. This changes nothing about the protocol —
`EXPOSURE NEVER SETTLED` was already on the discard list as a mechanical failure,
and `run.sh` greps for it after every run — but it is the reason the sixteen runs
below are not the board's first acquire, and a cold-boot session is exactly where
that distinction could have gone unnoticed.

**2. The scene is a wall at 30 cm, not an empty desk.** 08-25's sixteen runs were
an empty desk; the rig currently faces a blank wall about 30 cm away, and it was
left there rather than re-aimed, because re-aiming means an operator at the rig at
the start of a session whose whole premise is that nothing moves.

This costs something specific and it is not the primary test. Locked and free see
the same wall, so the within-session comparison is untouched. What weakens is the
pooling with 08-25, which the table above called legitimate for the primary on the
grounds that the protocol is identical — it is not identical, the scene differs,
and a flat low-texture surface at 30 cm may carry less z-score structure than a
desk. **Pooling is therefore downgraded from planned to conditional**, to be
argued for on the evidence or dropped, and the primary test stands on this
session's own sixteen runs.

## Results — ABORTED after one pair. There are no soak numbers in this directory

**Do not read `free-1.log` or `lock-1.log` as an arm of anything.** The session
was stopped 07:52 into it because the instrument was broken, and what is in here
is the diagnosis, not the measurement. #30's cold run has still never been taken.

`free-1` came up with its frame at the sensor's floor — `mean RGB 23 26 18`, ramp
`12 11 10 9 10 11 12 13 14 15 ... 20` over forty frames, `EXPOSURE NEVER SETTLED`.
`lock-1` immediately after it was fine, ramping to 133. Ten more acquires put the
rate at four in ten, and an hour later at ten in twelve, on a lit wall thirty
centimetres away with nothing moving.

That is [#33](https://github.com/kazunori279/fpga-open-vocab/issues/33): the
sensor's three auto loops come up disabled on some acquires and
`cam_image_defaults()` does not notice. The dumped frame is the striped backdrop,
in focus and correctly framed, underexposed and **green** — `21 26 17` — which is
the signature `cam.h:243` records for the AWB loop being off. Cycling `'L'` back
round to `CAM_AUTO_ALL` mid-run took the same run from `21 26 17` to
`130 127 127` without anyone touching the board or the room, which is both the
rescue and the proof.

The pre-registered discard rule covers this — `EXPOSURE NEVER SETTLED` is a named
mechanical failure — but the rule assumes failures are rare enough to re-run at
the end of a session. At four in ten and rising it stops being a discard rule and
starts being pressure to retry in place, which is the design change the
pre-registration exists to prevent. Stopping was the cheaper mistake.

**The pre-registration above stands and is not spent.** No soak run in this
directory was scored, no test was applied, and no number here has been looked at
against the hypothesis. When #33 is fixed, `run.sh` and the sections above can be
re-used unchanged, and that session's results go in a new dated directory rather
than in this one.

## The afternoon: two dead register waits, and a signature that reads before the ramp

Same day, same wall, same rig. #33 item 2 asks why `cam_image_defaults()`'s write
does not take, and the register dump above could not answer it because all nine
bytes are identical on dead and live acquires. So the thing to instrument was not
the state but the *waits* — how many 100 µs polls each `cam_wait_idle()` actually
spent. A wait that exits on its first read never gated anything.

**The reset gate is not the fault, and that is a real negative.** The first
suspicion was `cam_wait_idle("reset")` in `cam_begin()`: it reads
`CAM_REG_SENSOR_STATE` on the SPI transaction right after the reset write, and if
the ArduChip has not begun executing the reset yet, that read returns the
pre-reset IDLE and the wait returns having waited for nothing. Eight acquires say
otherwise. Every one of them read `first state 21` — `& 3 == 1`, not IDLE — and
`polls 425`, about 42.5 ms, **identical on the two that came up dead and the six
that did not**. The gate gates, it is deterministic, and it does not discriminate.

One level down it does. The four writes in `cam_image_defaults()` — auto exposure,
auto gain, auto white balance, white balance mode — each have their own
`cam_wait_idle()`, and those poll counts separate the two outcomes completely:

| | ae | ag | **awb** | **wbmode** | mean RGB |
| --- | --- | --- | --- | --- | --- |
| live, 27 acquires | 0 or 10 | 0 or 10 | **10** | **5** | 126–137 |
| floor, 7 acquires | 0 or 10 | 10 | **0** | **10** | 23–38 |

**34 acquires, 7 of them at the floor, and the white-balance pair agrees with the
frame every single time.** `ae` and `ag` are noise — `ag 10` appears in four live
runs and `ae 0` in one — so it is specifically the wait after the auto white
balance selector, and the wait after the write that follows it, that carry the
signal. That matches the picture: the dumped floor frame is *green*, and
`cam.h:220` already records that `CAM_REG_WB_MODE_CONTROL` is the register that
moves blue from 42 to 133.

*Read the correction near the end of this file before quoting the pair. On a
further 36 acquires `awb` stopped separating and `wbmode` did not; the surviving
claim is about `wbmode` alone.*

This is the first host-side predictor of the fault, and unlike everything before
it, **it reads before a single frame is captured** rather than after forty.

### What it rules out, and what it does not say

Issuing `cam_image_defaults()` **twice does not fix it**, which was measured and
not assumed. Twelve acquires with the call doubled failed four times — the same
rate — and the signature simply *moved*: every first call then read
`awb 10, wbmode 5`, including on runs that came out green, and the second call
carried `awb 0, wbmode 10` on exactly the four that did. So the frame follows the
last write sequence, and the write is not being dropped on the floor for a repeat
to pick up. The doubled call was removed again.

That also isolates what the `'L'` rescue had. Cycling `CAM_AUTO_ALL` back in at
frame 20 took a run from `21 26 17` to `130 127 127`; a second call in the boot
path does nothing. The difference is not repetition — it is **the twenty captures
in between**, which is #27's territory rather than a write-ordering bug.

**What the poll counts mean is still open.** "Exits at poll 0, so the write was
not latched" is the obvious reading and it is not the only one: good runs spend
10 + 5 across the last two waits and bad runs spend 0 + 10, which looks as much
like the busy period landing in a different slot as like a write going missing.
The correlation is 34 for 34; the mechanism behind it is a claim this directory
has not earned.

**And the rate moves on its own.** Across today: 4 in 10 at 06:55, 10 in 12 at
07:15, then 2 in 8, 3 in 10, 4 in 12, and a last batch of 12 with **no failures at
all**. Nothing was fixed between the fourth batch and the fifth. Any future "the
fix works" has to survive that, and a single clean batch will not be evidence.

## The rescue, and the frame cap that was built to defeat it

#33 item 3 asks whether `ft_acquire()` should verify rather than write. It now
does: when the ramp's own convergence window is full of real frames and the
exposure still has not moved, the loop re-issues `CAM_AUTO_ALL` and re-bases the
ramp on that moment.

**The trigger is not a new threshold.** It is exactly the state the banner has
always described as *"the exposure never moved from its first reading"* — the
only change is that the loop used to report it after forty frames rather than
act on it after six. And there is no reset: the recovery already in that loop is
#27's and reaches for `cam_begin()`, because a constant fill means the sensor
never started. This is the opposite state — the sensor is writing frames and only
the loops are off — and a reset would throw away the captures that are the
ingredient doing the work.

**Sixteen acquires with the cap still at 40:**

| | n | outcome |
| --- | --- | --- |
| `wbmode 5`, rescue never fired | 7 | all settled in 10–15 frames, 126–135. Untouched |
| `wbmode 10`, rescue fired and worked | 7 | all settled, 24–36 frames, **129–136** |
| `wbmode 10`, rescue fired six times and failed | 2 | 35 43 35 and 42 49 42 |

Nine acquires hit the fault and **seven came back as ordinary frames**. Before
this they would all have been floor frames.

The signature also got sharper, and now with something causal behind it:
**`wbmode 10` predicted "will need rescuing" 16 times out of 16**, before the
ramp started. Every `wbmode 5` run had `relit=0`; every `wbmode 10` run had
`relit≥1`.

### The two failures were the rescue's own design, not bad luck

Both spent all six attempts. Six attempts one settle-window apart is 36 frames,
so the sixth fires with four frames left — and a successful rescue demonstrably
needs fifteen to twenty to converge, since the seven that worked settled at 24 to
36. **The last attempt could not have paid off however well it worked.**

The comment above that loop had claimed for two milestones that 40 was a backstop
and `FT_RAMP_BUDGET_US` was the bound. With the rescue in, that stopped being
true: the cap was the bound, and it bound in a way that broke the rescue. So the
cap is now `FT_RAMP_FRAMES 200` and the 25 s budget binds again, which is what
the comment always said.

The cost is real and it is the right way round. A camera the rescue cannot save
now spends the full 25 s at boot instead of 6, once per run, and reports
`EXPOSURE NEVER SETTLED` exactly as before. A run that is minutes long can pay
nineteen seconds for the chance not to be thrown away.

### Twenty more with the budget as the bound: eight hit the fault, eight recovered

| | n | outcome |
| --- | --- | --- |
| `wbmode 5`, rescue never fired | 12 | settled in 12–17 frames, 125–136 |
| `wbmode 10`, rescue fired | 8 | **all settled**, 25–42 frames, 125–134 |

One to four attempts each. **Three of the eight settled at 40, 40 and 42 frames**,
so under the old cap one would have been over it and two would have been exactly
on it — the cap change is load-bearing and not tidying.

Across both batches: 36 acquires, 17 in the fault state, 15 recovered. The two
that did not are the two the old cap had already made impossible.

### A correction, to something written earlier in this same directory

The section above says the *white-balance pair* — `awb` and `wbmode` — separated
the two outcomes 34 for 34. On 34 acquires that was true. On 70 it is not.

**`awb` stops separating.** Eight of the seventeen runs that turned out to need
rescuing read `awb 10`, which is the live value. It agreed on the first sample and
then disagreed about half the time on the second, which is what a coincidence
looks like when you give it more data.

**`wbmode` holds on all seventy.** `5` and the loops are on; `10` and they are
off. It has never once been wrong, it reads before the ramp starts, and it now has
something causal underneath it rather than a correlation: every `wbmode 10`
acquire went on to need the rescue and every `wbmode 5` acquire did not.

That does not make the mechanism known. The reading is still that the wait after
the white-balance-mode write is spending 10 polls where a healthy one spends 5,
and why that tracks the state of three auto loops is not established here.

### What is actually in here

| file | what it is |
| --- | --- |
| `dark-1.log` | the first floor frame, right after `bootsel.py` fell back to a power cycle |
| `free-1.log`, `lock-1.log` | the one aborted pair. `free-1` is a floor frame. **Not data** |
| `altcheck/a{1..6}.log` | six back-to-back acquires: fine, fine, dead, fine, dead, fine |
| `regdiff/r{1..12}.log` | twelve acquires with the new register dump — ten dead, and every register identical |
| `regdiff/snap.log`, `dead-frame.png` | the floor frame — the backdrop in focus, underexposed and green |
| `regdiff/relight.log`, `rescued-frame.png` | the `'L'` rescue, `21 26 17` to `130 127 127` in one run, same wall |
| `regdiff/torch.log` | inconclusive and kept anyway: 60 frames while the room light was waved, but `demo.py` logs no per-frame luma, so it could not answer what the two PNGs did |
| `gate/g{1..8}.log` | the reset gate measured: `polls 425, first state 21, busy yes` on all eight |
| `gate/w{1..10}.log` | per-write poll counts, one `cam_image_defaults()` call. 3 floor frames, 10/10 |
| `gate/d{1..12}.log` | the same with the call issued **twice**. 4 floor frames, the rate unchanged and the signature moved to the second call |
| `gate/s{1..12}.log` | back to one call. 12 live, 0 floor — the batch that says the rate moves by itself |
| `relit/r{1..16}.log` | the rescue in, frame cap still 40. 9 hit the fault, 7 recovered, 2 spent all six attempts |
| `relit2/r{1..20}.log`, `summary.txt` | the rescue with the 25 s budget as the bound. 8 hit the fault, **8 recovered** |
| `session.log` | `run.sh`'s wall clock, ending where it was stopped |

`run.sh`'s post-run failure check was wrong on its first outing and is fixed. It
grepped for the topics `camera bus` and `USB`, which the banner prints
unconditionally — "camera bus: worst gap 14 us against the 2000 us deadline",
"usb: 0 outages" — so `free-1` was flagged for three mechanical failures when it
had one. It now matches the failing form of each and not the word. `session.log`
still carries the three bogus flags, because it is what the session actually
printed.
