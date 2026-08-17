<!-- moved out of README.md on 2026-08-01; see ../README.md#documentation -->

# Bring-up log

Dated entries, newest first — what was straps, what was measured, and what was
got wrong on the way. **Append-only**, and deliberately so: several entries here
exist only to record a claim that later turned out to be false.

[← back to the README](../README.md) · [architecture](architecture.md) ·
[building](building.md) · [history](history.md) · [dev plan](milestones.md)

---

### 2026-08-17 — better staged, and the degenerate enrolment moved from the origin to the pair

Two more runs, 08:55 with the empty rotation and 08:57 with
`--no-revisit-empty` as [#19](https://github.com/kazunori279/fpga-open-vocab/issues/19)'s
control. The book was propped up this time. Neither confirms
[#18](https://github.com/kazunori279/fpga-open-vocab/issues/18) either, and the
first one fails in a way the morning's guard cannot see.

**Run 1: the two references landed on the same point.** `nearest pair 0.20
apart`, against 3.25 in the morning — `an opened book` at `(+3.76, −3.76)` and
`a closed book` at `(+3.62, −3.62)`. With everything else 15 to 32 sep away the
board called **every frame absent**: the presence stage "held" 90/90 of the
empty desk and kept 0/120 of the classes, and `MATCH` read 0/126. A stage that
answers "nothing there" to everything is not a stage that works.

**And the staging was right, which is how the pictures earned their keep on the
first run that had them.** The enrolment windows are on disk as
`m9_cue-20260817-085504-f0086.png` and `-f0126.png`: a held-open book filling
the frame, and the closed cover. Ten seconds of looking ruled out the framing
explanation that took the 07:33 run an hour of arithmetic. What collapsed is
the *contrast*, not the picture: `z(opened) − z(closed)` was **−6.02** in the
opened-book window and **−6.62** in the closed-book one. With two queries the
centred space is one-dimensional and antisymmetric — a reference *is* the
contrast — so two scenes that read the same contrast have no geometry between
them, however different they look.

**The origin guard is blind to this, structurally.** It measures the distance
from the origin *in units of sep*, so a collapsed `sep` inflates every distance:
run 1's references sit 26 sep from the origin and the guard stayed quiet. The
check that is missing is the pair against the **scatter inside the enrolment
windows** — the board already averages 20 frames, so the spread of those frames
around their own mean is free, and a between-class distance that is not much
bigger than the within-class scatter is an enrolment that should be refused at
the console rather than scored ten minutes later.

**Run 2, the control:** `sep` 6.73, state stage **92/120 (76.7%)** held out —
and `SITS 0.27 SEP FROM THE ORIGIN`, so its geometry is doubtful in the
morning's way instead. The tally #19 now has to explain: 120/120 (08-11,
no-revisit), 57.5% and 58.3% (08-16, revisit), 96.7% (08-17 07:33, revisit),
76.7% (08-17 08:57, no-revisit). **The empty rotation does not order that
list.** What varies with it is the enrolment geometry, which nothing was
measuring until three days ago.

The two enrolment dumps are not what broke run 1: run 2 asked for the same two
and separated 6.73. The pictures also caught a bug in themselves — they are
named after the log, so run 2 overwrote run 1's pair. The base64 is in the
rotated log, so `cam.py` re-rendered them, and the log rotation now moves the
PNGs along with the `.cues` sidecar for the same reason it moves that.

---

### 2026-08-17 — the confirmation bench measured the staging instead of the rule, and the board said so before it started

[#18](https://github.com/kazunori279/fpga-open-vocab/issues/18)'s first run on
hardware, 07:33, `an opened book` against `a closed book` with `--enrol`. The
presence stage held **0 of 90** held-out empty frames. That is worse than the
level rule it replaced, and it is not the rule's fault: the log carries the
guard the entry below shipped, in capitals, from before the first empty revisit —

```
'an opened book' SITS 0.14 SEP FROM THE ORIGIN, which is where a scene identical to ...
```

**The origin is the empty desk, and one of the two references was standing on
it.** The references came out at `an opened book (-0.32,+0.32)` and `a closed
book (+1.98,-1.98)`, 3.25 apart, sitting 0.14 and 0.86 sep from the origin. The
empty desk is then 0.00–0.46 sep from the nearest reference and the class frames
are 0.03–0.80, so the two overlap almost completely: **AUC 0.319**, below the
0.500 that means no signal at all — the empty desk is on average *closer* to a
reference than a class frame is. `tools/probe_reject.py` sweeps every radius
from 0.25 to 3.6 sep and every hysteresis pair, and not one of them holds a
single empty frame while keeping the classes. There is no threshold to tune
here. Nothing about the run tests #18.

**What put the reference on the origin was the staging, not the geometry.** The
enrolment window landed in the first visit to the opened book, and that visit is
the weakest-framed of the three: the opened book reads z −1.61/−0.46 against the
closed book's −11.15/−14.64 in its own first visit. An opened book lying flat on
a desk *is* nearly the empty desk as far as the encoder is concerned, and the
background froze on that desk. Two things to fix at the bench and neither is in
firmware: prop the book up so it fills the frame, and check what the camera sees
before enrolling rather than after scoring.

**The state stage, on the same run, held 116/120 (96.7%).** That is the same
empty-revisit rotation that read 57.5% and 58.3% yesterday, which weakens
[#19](https://github.com/kazunori279/fpga-open-vocab/issues/19)'s hypothesis that
re-staging is what costs the state stage — with the caveat that today's geometry
is degenerate and one run is not a control.

**So the tooling gap got closed instead.** Nothing recorded what the board was
looking at while it learned, which is exactly the question this run raises and
cannot answer. Three additions, no firmware change — the `'P'` dump has been
there since M8a:

* `./ab.sh A B --frame-check` runs no experiment at all: camera on, a picture
  every 4 frames to `/tmp/fgx_preview.png`, Ctrl-C when the scene sits right.
* `--preview N` does the same during a real run. `cue.py` takes the 44 KB
  base64 block out of the stream before it reaches the bars and renders it on a
  worker thread, because a blocked pipe reader stalls the board.
* With `--enrol`, a picture of each enrolment window is kept beside the log as
  `<log>-fNNNN.png` whether or not anyone asked. Mid-window, so it is one of the
  20 frames that were averaged into the reference.

`host/cam.py --preview PNG` is the renderer, one fixed path rewritten in place
so a viewer left open on it follows the camera. It cross-checks its own mean RGB
against the `mean RGB` the board prints beside the dump — worth knowing that
this is a *decode-agreement* check and not a byte-order oracle, because
`cam_frame_means()` decodes hi-first too, the same as `CAM_HI_FIRST`. For the
byte-order question the only evidence is still the picture, which is why the
full `cam.py` writes both.

**Re-run owed**, better staged, still paired with `--no-revisit-empty`.

---

### 2026-08-16 — presence is a distance now, and it was scored on the frames that killed the old rule before anything was flashed

[#18](https://github.com/kazunori279/fpga-open-vocab/issues/18), the replacement
for the stage the entry below measures at 17.8% and 24.4%. The rule:

```
absent  ⇔  min_k ‖c[] − qref[k]‖  >  radius
```

Open-set rejection in the centred space the state stage already decides in, so it
inherits the drift immunity the level axis could not have — the level axis *was*
the drift. `m21_d` is the same minimum the state decision already takes, so the
frame loop pays one comparison. `radius` is in units of `sep`, the closest two
references, which is what lets a constant travel between rooms.

**Replayed first, flashed second, and that order is the point.** `c[] = z[] − lvl`
is recoverable from the frame lines, so `tools/probe_reject.py` scores the new
rule on the two runs that failed, no board involved:

| held out, at 2.0 sep | run 1 (17:22) | run 2 (17:35) |
|---|---|---|
| **empty desk held** | **81/90 (90.0%)** | **79/90 (87.8%)** |
| the level rule, same frames | 16/90 (17.8%) | 22/90 (24.4%) |
| class frames kept | 118/120 (98.3%) | 102/120 (85.0%) |
| AUC, empty vs class | 0.956 | 0.909 |

The probe reproduces `score_cue.py`'s numbers for the old rule exactly on the same
frames, which is what says the harness is measuring the rule and not itself. The
radii are read off a sweep rather than fitted: 2.0 sep is the single-edge optimum
on **both** runs, and 1.5/2.0, 1.0/2.0 and 0.5/2.0 sit within 0.4 points of each
other averaged over the two, so the narrow band was picked for its shape.

**No drift trend in the new quantity**, which is the claim that had to hold: the
three empty revisits sit at 3.03 / 3.48 / 3.06 sep in run 1 and 2.31 / 2.87 / 2.54
in run 2 — highest in the middle, not climbing. The old rule's equivalent walked
0.23 of a span in one direction across the same frames.

**The failure mode moved rather than vanished, and it is checkable at enrolment.**
`c[] = 0` is where "nothing has changed since the background froze" lands, so a
reference sitting near the origin cannot be fenced off from a still desk. The
2026-08-11 run has `a closed book` at **0.49 sep** from the origin and its baseline
is inseparable there (AUC 0.624) — that run cannot test this rule at all. The
08-16 runs sit at 3.16 and 0.97 and work. The board now measures this when the
last class is enrolled and prints `SITS n.nn SEP FROM THE ORIGIN` in capitals if
it is under half a `sep`, which is four minutes found rather than spent.

Shipped with it: `'0'` is gone from the console and from `cue.py`'s schedule (it
is accepted and explained rather than silently ignored), `--baseline` only has to
freeze the background now, and the frame line carries **`d`**, the distance in
sep — because #15's lesson was that the log recorded the verdict and not the
quantity it came from. `score_cue.py` reads `d` when it is there and scores the
old logs unchanged when it is not.

**Not yet benched.** Every figure above is offline replay. The confirmation run
pairs with [#19](https://github.com/kazunori279/fpga-open-vocab/issues/19)'s
`--no-revisit-empty` control so one session settles both.

---

### 2026-08-16 — the presence stage fired on a bench for the first time, and holds one frame in five

The last unmeasured claim in the decision rule ([#15](https://github.com/kazunori279/fpga-open-vocab/issues/15))
had never been measured for a reason that was in the schedule rather than in the
rule: the only empty desk `cue.py` ever cued was the baseline at the head of the
run, which is both before the rule engages *and* the segment the empty reference
is taught from. `cue.py` now returns to an empty desk **once per cycle, last in
the rotation**, so the first revisit lands at frame 140 with the rule live from
134, and `tools/score_cue.py` scores those segments on their own. Two runs of
`./ab.sh "an opened book" "a closed book" --enrol`, 90 held-out empty frames
each:

| | run 1 (17:22) | run 2 (17:35) |
|---|---|---|
| **held** — what the stage buys | **16/90 (17.8%)** | **22/90 (24.4%)** |
| called present, wrongly | 74/90 | 68/90 |
| what it called them | `a closed book` 73 | `a closed book` 67 |
| visit 1 (cue 140) | released 24 frames after the cue | released 18 frames after the cue |
| visits 2 and 3 | 30/30 present, never released | 30/30 present, never released |

**The replayed 0/30 was training accuracy.** It was the baseline scored against
references taken from the baseline, and it said the stage would hold on every
frame. On frames nothing was fit to, it holds one in five, and after the first
revisit it does not hold at all.

**Two things are wrong, and only one of them is the desk.** The enrolled anchors:

| | `absent_lvl` | `an opened book` | `a closed book` | span |
|---|---|---|---|---|
| run 1 | −0.46 | −8.24 | −14.94 | −11.13 |
| run 2 | +0.16 | −13.42 | −17.33 | −15.53 |

`absent_lvl` ≈ 0 in both, and that is arithmetic rather than a fact about the
desk — key `0`'s window sits immediately after `--bg-tau` froze the background,
so it measures the freeze against itself and would read ≈ 0 with a book in shot
too. It stores the freeze, not an empty desk. That is fixable. The second one is
not: the presence axis **is** the common mode, and the common mode is where the
drift lives — it is the exact term `c[i] = z[i] - lvl` subtracts to make the
state stage drift-immune. Run 2's three empty revisits read 0.21 / 0.32 / 0.44 of
the span, rising monotonically, against a `FGX_PRESENT_OFF` of 0.15. That is the
sensor warming up, priced in fractions of span, and it is why visit 3 is worse
than visit 1. The two distributions overlap by −0.87 of a span at their worst
cases, so no pair of edges separates them and retuning is not a fix.
[#18](https://github.com/kazunori279/fpga-open-vocab/issues/18) proposes
rejecting on `min_k ||c[] - qref[k]|| > radius` instead — the same centred space
the state stage already decides in, which inherits its drift immunity and removes
the empty-scene enrolment entirely. `m21_d` and `m21_sep` are already computed
every frame, and the rule can be replayed off these two logs before any firmware
change, because `c[] = z[] - lvl` is recoverable from what the frame lines print.

**The same runs put the state stage in doubt, which was not the plan.** It scored
58.3% and 57.5% held out, against 120/120 on 2026-08-11 with the same rule, the
same two phrases and the same board. The two failures do not look alike — run 1
separates the scenes cleanly (`a closed book` AUC 0.954) and puts the labels on
the wrong side of the split, 46 of 66 closed-book frames called `an opened book`;
run 2 barely separates them at all (AUC 0.647 / 0.595). The one thing that
changed is this bench: emptying the desk three times means the books were
re-staged three times, where on 08-11 they plausibly sat still for all three
visits. If that is it, 120/120 was measuring a desk that never moved and the
honest headline is the lower number.
[#19](https://github.com/kazunori279/fpga-open-vocab/issues/19) has the runs and
the `--no-revisit-empty` control that tells them apart. Until it is run, **the
120/120 in the README is not safe to quote.**

Logs: `/tmp/m9_cue-20260816-172256.log`, `/tmp/m9_cue-20260816-173537.log` (run 2;
it was still `/tmp/m9_cue.log` when this was written and `cue.py` has since
rotated it), and `/tmp/m9_cue-20260811-072207.log` for the run they are against.

---

### 2026-08-16 — it was never USB: the PSRAM's chip select had the QSPI bus the whole time

**The entry below reads the 1,987 outage as a USB fault and offers two endings,
and neither is right.** The board was not leaving the bus. It was losing the
flash — and everything above that, USB included, is downstream of a core that
cannot fetch an instruction.

**What made it findable was refusing to power-cycle first.** The recovery is a
VBUS cut, a VBUS cut is a power-on reset, and a power-on reset is also the thing
that erases the evidence, so every previous outage had been recovered before it
could be asked anything. This one was caught in BOOTSEL instead — a 280 MHz soak
died at frame 1,554, and 6.9 s later the same board re-enumerated as
`2e8a:000f RP2350 Boot` with the pull-up never dropping at all. In that state:

```
picotool info                            ->  Program Information: none
picotool save -r 0x10000000 0x10001000   ->  high-entropy noise
  ... the same 4 KB, three times         ->  three different answers
picotool verify forgix_m9.uf2            ->  ERROR: contents did not match
uhubctl -l 2-1 -p 2 -a cycle             ->  the SAME flash verifies OK
```

**Nothing was ever corrupted.** The bus was jammed, and only removing the 5 V
cleared it.

**GPIO0 is U1's chip select.** The APS1604M PSRAM shares the RP2354A's QSPI bus
with the in-package flash on QMI CS1, which on this board is GPIO0
([pinmap](pinmap.md)); `PADS_BANK0_GPIO0_RESET` is `0x116`, so the pad comes out
of reset with the pull-down enabled and the pull-up off; and `m9` does not link
`hardware_psram` — it has no `.psram_load` to place — so the QMI never takes the
pin. Nothing else does either: the firmware's pins start at GPIO1. **The
pull-down therefore holds U1 selected for the whole run**, orders of magnitude
past the part's ~8 µs tCEM, watching every read the flash answers and free to
decide one of them was addressed to it and drive `SD0..3` back. Only a VBUS cut
recovers it because only that power-cycles U1 — which is exactly why
`picotool reboot` and the firmware's own watchdog never did.

**One cause, both shapes.** XIP dies instantly, so D1 goes dark mid-frame and
the loop is not slow but gone; the watchdog fires 8 s later exactly as designed;
and the bootrom it hands control to cannot read the image either. Whether that
ends in USB boot or in a hang before the pull-up goes up is the difference
between "came back as `RP2350 Boot`" and "`0100 power` and stayed there". The
entry below saw the second and reasoned about the USB block.

**Two soaks retired the other suspects before the cause was found.** 280 MHz
died at frame 1,554; 150 MHz — a 1.87× slower clock, the most conservative point
in the project — died at frame **1,478**, seventy-six frames away. It is not the
clock. And a USB current meter photographed once every two seconds by a webcam
(`host/meter_cam.py`, filenames are the wall clock) read **5.09 V and 0.16 A
straight through the failure**, against 5.10 V / 0.14 A idle and 5.08 V / 0.20 A
under load: no sag, no spike, no collapse to zero. It is not the rail, and it is
not the cable.

**The fix is three lines and it is in `m9`'s `main()` before anything else** —
value, then direction, so the pin never drives low on its way up. It cannot be
truly first, because that code is itself running from XIP, but it takes the
exposure from a whole run down to a few milliseconds of boot. Driving the pin
beats linking `hardware_psram`: `m9` has no use for the 2 MB, and
`psram_detect_size()` returns 0 on this board for reasons [pinmap](pinmap.md)
still calls unexplained, so initialising a part nobody needs would only buy a new
way to fail.

| | before | after |
| --- | --- | --- |
| 3,000-frame runs completed | 1 of 5 | **5 of 5** |
| frame the board died at | 687 / 1,478 / 1,554 / 1,987 | — |
| frames run, outages seen | — | **15,008, zero** |

All five print `usb: 0 outages, 0 ms off the bus, 0 re-attaches`.

**What this does not close.** [#9](https://github.com/kazunori279/fpga-open-vocab/issues/9)
was filed for a board that *keeps computing* while off the bus — frame 71 to 244
with no banner and no counter reset — and that shape is a live firmware in a
dead pipe, not a dead core. A wedged QSPI bus cannot produce it: it stops the
core, so there is nothing left to keep counting. Nothing with that signature
turned up today either — the one 1-second pull-up drop in the 150 MHz runs came
back with a full banner and a counter reset, which is what `demo.py`'s own `'R'`
restart looks like, not a fault. The QSPI wedge explains the outages that end in
`uhubctl`; #9's original event is still unexplained, and the desk can now run
long enough to hunt it.

**The two instruments are the durable part.** `host/usb_watch.py` polls the hub
port every second and logs transitions, which is the only record that survives
the power cycle that erases watchdog scratch — it is what caught the two-stage
`connect`-then-`RP2350 Boot` signature that started all of this.
`host/meter_cam.py` photographs a USB meter on a timer, because the reading only
means anything if it was already on disk when the failure ended the session.
Both are cheap to leave running beside a soak, and neither existed a day ago.

### 2026-08-16 — the outage happens with the instruments on, and the reboot turns out not to be the recovery

**A 3,000-frame soak at 280/140 lost the board at frame 1,987, and for the first
time the desk could say what the board did about it.** 1,988 frames, all good,
about eleven minutes, and then the port went. `demo.py` followed it and gave up
after 45 s with nothing on VID 2E8A. `uhubctl` read `Port 1: 0100 power` —
power, enable and connect gone, so as far as the hub is concerned nothing is
pulling D+ up.

**D1 was dark, and that is the measurement.** The LED is written over the link
every frame and holds its last value if the loop stops mid-frame; at 1,987 it
was at full brightness on a `MATCH`. Dark is therefore not a frozen loop, it is
no loop — so the 30 s escalation fired, the board rebooted itself, came up, and
sat in the wait for `stdio_usb_connected()` that precedes the clock. **The
deliberate reboot happened and the bus still did not come back.** That is new,
and it is the opposite of what [#9](https://github.com/kazunori279/fpga-open-vocab/issues/9)'s
escalation was written to assume: rebooting is not a recovery for this shape,
because `stdio_init_all()` re-asserts the pull-up on the way up and the hub
still saw nothing. `uhubctl -l 2-1 -p 1 -a cycle` brought it back on the first
try, and it enumerated as the application with the old image still running.

**Two readings survive that, and this run cannot separate them.** Either
something on the board leaves the USB block in a state a chip reset does not
clear, or **the hub port latched and only cutting VBUS cleared it** — in which
case the fault is at this end of the cable and the issue has been named after
the wrong participant for three weeks. Everything seen is consistent with both.
The cheap test is a different hub, and it is worth doing before any more board
work.

**The instrument has a hole in it, and finding that is worth as much as the
event.** The reason line only exists if USB comes back: the stage, the frame and
the `POWMAN_CHIP_RESET` copy all live in watchdog scratch, and scratch does not
survive the power cycle that is the only known recovery. So *an outage that ends
in `uhubctl` is unattributable by construction* — the board knew why, and the
knowing died with the 5 V. Last words in a flash sector are the fix, and that is
now the next thing on #9.

Two smaller things the same run settled. **A cold scratch is not proof of power
loss**: `picotool reboot` came back cold with nothing wrong at all, because the
bootrom clears it too, so the banner now says "power, or the bootrom" rather
than claiming the stronger thing. And the camera-bus margin
([#12](https://github.com/kazunori279/fpga-open-vocab/issues/12)) is printed
only in the `stopped :` summary, which means **the runs most worth measuring —
the ones that end in a fault — are exactly the runs that never print it.**

### 2026-08-15 — three faults that only ever happened when nobody was watching

**None of #3, #9 or #12 left evidence, and that was the thing they had in
common.** A run that dropped off USB left a log that stopped; a byte lost on the
camera bus left a frame that was wrong; and a board parked at the bitstream
prompt could not be sent to BOOTSEL at all, so recovery was a strap at the desk.
All three were fixed the same way: make the fault happen on purpose, and make
the board the thing that reports it.

**[#3](https://github.com/kazunori279/fpga-open-vocab/issues/3): the one prompt
a host-side abort always lands on was the one prompt whose hotkey was eaten.**
`ft_recv_bitstream()` swallowed stdin while it waited for the `FGXB` magic.
`'B'` reaches BOOTSEL from there in **1.2 s** now, behind two guards — `'B'` is
the last byte of the magic, and a byte arriving inside a stream is data. 4,096
bitstream bytes carrying 16 × `0x42` leave the board at the prompt, checked.

**Writing that guard caught its own twin.** `m9`'s frame loop had no such rule,
so a re-run that pushed 173 KB into a board still looping from the previous run
sent it to BOOTSEL mid-download on a `0x42` in the data — and at the host that
is indistinguishable from #9. Every hotkey that costs a run (`B R W U I`) now
needs 100 ms of quiet either side. **The fault we spent the day building
instruments for, we also manufactured ourselves, twice, before lunch.**

**`picotool load` reports success without writing.** Twice in one session: the
progress bar ran to 100%, nothing printed wrong, the flash stayed at `0xff` and
`Program Information` read `none`. **`verify` is the check and `load` will not
tell you** — and timing is a hint, not the check: a real write of the 2.1 MB
image takes 15–22 s, the silent no-op took 2.5. A second and third `load` failed
the same way; `uhubctl -l 2-1 -p 1 -a cycle -d 3` then wrote and verified first
try. `host/bootsel.py --flash` does that loop now so the next person does not
have to know it.

**#12 has a number for the first time: 15 µs of worst camera-bus gap against a
2,000 µs deadline over 101 frames at 320/160, and 16 µs over 152 at 280/140.**
That is a margin, not a fault count, and it is the same margin at both rates —
125× clear — so whatever drops the byte on the fast side, it is not the bus
gradually running out of time, which is the hypothesis the issue leads with.

**#9: `tud_mounted()` lies.** Two of the three real outages this session left
TinyUSB certain the board was still attached while the hub showed no connect, so
a watcher built on the stack's own opinion could never have fired. The watch is
`usb_hw->sof_rd` now — the host numbers every frame, the counter is 11 bits and
wraps in 2,048 ms, so three identical samples at ~330 ms apart cannot happen
unless the packets have stopped. The board re-attaches itself in ~2.8 s and says
which frames went with it; after 30 s it reboots deliberately and the next
banner names that as a reason rather than as a hang. `'U'` and `'I'` make both
halves happen on demand, which is how they were checked rather than hoped for.

**And the report was correct and invisible before it was correct and useful.**
The first version printed at `tud_mounted()` — before the host raises DTR, which
is exactly when `stdio_usb` discards writes. It never reached a single log.

### 2026-08-01 – 2026-08-14 — where this log's gap went

**M14 through M21, the two clock audits, the three benches and the voltage-floor
sweep are not here.** They were written up in
[`milestones.md`](milestones.md) as they happened, and the frictions in
[`history.md`](history.md); this file was not being kept during that fortnight.

**No backfill was written, deliberately.** Reconstructing dated entries from
those write-ups would mean composing "what the board did on the bench that day"
out of a document that was not taking notes that day — which is the kind of
entry the append-only rule at the top exists to keep out. A pointer that is
honest about the gap is worth more than fourteen days of plausible diary.
[#6](https://github.com/kazunori279/fpga-open-vocab/issues/6).

### 2026-07-31 — M10 closed on a timing report, for 32 ms

**A milestone died twice in one day, and the second death cost five container
builds and no hardware at all.** M10's PSRAM half had already gone in the
morning, for want of a buyable QSPI breakout. The surviving half — give `u_tile`
its own clock so the MCU stops clocking it — was written up as worth ~450 of the
wire's 644 ms. Reading the RTL to scope the CDC work found that number was
arithmetic, not measurement, and wrong in two independent ways.

**RUN's 314 ms is compute, not transport.** `firmware/m7.c:490` sizes RUN's idle
bytes as `sweep = K*QG*(P+6) + 512` — the tile's own cycle count plus slack — and
23.24 Mclk over 314 ms is 74.0 MHz, which is `link_clk`. The bytes *are* the tile
computing. Give the tile an independent clock at the same speed and the MCU
idle-**waits** instead of idle-**clocking**: the bytes leave the wire, the time
stays. The entire prize was `22.3e6 x (1/75MHz - 1/f_tile)` — **zero at 75 MHz**.
And `f_tile` had never been measured, because `gemm_tile` had never been
synthesized on its own; both shipped builds report a critical path inside
`gemm_link`'s framing logic, so neither number was the tile's.

So `tile_probe.v` — an LFSR wrapped around the real `u_tile`, three pins, a 6.0 ns
constraint meant to fail, and `res.csv` checked for 8 multipliers and 21 memory
blocks so a folded-away tile could not report a fictional Fmax. The first four
seeds came back at **66 ± 3 MHz** and every one of them named a path in the drain
walk, which `gemm_tile.v:592-594` records as deliberately unpipelined. That is
the wrong answer to the question: the drain walk is not what runs for 314 ms.

Pipelining the walk behind a `DPIPE` parameter (default 0, and the compute loop
never sees it) moved the critical path in two seeds of three and lifted Fmax to
**69.9 MHz** — proving the first measurement was measuring the walk, and
producing the real one. **The new worst path is `wbuf` RAM output straight into a
`mult_18x18` input at logic level 0**: 5.264 ns of RAM clock-to-out, 6.802 ns of
net across a 52-unit hop, 2.716 ns of multiplier setup. There is no logic on it
to pipeline, retime or restructure, and the two hard blocks alone are 7.98 ns —
**125 MHz is the fabric's ceiling before a single track is routed.** `u_tile`
holds 21 of the T8F49's 24 memory blocks, so the placer has nowhere to put them
closer.

Reported 70 MHz, and the analyser is pessimistic by about 20% on this device
(`gemm_top` reports 62.449 and runs bit-exact at 75), so call it ~84 MHz real.
**Prize: 32 ms of 917, or 3.5%, for a second clock domain, two dual-clock RAMs,
four synchronisers, a drain handshake, a new skewed-clock testbench and a PLL
this repo has never instantiated.** Closed.

One correction worth recording against myself: `DPIPE` was promised to leave
`gemm_top` bit-identical and it does not. Built twice at seed 2 against the
pre-`DPIPE` source — flow verified deterministic first, by building the original
twice and diffing — the netlist gains one flop and one LUT, both attributed to
`u_link`, a module that was not touched. `u_tile` is unchanged in every column
that matters. The bitstreams differ; Fmax moved 62.449 → 64.737, inside the
±2.4 MHz seed spread. All three benches still pass bit-exact on 10,560
accumulators, which is the contract that actually holds, and the comment in
`gemm_tile.v` now states the measured truth rather than the intended one.

**917 ms is now the floor for this board, not just for this firmware.** The
lesson is the cheap one: the gate cost five builds because it was built as a
gate. Had Stage 1 been written first, the CDC would have been correct, the
testbench would have passed, and the board would have reported 885 ms.

### 2026-07-31 — 211 ms of CPU, 58 ms of frame, and why M7 ends here

**M7h landed both of its items exactly as costed and the frame kept 27% of
them.** The weight cache served **precisely** 43% of weight bytes — `1009 of 1856
passes built, 847 served from the cache`, identical in all six modes of both link
configurations, which is the arithmetic's ceiling and not one pass short of it.
The build fell 502 → **318 ms** serialized and 460 → **292 ms** on core 1.
`gw_pack3()`, freed of its 16-byte stack round trip, took `stage` from 97 → **70
ms**. Config C's frame went 975 → **917 ms**; config A 1,139 → 1,110; the third
data line is now worth **1.21×** and the whole thing **3.76×** the MCU's 3,448 ms.
All eight layers bit-exact in all twelve modes, 512/512 embedding floats exact,
174 of 174 blocks swept accumulator by accumulator.

**58 of 211 ms.** The serialized mode is the control and it behaves perfectly —
1,582 → **1,374 ms**, the full 208 ms, exactly where a model that adds columns
says it should be. The pipelined mode does not, because `W1_HI`'s 460 ms of
builds were running *inside* 641 ms of wire. Core 1 was busy; core 1 was not the
critical path. Making it 168 ms less busy made it idle sooner.

That is the third milestone in a row to remove a real, correctly-predicted
quantity and get a fraction of it at the frame. M7e moved work between cores and
got half. M7f-2 removed 286 ms of wire and got nothing, because a latent bug ate
precisely that much. M7h removed 211 ms of CPU and got 40%, because the work was
already hidden. **Three mechanisms, one shape**, and the shape is that this
project's cost model is a sum and the machine is a `max()`.

So M7 closes at 917 ms rather than continuing. Not because the remaining items
are done — pre-interleaved `weights.bin` is still there, ~15 ms of CPU, which
this milestone just priced at ~6 ms of frame — but because **every remaining
firmware item is smaller than the one that converted at 40%.** What is left of
917 ms is 644 of wire, of which **314 is RUN and RUN is the tile computing**, and
127 ms of core 0 stalled on a queue that got 14 ms shorter when core 1's load
dropped by a third. The first of those is the 265 ms floor showing up in a
measurement for the first time. The second is a queue-depth bound. Neither is
something more C will fix, and both are what
[M10](milestones.md#m10--take-the-tile-off-the-links-clock--closed-measured-70-mhz-and-the-prize-is-32-ms)
goes around.

One harness lesson, cheap: M7h gave `park()` an eight-second watchdog so a
finished board always returns to the bitstream prompt — the previous firmware
could go deaf on stdin while staying enumerated, which left a `park()` whose only
exits were all through stdin, i.e. no exits. It worked, and then the reboot it
causes dropped the CDC device mid-`read()` and crashed `m7.py` *after* a PASS.
**A completed run exiting 1 with a traceback is worse than the wedge it fixed**,
so `pump()` now treats a vanished port as quiet and lets the idle timeout end the
run in the ordinary way.

### 2026-07-31 — one jumper, soldered, and the 300 ms it did not deliver

**The PIN2↔PIN17 jumper is fitted, and configuration C is measured at last: three
forward data lines, 16.791 MB in 637 ms, 26.4 MB/s against M2's 26.8 MB/s
prediction and 8.94 MB/s on one line.** Bit-exact at 75 MHz through all six rungs
of the ladder plus the accumulator sweep, in the same boot as configuration A —
the board takes the second bitstream over the same USB CDC channel between the
two runs, so the comparison is two links and not two builds.

**The soldering was the part with a wrong assumption in it, and it was not about
soldering.** Pad number is not silkscreen number on this board: silk `0`–`12` is
pad = silk + 2 and silk `13`–`23` is pad = silk + 7, so silk `17` is pad 24 and
ball B3 — *not* the GND the silk sequence invites you to read it as. That was
checked with a meter before the iron came out, not after, which is the only
reason this paragraph is short. The finished joint measures 0 Ω to silk 2 and
100 kΩ to its neighbours.

**And then the frame did not move: 1,140 → 1,144 ms.** 286 ms of wire vanished
and every millisecond of it came back somewhere else. The command that made it
legible was one added the same afternoon for a different reason —
`gh_prof_t` splitting the wire *by command in link clocks*, because bytes are not
comparable across widths and clocks are. It read **WGT at 40.36 ns per 13.333 ns
clock**. A link clock cannot cost 40 ns; what that column was measuring was CPU
time inside a pipelined window, and the CPU was in `gw_locate()`, at 389 ms
against configuration A's 4.

**The cause is the kind that only a hardware change can surface.** `gw_locate()`
predicts where a response begins, and the prediction used a truncating division
where it needed `ceil`. **At width 1 those are the same number.** Five milestones
of daily use on configuration A could not have found it, and neither could any
host test, because the property is an agreement between the C and the Verilog and
only one of them runs on the laptop. Fixed, along with two things it exposed — a
shared hint slot that had been costing configuration A 348 misses a mode
unnoticed, and a bit-at-a-time scan whose cost was in the *hit* path — the frame
went 1,144 → **975 ms**, and the third data line went from 1.00× to **1.17×**.

The lesson is the same shape as [the USB hub](#2026-07-29--two-boards-one-alive-one-dead-corrected-2026-07-30), from the
other direction. There the variable that mattered was never moved; here moving it
was what revealed that something else had been wrong all along. **A measurement
that does not change when you change the hardware is not a null result — it is a
second thing to find.**

### 2026-07-30 — the driver stops scanning, and the FPGA finally beats the MCU

**M7a: 42 → 12 ms per block, still 2,048 of 2,048 bit-exact at every rate from 38
to 75 MHz.** The extrapolated frame goes from ~3,900 ms — *slower than the CPU* —
to **874 ms against a 3,358 ms MCU baseline**. That is the number M6 was supposed
to produce and did not. Phase table in [M7a](milestones.md#m7a--the-o1-driver--done-20482048-still-bit-exact-at-every-rate).

**The bug worth writing down is one the obvious fix would have missed.** The plan
this milestone inherited said to "measure the return path's byte offset once at
init with a NOP — it is a constant, because the MCU drives the clock." Reading
`gemm_link.v` before writing any code showed that is true for five of the six
commands and false for the one that matters: non-RUN responses start in `R_EXEC`
a fixed number of clocks after the last payload bit, but `is_run` branches to
`R_WAIT` (`gemm_link.v:487`) and holds the preamble until `busy` has risen *and*
fallen. RUN's offset therefore carries the sweep, not just the frame length — and
RUN is 8 of 28 transactions and **39% of the bits being scanned**. A single
NOP-measured constant would have left the largest share of the cost in place and
looked like a fix.

What shipped instead is a **signed, self-calibrating, per-command-class hint**:
latch `delta = preamble_end − ref` on first use, verify the full 32-bit preamble
at the predicted position on *every* subsequent use, and on mismatch rescan,
re-latch, and count the miss. Signed because a RUN response arrives *before* its
idle budget ends. The property that makes it safe to ship is that **it cannot be
wrong, only slow** — which is the right trade here, because a wrong bit boundary
does not raise an error, it returns a plausible wrong tensor. The board reported
`24 hit, 2 miss`: exactly one miss per class, which is the cost of learning.

**The measurement design mattered as much as the fix.** Both decode paths are in
one binary behind a runtime flag and run at every rate in the same boot, because
[M5b's own entry](#2026-07-30--the-tuned-baseline-and-a-28-error-we-nearly-shipped)
warns that ratios quoted across builds of this firmware are not measurements. And
the decode was made a pure function of a byte buffer in a Pico-free file, so
`test_gemm_wire.c` could check it on the laptop at every bit offset, on all six
command codes, and on five distinct failure modes — before a strap was spent. It
passed on the first run, which is not evidence of anything, so four deliberate
mutations were injected (drop the high half of the funnel shift; mask the CRC
index to `0x7f`; corrupt one CFG field; misalign the payload copy) and all four
were caught. *One strap covered the whole milestone.*

**And the comment that was wrong.** `gemm_host.c:74-78` argued a CRC table was
not worth building "beside the 16 K link clocks the same payload spends on the
wire." True per byte on the wire; false in elapsed time, because the CPU and the
wire never overlap — we are the FPGA's only clock, so the tile is frozen for the
entire decode. Cost of that reasoning: 11 ms a block, which is most of the gap
between M6's 53 ms and this milestone's 42 ms baseline column.

The wire is now the largest single phase (5.47 of 12.08 ms) for the first time in
the project. That is the correct thing to be bottlenecked by, and it makes the
next levers — requantising in fabric, overlapping the strip build with the DMA —
choosable by evidence instead of by guess.

### 2026-07-30 — the tile is bit-exact on silicon, and it moved the bottleneck

**M6c: 2,048 of 2,048 int32 accumulators bit-exact, at every link rate from 38
to 75 MHz.** One real conv2 block, run on the T8, compared against
`fgx_conv_acc()` computed on the MCU in the same boot — no tolerance, no
sampling. Status `0x61` at all six rates: no underrun, no bad frame, no sticky
fault. Full results in
[M6c](milestones.md#m6c--on-board-2048-of-2048-at-every-rate).

**The result that matters is the one we were not looking for.** The block moves
50,980 bytes and takes 53 ms; at 75 MHz and one bit per clock that is 5.44 ms of
wire. **The link is idle 90% of the time and the MCU-side driver is the
bottleneck** — 0.92 MB/s measured against 8.94 MB/s of measured wire. Extrapolate
the per-frame blocking through that driver and a frame costs ~3,900 ms, which is
*slower than the 3,358 ms MCU baseline M6 exists to beat*. The tile is not the
problem and the wire is not the problem; `find_preamble()` scanning 66,000-bit
capture buffers from offset 0 is. That is a fixable, structural mistake — the MCU
drives the clock, so the response offset is a constant that can be measured once
at init — but it had to be measured to be believed, and no amount of simulation
would have surfaced it. Analysis in
[The 90% that is not the link](milestones.md#the-90-that-is-not-the-link).

**Three procedural things paid for themselves**, and all three are worth keeping.

*The strap was spent once, for the whole milestone.* `fpga_configure()` takes a
plain pointer, so `m6.c` receives the 173 KB bitstream over USB CDC into SRAM
instead of having it compiled in. Reflashing the MCU costs a physical `PRG`–`GND`
strap; reconfiguring the FPGA does not. Every RTL revision after the first — and
the entire six-point clock sweep — cost **zero straps**. On this board that is
the single biggest lever on iteration speed, and it is why the sweep happened at
all rather than being replaced by one measurement and an argument.

*The simulator and the board were made to run the same layout code.* The strip
and weight layout used to live inside `gen_gemm_vec.c`, which writes the vectors
`tb_gemm` and `tb_gemm_link` check the RTL against. Transcribing it into `m6.c`
would have put an unverified second copy on the hardware path — and a strip bug
there presents as "0 of 2048 accumulators match", which localises nothing. It
was pulled out into [`firmware/gemm_block.c`](../firmware/gemm_block.c) and both
callers now link it. The regenerated vectors were **byte-identical** and both
testbenches still PASS, so the refactor is provably inert and those two PASSes
are now evidence about the code the MCU actually runs.

*The padding buffer was poisoned rather than zeroed.* Strip rows outside the
image are a don't-care — a correct tile never reads them — and that is exactly
why filling them with zero is wrong: it makes a stray read of a pad row return
the right answer. Filled with `0xa5` instead. Mutating the row-bounds test in
`im2col_feed.v` was caught by **2 of 6 cases against a zero-filled strip and by
all 6 against a poisoned one.** A don't-care that is cheap to make loud should
be made loud.

**One caveat recorded so it is not misread later.** 75 MHz is 15% past the
64.973 MHz the static timing model predicts, which says something about C2-corner
conservatism — but the sweep found **no failure edge**. 75 MHz is the ceiling
`m6.c` can generate (sys_clk 150 MHz ÷ 2 in the PIO), not a measured limit of the
fabric. The honest statement is "correct everywhere we could reach", not "correct
up to 75 MHz". sys_clk above 150 MHz is unexplored, and free if it works.

### 2026-07-30 — the tuned baseline, and a 28% error we nearly shipped

**M5b: 3,357.6 ms/frame, 3.17 cycles/MAC, still 2048/2048 bit-exact.** im2col
plus a blocked int8 GEMM with an `SMLAD` inner loop, 7.4× the reference kernel,
flat at 7.2–7.9× across every conv shape. M6 now has an honest number to beat.
Full results in
[M5b](milestones.md#m5b--tuned-mcu-baseline--3358-msframe-bit-exact-74-the-reference).

**Two things went right for procedural reasons rather than lucky ones**, and
both are worth keeping.

*The strap was spent last, not first.* Reflashing this board needs a physical
`PRG`–`GND` strap, and the one thing that could not be tested on macOS was the
`SMLAD` path — aarch64 does not define `__ARM_FEATURE_DSP`. So
[`dsp_shim.h`](../firmware/dsp_shim.h) transcribes the four intrinsics from the
Armv8-M ARM and `cc -DFGX_DSP_SHIM` compiles *the same source lines* the M33
runs. The tap pairing, the loop bounds and the `K % 4` tail were all proven
against numpy on the laptop, per layer, before the board was touched. What was
left for the strap was "does the silicon match the ARM ARM", and it did — first
try, no second strap.

*The reference was re-run in the same boot rather than quoted.* This one nearly
cost us. `encoder.c` runs at **24,970 ms** in the M5b binary against the
**31,798 ms** M5 logged — same source, same clock, same flags. The cause is that
M5b had to drop `static` from `fgx_conv` to call it from the harness, which
stopped GCC inlining it into `fgx_run`; a 1,086-byte monolith became a 636-byte
hot kernel, and on a part that fetches instructions from flash XIP that was
worth 21% with **no change to a single arithmetic operation**.

Dividing 31,798 by the new figure would have produced "9.5×" — a 28%
overstatement, in our own favour, from an arithmetic shortcut that would have
looked completely reasonable in review. The true figure is 7.4×. **On this board,
ratios quoted across builds are not measurements**, and the only defence is to
pay the ~25 s to re-run the baseline inside the same boot.

One consequence beyond the number: the MCU baseline is 3.36 s, not the ~1.7 s
CMSIS-NN estimate the tier table was built on, so the FPGA's honest multiple is
~15× rather than ~7×. **The argument for M6 got stronger by being made
honestly** — which is not the direction that correction usually runs, and is the
reason to keep making it this way.

### 2026-07-30 — the second board was never broken, and it settles the PSRAM

Two findings, and the first is what made the second possible.

**Board #1 works.** It had been written off as dead on 2026-07-29 — no USB
enumeration across two cables and two 4-minute hotplug watches — and a repair
plan had been drawn up around SWD on J2, a pogo cable we do not own, and a
suspect list of the 1V1 buck, `RUN`, the crystal, and the USB ESD array.
Plugged **directly into the Mac rather than through the hub**, it enumerated
instantly as `2E8A:0009` serial `118E1FFA149C9E95`, and its factory loader
answered `forge-loader rp2350 ready`, state IDLE. The whole suspect list was
imaginary.

That is worth more than a board. The 2026-07-29 entry recorded two cable swaps
as evidence, and two cable swaps *feel* like independent trials — but every
attempt went through the same hub, so the variable that mattered never moved. It
was one experiment run twice. A negative result across N retries is only worth
N if the retries differ in the way that counts, and the cheapest way to find the
untested constant is to ask what every failed attempt had in common. Here it was
sitting in the sentence "across two cables".

**And with two boards, the PSRAM question resolves.** The rev 4 probe on board
#1 returns a *different* byte string that decodes identically:

```
                raw record                MFID KGD  EID
board #2   5e 0c 03 57 46 f6 9c 06   ->   0D   5D   1b da 70 19 78 30
board #1   95 17 43 57 46 f6 9c 06   ->   0D   5D   1b da 70 1a 54 5d
                                                    └ common ┘└serial┘
```

Two APS1604M dies with sequential serials, both healthy, both 2 MiB, both
answering correctly — and both landing **exactly 18 bit-times out of frame**.
Each record has precisely one rotation out of 64 that yields the `0D 5D` header,
and on both boards that rotation is 18. So the offset is not a bad part; rev 4's
path × chip matrix already showed it is not our driver either, since
`flash_do_cmd_cs()` and `raw_xfer()` both frame CS0 at bit 8 and both slip +18 on
CS1.

Chip cleared, wire cleared (the quad capture puts the data on SD1), opcode
cleared (dead opcodes return nothing), host cleared, datasheet says `9Fh` takes
no dummy cycles. **Nothing left is reachable from the MCU** — `RXDELAY` moves the
sampling point inside a bit and dummy settings move whole bytes, so no register
addresses an 18-bit shift. Open question #10 closes as bounded: the next
instrument is a scope at the package, and PSRAM was always headroom rather than
a dependency. Two units reproducing it is also what turns this from a warranty
claim into something worth sending Adiuvo.

**Reported to `support@adiuvoengineering.com` on 2026-07-30.** The headline ask
is the cheap one — *has U1 ever been brought up successfully on a Forgix board?*
If the answer is "we route it but never validated it", that closes this outright.
Secondary questions: whether CS1 needs an init step the forge-loader performs,
and whether the U1 stub off the RP2354A's shared QSPI pads is known-good at
speed. No replacement requested; two dies with the same offset is a design
question, not a warranty one.

#### Vendor reply, 2026-07-30 — **#10 closed: U1 was never meant to be there**

Adam Taylor (founder, Adiuvo Engineering) answered the same day, and the
headline ask landed:

> "The PSRAM was not intended to be fitted to the boards in the production run,
> but they were accidentally assembled as such we left them fitted but have done
> no testing of them as the RP2354A does not need the external PSRAM for
> operation."

**So there was never a working configuration to find.** U1 is an accidental
population — unvalidated, never brought up, no known-good `psram_detect_size()`
exists on any Forgix board. Question 1 is answered completely, and question 3
with it: he confirms nothing in the schematic or layout would produce the delay,
and is himself "at a loss as to why the signal would arrive back 18 clock times
later than expected". Question 2 (a CS1 init step the loader performs) went
unanswered, which no longer matters. He offered to investigate on his return
from the US after 8 August, and offered a refund.

**Refund declined, investigation not requested.** The boards do everything this
project needs — the weights live in the 2 MB stacked flash, M5 and M5b both ran
from XIP, and [the bandwidth analysis](milestones.md#m3--memory-bandwidth--answered-as-a-side-effect-of-m5)
shows PSRAM would have added capacity and not speed. Asking a founder to spend
bench time on an unvalidated part we have no use for would be spending his time
to satisfy our curiosity.

**The 18 bits remain genuinely unexplained**, and that is now the permanent
state of this question rather than a to-do. Worth being precise about what was
and was not established: everything reachable from the MCU was eliminated
rigorously, and the vendor has confirmed there is nothing in the board design to
find. What was never done is the one measurement that could actually answer it —
a scope at the package. Nobody knows why it is 18. That is an acceptable place
to leave it, but it is not the same as knowing.

*The retrospective value is in the ratio.* Four probe revisions, a
photograph that overturned a documented "DNP", and a full 2×2 host-exoneration
matrix — all spent on a component that was **fitted by accident on a board that
does not need it**. Every step was locally justified, and the whole was
disproportionate. The one question that would have capped the effort at zero was
the one sent last: *has this ever worked for you?* **Ask the vendor before
out-debugging the vendor** — especially about a peripheral whose only
advertisement is a product page.

### 2026-07-30 — M5 is bit-exact on silicon, and the PSRAM stays a mystery

Two results from one `PRG` strap, and they point in opposite directions.

**The good one: 2048 / 2048 bit-identical float32 outputs, on the device.** The
Cortex-M33 running `encoder.c` produced embeddings that `memcmp` equal to the
numpy int8 golden vectors — every bit of every one of 512 floats, on all four
test images. `lrintf`'s round-half-to-even, the FPU's rounding mode, and the
int32 accumulators all agree with the host. **The integer contract M6 has to
reproduce is now pinned on the target silicon**, and a cosine of 1.000000 would
not have shown that: a small systematic scale error also produces cosine 1.0.

**The bad one: `psram_detect_size()` returned 0**, and the hour spent explaining
that is the part of this entry worth reading, because the explanation was wrong.

The search for a reason found `dnp exclude_from_pos_files exclude_from_bom` on
U1 in the vendor `.kicad_pcb` — the same trio as `U8`, which is the *Teensy
form-factor outline*, a part that does not exist — and U1 absent from
`build/positions.csv`. Since JLCPCB places from the CPL, that read as conclusive:
**U1 is not populated.** The README, `docs/pinmap.md` and `m5.c` were all edited
to say so, and it was committed.

Then a photograph of the underside took five seconds to disprove it. **U1 is
soldered on**, a SOIC-8 just past the Tag-Connect pads. So is **J3**, which
carries the same `dnp` flags. The premise was false: *the exclusion flags in this
repo do not describe the manufactured board.* And `positions.csv` is generated
from those flags, so it was never a second source — it was the same claim wearing
a different hat, which is exactly what made two agreeing files feel like
corroboration.

The first answer to [#1](history.md#verify-before-building) (populated, from a BOM row)
was right for a poor reason. The second (not populated, from CAD metadata) was
wrong for a reason that felt much better, which is the more expensive failure
mode. Two real-world signals — the vendor product page and the press coverage —
were pointing the right way the whole time and were argued down. *"Is this part
on the board"* is a question about an object; the photograph should have been
step one.

What actually remains is [#10](history.md#verify-before-building): a populated, correctly
wired APS1604M that will not return an ID. **M5c** is the probe for it — print
the raw bytes the SDK throws away — and it ran the same evening. **It turns out
U1 was never failing to return an ID. It was returning the correct one, in a
place we were not looking.** `00 00 00 00 5e 0c 03 57 46 f6 9c 06` carries
`0D 5D 1B DA 70` at bit offset 50: AP Memory, known good die, 2 MiB. The part on
the BOM, the density on the BOM, answering correctly. What is wrong is that the
reply arrives 18 bit-times late, so every byte-aligned read of it cuts a good
answer in half. Details in
[M5c](milestones.md#m5c--make-u1-talk--closed-the-vendor-never-fitted-u1-on-purpose-and-never-tested-it).

There are two lessons inside that, both variants of the big one. The first: M5c
rev 1 printed the bytes *and then printed its own verdict*, `U1 SILENT on every
variant` — because its classifier only recognized an exact `0D 5D` and treated
everything else as absence. The raw row and the summary line disagreed, and the
summary was the wrong one. A diagnostic that interprets is more useful than one
that dumps, right up until its interpretation is narrower than reality; then it
launders a third outcome into one of the two it was built to expect. The bytes
were on screen the whole time.

The second is sharper, because it cost two more revisions. Both rev 1 and rev 2
searched for `0D 5D` **on byte boundaries** — and byte alignment was not a
property of the data, it was an assumption the probe inherited from the SDK it
was written to debug. The one thing a diagnostic must not import from the system
under test is the system's own framing. Rev 2's real contribution was not its
timing matrix, which found nothing; it was reading twelve bytes where rev 1 read
eight, which is the only reason bit 50 was inside the window at all.

**Tier 3 is unaffected either way, because it never depended on the PSRAM.**
1.42 MB of int8 weights + a 173 KB T8 bitstream + ~60 KB of firmware is 1.65 MB
of the RP2354A's 2 MB stacked flash, and M5's per-layer table shows the flash
fetch is nowhere near binding: weight bytes per MAC vary 64× across conv1–conv7
while cost per MAC stays flat at ~195 ms/MMAC. U1 would sit behind the same QMI,
so it was never going to be faster — it is 2 MB of *writeable* headroom, which
matters for growing the model past the flash budget and not much else.
[M3](milestones.md#m3--memory-bandwidth--answered-as-a-side-effect-of-m5) is dissolved into
this finding rather than run.

**The latency number needs a caveat louder than the number.** 31.8 s/frame, or
30 cycles/MAC — that is the cost of an inner loop that re-tests a flag and
bounds-checks two axes on every tap. `encoder.c` was written to be obviously
correct and the bit-exact row is what that bought. Quoting 31.8 s as "the MCU
baseline" would make the FPGA look like a 140× win. Hence **M5b**, blocking M6:
the tuned kernel gets `encoder.c` as its golden reference for free, and its
im2col decomposition is the same one M6 has to build in RTL.

*Resolved the same day:* M5b measured **3,358 ms/frame**, still bit-exact,
7.4× the same-boot reference. The FPGA's honest multiple is **~15×**, not 140×
and not the ~8–10× this section assumed — the tuned MCU came in 2× slower than
the ~1.7 s CMSIS-NN estimate the tier table was built on, so the argument for
M6 got *stronger* by being made honestly.

Also worth recording: the graceful-degradation path in `m5.c` earned its keep.
It was written on the assumption that a dead PSRAM was *unlikely*, and it is the
only reason a strap spent on a board whose PSRAM stayed silent still came back
with the correctness result and the full per-layer profile.

### 2026-07-30 — both gates pass: the link is real and so is the student

**M2 measured: 8.94 MB/s each way, zero errors at every operating point.** But
getting there took finding out why nothing we built would configure.

**The bug was clocks, not bits.** `fpga_configure()` released `CRESET_N`, waited
`sleep_us(100)`, and started sending. The T8 needs to be *clocked* during that
window before it will start matching the sync pattern, and an idle SPI master
emits no clock — so the part got time and zero edges. Every bitstream from
`rtl/build.sh` had been arriving ~2048 clocks too early since the day the script
was written.

**What made it invisible for two sessions** is that the vendor's `plasm_led.hex`
configured perfectly, first try, every time. Efinity normally prepends a 256-byte
ASCII banner (`Version:`, `Generated:`, …) to a `.hex`, and `rtl/build.sh` passes
`generate_header=off` to strip it — reasoning, in a comment I wrote, that it was
a banner the programmer discards and our firmware "would happily shift into the
FPGA". Exactly backwards. AN 006 Figure 15 draws the CDI0 waveform as
`Header, D, D, D, …`: that banner **is** the lead-in clocking, and the vendor
image only worked because it still carried one.

**The bisect that caught it** was putting the byte-identical vendor payload on
the ladder twice — once whole, once with its first 256 bytes removed. Whole:
CDONE high in 73 µs. Stripped: never. Same bytes, same device, same driver. That
is the entire finding, and it took one flash. My standing hypothesis until that
moment was that reusing configuration pins (F3/CCK, F2/CDI0, G3/SS_N, A4/NSTATUS)
was upsetting the config engine; the `probe_a`/`probe_b`/`probe_c` ladder was
built to bisect *which pin*, and it refuted the whole idea instead — `probe_a`
reuses nothing and failed too. Pin reuse was never the problem.

**Two lessons worth more than the fix.** First, a control that passes is only
informative if you also test the *minimal difference* from it — "vendor works,
ours doesn't" and "vendor works, vendor-minus-header doesn't" cost the same flash
and only the second one names a cause. Second, when every flash costs a physical
`PRG` strap, batch the whole matrix: `firmware/diag.c` walks six rungs without
stopping on success, and the bring-up firmware sweeps lead-in sizes ascending
from zero, which is how the **measured minimum of 32 bytes / 256 clocks** came
for free alongside the fix.

The fix lives in `fpga_config.c` (`LEADIN_BYTES` = 256, 8× margin) rather than in
the build script, so configuration no longer depends on a bitstream-generation
flag. Two vestigial things went with it: the `FPGA_ERR_NSTATUS` timeout loop,
which pin-probing showed was waiting on a line that is externally driven high and
never dips, and the single immediate `CDONE` read, which could not tell "failed"
from "needed another microsecond".

**Then the link swept clean on the first try** — all six operating points, 0
errors, up to 75 MHz. The correlator's alignment offset walking 8 → 9 → 10 as the
rate climbed is exactly the behaviour predicted in the M2 section, which is a
small vindication of building the offset search instead of asserting a latency.

**M4 also came in, and it is a clear GO:** 1.40 M params retain **94%** of the
queries CLIP ViT-B/16 clears (30 of 32), against a 60% threshold. The result that
matters most for M5 is that **simulated int8 is free** — identical mean AUC to
fp32 at three decimals. The result that deserves suspicion is `person` and
`chair`, where the student *beats* the teacher; those are the two near-chance
queries, and the student is fitting dataset bias, not out-reasoning CLIP. Noted
in the M4 section so nobody later reads them as headroom.

So both GO/NO-GO gates are behind us. What is *not* settled is whether 8.94 MB/s
(or 26.8 with the jumper) makes the FPGA worth using at all — M2 proved the link
is clean, not that it is fast enough. That is still M6/M7's question.

### 2026-07-29 — two boards, one alive ~~one dead~~ (corrected 2026-07-30)

| | Board #1 | Board #2 |
|---|---|---|
| USB enumeration | `2E8A:0009` "Pico", serial `118E1FFA149C9E95` | `2E8A:0009` "Pico", serial `4A7C7EFE9A15CFD6` |
| 3V3 rail | present (meter) | — |
| Loader responds | yes, `forge-loader rp2350 ready`, state IDLE | yes, `forge-loader rp2350 ready` |
| FPGA configured | — | **yes**, CDONE + nSTATUS high |

**Both boards work.** Board #1 was written off here as dead — no enumeration
across two cables and two 4-minute hotplug watches, with 3V3 confirmed present —
and a suspect list was drawn up around it: the `1V1` rail (RP2350 internal buck
via L1), `RUN` held low, the 12 MHz crystal Y1, the USB data path (R4/R5 27 Ω
series, U3 USBLC6 ESD array). All of it was wrong. Plugged **directly into the
Mac instead of through the USB hub**, board #1 enumerates immediately and its
factory loader answers HELLO. Every suspect above is exonerated; nothing was
ever wrong with the board.

The lesson is narrower than "check your cables", because the cables *were*
checked: two of them, twice. What went unchallenged was the hub, and the hub was
the one element of the path shared by every failed attempt. Swapping the cable
twice felt like two independent trials and was really one trial run twice — the
variable that mattered was never moved. A negative result across N retries only
buys you something if the retries differ in the way that counts.

Note the board has **no power LED**; D1 is FPGA-driven. A dark board proves
nothing about power. That is what made the hub theory so easy to skip past:
there was no cheap signal distinguishing "not powered" from "not enumerating",
so the investigation jumped straight to the rails.

**Board #2 reached M1.** Streamed the vendor `plasm_led.hex` several times, always
ending CDONE = 1 / nSTATUS = 1, plus the four control loads in the table above.
A power cycle clears the configuration, as expected for SRAM.

**Known issue: the loader can wedge.** Once, after a successful load and a
close/reopen of the CDC port, the firmware stopped servicing USB — the device
stayed enumerated but host writes *and* `close()` blocked indefinitely.
`protocol_send_response()` uses `putchar_raw()`/`stdio_flush()`, which can stall
on a stale `stdio_usb` connection. With no reset button, recovery is a USB
unplug/replug.

**The trigger is not pinned down.** After the replug, six consecutive
open/load/close cycles ran clean, so it is intermittent rather than a
deterministic consequence of reopening the port. Mitigations now in
`host/forge.py`: a settle delay after opening, a `write_timeout`, and an
`ABORT`-first retry in `probe.py`. Note `write_timeout` does **not** rescue a
blocked `close()` — if it recurs, expect to unplug.

### 2026-07-29 — one jumper triples the forward link

Went to write M2's loopback as a 1-bit link and stopped at the pin table. The
constraint that produced "1 bit" was real — GPIO1–3 is the only contiguous run —
but the step from there to "so the link is 1 bit" quietly assumed the clock had
to sit inside that run. It does not. PIO keeps `out_base`, `in_base` and
`sideset_base` in three separate registers; contiguity binds only *within* a
group. Put the side-set clock on any pin at all and GPIO1/2/3 are three data
bits.

The pin to put it on is not arbitrary. Cross-referencing the T8F49 ball list
against the header shows four global-clock balls — B3, C3, E4, E6 — and exactly
one of them, **B3 (`GPIOL_16_CLK2`)**, is wired to a header pad (PIN17). The
other three are unconnected on this board, and F3 `CCK`, which configuration A
uses for the clock, is not clock-capable at all. So a single jumper from pad
PIN2 (RP GPIO22) to pad PIN17 buys both halves of the improvement at once: the
clock lands on the global network *and* it vacates GPIO1–3.

Forward ceiling goes from 8.9 to 26.8 MB/s. The return path stays at 1 bit and
always will — GPIO5 is `CDONE` and GPIO7 has no pad, so GPIO6 has no neighbour to
be contiguous with. That asymmetry is now the binding constraint on the dataflow,
and it is the right shape for an accelerator anyway: feed it a lot, ask it for a
little.

Built both configurations from one parameterized `link_core`. Two things about
the testbench are worth recording:

- **A "must fail at 125 MHz" check passed, and it was the check that was
  wrong.** With a correlator searching sample offsets, overclocking a
  source-synchronous link does not corrupt data — it slides the alignment, and
  the correlator finds the new offset. Errors only appear when the sample instant
  lands inside the return line's transition window, which depends on real
  `T_co`. Simulation cannot answer "how fast" honestly. Deleted the check and
  replaced it with a negative control that *can* fail: short a data line straight
  to the return line, bypassing the fabric. The deliberate inversion in
  `link_core` turns that into ~1985/4096 errors, so a solder bridge cannot
  masquerade as a passing link.
- The heartbeat check measured 1 edge in 2 ms and looked broken. A 488 Hz signal
  needs a much longer window than a link test does; the property under test is
  scale-invariant, so the testbench scales the dividers down rather than
  simulating 40 ms.

M1b folded into M2 rather than staying a separate milestone: the link test has to
repurpose GPIO1/2/3 the moment `CDONE` rises, and the vendor loader owns those
pins as an external service. `firmware/fpga_config.c` does passive x1 SPI config
from an embedded bitstream and then hands the pins over.

Both configurations compile clean and the RTL simulates clean. What is missing is
the bitstream — Efinity is not installed, and it sits behind an Efinix account
login. The firmware handles this deliberately: `hex2c.py` emits an empty
placeholder, and `main.c` prints the failure and stops rather than running a
sweep that would measure nothing.

### 2026-07-29 — Efinity, and both bitstreams

Registered an Efinix account, got the free Bronze licence (T8 covered, valid to
2027-07-28), pulled Efinity 2026.1.132 for Linux and containerized it. Both M2
configurations now place, route and generate a bitstream on T8F49C2.

Getting a *headless* flow working took longer than the synthesis. Four things,
none of them in the docs:

1. `efx_run.py` does not need a project `.xml` — `--family Trion -d T8F49
   --timing_model C2 -v <sources>` covers it. Yesterday I declined to hand-write
   that XML on the grounds that guessing at a version-specific schema would
   mislead. That was right, but for the wrong reason: the file is not needed.
2. It *does* need a `.peri.xml`, and nothing shipped can make one from scratch.
   `efx_run_pt_import_isf.py` merges an ISF into an existing design; a project
   never opened in the GUI has no existing design. `DesignAPI.create()` is the
   missing call, so `rtl/mk_peri.py` is fifteen lines that unblock the whole
   flow. Without it place-and-route runs with no pin assignments at all, after a
   single-line warning that scrolls past.
3. Constraint files are found by filename. `link.sdc` was silently ignored — and
   an ignored SDC does not fail, it defaults every clock to a 1 ns period, so the
   first timing report showed −5.7 ns slack and looked like a real problem.
   `build.sh` now stages it as `<top>.sdc`.
4. Efinity puts an ASCII banner *inside* the bitstream by default. Its own
   programmer strips it; our firmware would have shifted `Version: 2026.1.132`
   into the FPGA. `generate_header=off`.

The numbers, and what they are not. Configuration A costs 34 FFs / 22 adders /
5 LUT4s; C costs 38/22/11 — half a percent of 7,384 LEs. Fabric Fmax is 365 MHz
(A) and 228 MHz (C) on `link_clk`. **That is not a link rate.** The SDC has no
`set_input_delay`/`set_output_delay`, because those need the RP2354A's PIO
clock-to-out, which the FPGA toolchain cannot know; the analysis covers internal
paths only. What it does establish is that the fabric is nowhere near binding,
which is the useful fact for M6.

Two smaller findings:

- **C is slower in the fabric than A** (228 vs 365 MHz) because XOR-reducing
  three lines adds a LUT level ahead of the shift register. Irrelevant at PIO
  speeds, but it is the first concrete instance of the width-vs-depth trade that
  M6 will live inside.
- **The jumper buys almost nothing in clock quality.** On B3, a real GCLK ball,
  pad-to-global-buffer routing is 2.64 ns; on F3, which is not clock-capable, it
  is 3.99 ns, and both pay the same 3.32 ns through the buffer. So B3 is 1.35 ns
  better — not a different class of routing. Yesterday's argument for the jumper
  had two halves, "a real clock ball" and "a third data bit"; only the second
  half survives contact with the router. It is still worth doing, for that
  reason alone.

`mode=passive` and `mode=active` produce byte-identical bitstreams on this
device — checked, rather than assumed, because the firmware's whole configuration
path depends on the distinction. Passing `mode=passive` anyway, as documentation.

Firmware now links with the real 173,124-byte image: 417 KB `.uf2` against 2 MB
of flash. Everything that can be done away from the board is done.

### 2026-07-29 — netlist, and the 8-bit bus dies

`kicad-cli` is not installed and the vendor ships no netlist, so
[`tools/kicad_netlist.py`](../tools/kicad_netlist.py) recovers connectivity
geometrically from the `.kicad_sch` files: union wire endpoints, attach labels
and power symbols to the points they sit on, project each symbol's library pins
through its placement transform. It reproduces all eleven RP GPIO assignments
already known from the PDF, which is what makes the rest of its output usable.

Two parser bugs were worth the time to fix, because both would have produced
confident wrong answers rather than obvious failures:

- **Multi-unit symbols.** KiCad places each unit of a resistor pack separately.
  Merging all four units onto one instance put three quarters of the pins at
  fabricated coordinates and invented nets that do not exist.
- **Power symbols.** `power:+3V3` is referenced through a local `lib_name`
  override, so the naive lookup missed its pin and every rail read as an unnamed
  net — which made a 10 kΩ pull-up indistinguishable from a pull-down. That
  distinction is exactly what tells you `SS_N` is strapped to passive mode.

Findings, in descending order of how much they hurt:

1. **Only 6 header pins reach the RP**, in three isolated pairs, and 13 GPIO are
   unbonded. The widest contiguous RP↔FPGA run is **3 bits** (GPIO1–3, the config
   SPI pins reused after `DONE`). The 8-bit parallel dataplane is not buildable.
2. Bank spread on the FPGA side is a non-issue — 18 header pins, 7 of them in
   bank 2A.
3. `CRESET_N` and `SS_N` have 10 kΩ **pull-downs**: the board is hard-strapped to
   passive SPI configuration. That retroactively explains why the vendor's
   `active (x1)` bitstream configured fine.
4. The PSRAM is populated — **confirmed by photograph 2026-07-30**, after a day
   spent wrongly retracting it on the strength of the layout's `dnp` flag. Treat
   `dnp` and `positions.csv` in this repo as **not evidence either way**: `J3`
   carries the same flags and is also fitted. U1 is nonetheless silent on the
   bus, which is [#10](history.md#verify-before-building). The SWD pads need no connector,
   and header pad 18 is `QSPI_SS` — a probable BOOTSEL escape hatch.
5. Two earlier claims corrected: SW1 is on **G6 (`CSO`)**, not E4/`CBSEL1`; and
   the schematic independently confirms the vendor `.isf` has red and blue
   swapped (E1 = R, G1 = B).
