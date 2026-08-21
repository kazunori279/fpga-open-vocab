<!-- moved out of README.md on 2026-08-01; see ../README.md#documentation -->

# Bring-up log

Dated entries, newest first — what was straps, what was measured, and what was
got wrong on the way. **Append-only**, and deliberately so: several entries here
exist only to record a claim that later turned out to be false.

[← back to the README](../README.md) · [architecture](architecture.md) ·
[building](building.md) · [history](history.md) · [dev plan](milestones.md)

---

### 2026-08-22, later — a second draw, and two sweeps that measured nothing

The entry below ends with a generated set demoted to a teacher-side screen. The
obvious next use for one was as a **distillation regression harness**: same
pixels, several checkpoints, one row each, no camera and no board. That is now
implemented — `probe_bisect.py --runs a,b,c`, which refuses to mix runs
distilled against different teachers because their query vectors live in
different spaces — and the first thing it was pointed at says not to trust it.

Two sweeps on the screened crop sets. Five sieve settings (ViT-B/16: baseline,
InfoNCE at 0.3 and 1.0, RKD at 10, and both) and three SO400M ones (30k
baseline, RKD 10, RKD 100). The student rows span 0.0–0.4 sd on the book pair
and 0.3–0.8 sd on the glass pair, and `rkd-10` looked like a winner at 16 of 18
on the book pair — against a baseline of 12.

**Then the same checkpoint was measured on a second draw of each pair**, thirty
more val2017 scenes the first sets never touched (`synth_pairs.py --skip`).
Nothing about the model changed. The glass pair went 0.9 sd → 0.3 sd and its
class-axis cosine +0.263 → +0.183; the book pair 0.6 → 0.7 sd and +0.141 →
+0.058. **The draw-to-draw spread is wider than the entire spread across eight
distillation settings**, and `rkd-10`'s 16 of 18 does not survive into the glass
set, where it reads 18 of 25 against a baseline of 17. Not one of those
settings has been shown to differ from baseline.

Adding scenes does not fix this. The variance is *between* draws of scenes, not
within one — which is the same fact as the entry below, arriving from the other
direction. The sign count itself is stable to about ±0.05, so a generated set
still screens the teacher, and the teacher replicated exactly: **23 of 23**
after screening on unseen glass scenes, luma cue at AUC 0.617, with 30 of 30
before screening. Second confirmation that SigLIP binds fill state.

Three smaller things. `--skip` exhausted COCO: after the first 27 `book`
sources, val2017 has fourteen boxes left at 90 px, so there is no third book
draw at this size. `--runs` first rendered `_sieve_infonce-0.3` and
`_sieve_infonce-0.3+rkd-10` as the same truncated label, which is a table that
lies rather than a table that is hard to read; it now strips the shared prefix.
And a judge finished its thirty sheets and remarked that the sides looked
perfectly alternating — they were, by index. It had not used that, but a screen
that *can* be answered without looking fails silently when it is, so the side is
now a hash of the filename.

Sets in `bench/stills/20260822-synth-{book,glass}-crop2/`, sweeps archived as
`sweep-so400m.log` and `sweep-sieve.log` beside the first draws, and
`tools/synth_keep.py` now folds the two judges' verdicts into `keep.txt` so the
criterion lives in code instead of in a README.

---

### 2026-08-22 — a pair can be generated, screened by machine, and still not rank

The idea was cheap and looked sound: edit val2017 photographs into both states
of a contrast, and screen a pair before anyone buys the props for it. Six sets
later it works, for a smaller question than the one it was built for.

**Both sides are generated**, or the pair separates on photograph-against-render
and scores beautifully having measured nothing. **Sources are val2017**, held
out of the distillation. Neither of those was the hard part.

The hard part was that **the generator obeys the state clause and quietly
ignores everything else.** Whole COCO photographs are rooms, so the book came
back a few dozen pixels across and the teacher read 1.4 sd — a framing artefact,
not a limit. Asking for a close-up instead moved it to 3.0 sd and swapped that
fault for a worse one: on a third to a half of the pairs the generator had
*re-composed* the shot. A different room. The book replaced by a green wheelie
bin. A pint tumbler become a stemmed wine glass. None of it visible in the
margins, all of it fatal to a within-scene statistic.

Cropping the source around a COCO instance box before editing fixes both at
once, and grants the generator no licence: the object already fills the frame,
so the instruction can insist that nothing move. Scene survival went from 58% to
89%.

**Then screen every pair blind.** Two judges, no encoder output, each pair shown
as A|B with the sides alternating, asked *which side holds the positive state* —
a question with a wrong answer, unlike "is this pair any good". They agreed with
each other on 26 of 27 and 30 of 30, and the filter did what a filter should: the
teacher went from 26/27 to **18 of 18** on the book pair and stands at **25 of
25** on the glass pair. Screening the stimulus before encoding is a validity
filter; screening after seeing the margins would be fitting, and only the order
separates them.

**And the student still reads the two pairs in the wrong order.** 12 of 18, 0.6
sd on the book pair the appliance carries at 8.2 sd; 19 of 25, 0.9 sd on the
glass pair the appliance loses at 0.2 sd. Upside down, after screening, at n =
18 and n = 25. It is not the images and it is not the sample. Eighteen books on
eighteen desks asks for a *scene-independent* state axis; a bench asks about one
book on one desk across frames. A 1.4 M-parameter student holds the second and
not the first, so **a generated set cannot rank candidate pairs for the
appliance** — the ranking it produces is not the appliance's.

What it can do is the gate that was already the important one:
`bench/stills/README.md` has said since 08-21 that nothing downstream recovers
what the teacher never had. A generated, screened set is a propless
**teacher-side** screen, minutes per pair, and that is its whole remit.

One caveat closed on the way. [#28] was shut with a doubt attached: on real
stills the mean frame luma separates tea from empty at AUC 1.000, so the
teacher's perfect score there might have been a photometer. On the generated
glass set the luma cue is at **AUC 0.658** and the teacher is still 25 of 25,
with both judges asked specifically whether the amber ran past the rim. SigLIP
binds fill state. It is not reading the lamp.

Sets in `bench/stills/20260822-synth-*`, tools in `tools/synth_pairs.py` and
`tools/synth_sheet.py`, and the subset each set was read on in its `keep.txt`
with the dropped pairs listed and the criterion each failed.

[#28]: https://github.com/kazunori279/fpga-open-vocab/issues/28

---

### 2026-08-21, 10:35 — the glass axis is lost at the student, and only at the student

Still newest-first: the entry below calls itself *midday* and its runs are
stamped 07:01. The label is wrong and the order here is right.

Stills, logs and the two bisection runs in
[`bench/stills/20260821-bisect/`](../bench/stills/20260821-bisect/), which has
the long version. Tool is `tools/probe_bisect.py`.

Four glass benches — margin AUC 0.699 / 0.591 / 0.680 / 0.674, one of them with
the sign flipped, fenced by book controls at 96.7% and 98.3% on the same desk —
established that `a glass with tea` / `an empty glass` fails and that the
staging is not why. [#24](https://github.com/kazunori279/fpga-open-vocab/issues/24)
asks *where*: teacher, projection, student, or int4. Four stages, four different
fixes, three of them expensive.

**It is the student.** Effect sizes on 66 glass stills and 22 book stills, all
of them 128×128 PNGs off the appliance's own camera:

| pair | teacher 1152 | pca 512 | student fp32 |
| --- | --- | --- | --- |
| `an opened book` / `a closed book` | 26.0 sd | 24.1 sd | **8.2 sd** |
| `a glass with tea` / `an empty glass` | 7.9 sd | 5.4 sd | **0.2 sd** |

Two candidates die on the teacher row alone. **It is not the resolution** — the
teacher read 7.9 sd on the very same 128×128 pixels, so "the frame does not
resolve the fill state" comes off #23's list. **It is not the projection**, so
neither is #24's own "cheapest possible fix": refitting the 1152→512 basis on a
bank with fill-state contrasts cannot recover what the basis already passes
through at 5.4 sd.

Three things had to be got right before that table meant anything.

**Effect sizes, not AUC.** The first run of this measured AUC only: teacher
class 1.000, teacher drift-null 0.983. Both saturate, and two 1.000s that mean
opposite things are not a measurement.

**The board's `z`, not a raw cosine gap.** `m9.c` scores
`z = (cos − background)/std` per query. The background cancels out of any
ranking; the std does not, and using `cos_A − cos_B` hands the vote to whichever
query swings more. Fixing this moved the student's glass row from 0.784 to
0.533.

**And the book control, which is the reason the student row is readable at all.**
A student reading 0.2 sd might just as well mean the measurement is broken. Same
session, same script, same stage: 8.2 sd on the book pair. It is not broken. The
glass 0.2 sd is 0.8 sd *below* that pair's own round-to-round drift.

Rounds, alternating, are the other half of that: eleven tea, eleven empty,
eleven tea, and so on, because thirty consecutive frames of one scene confound
the class with the AEC and the daylight — the confound that made the four
benches unreadable in the first place. All eight capture runs printed `exposure
settled after 6–8 frames` and no `scene:` line, so #25's and #26's guards, one
day old, had nothing to say about any of these frames.

#### Two things this does not show, both bigger than the table

**The difference is not gone from the student.** A fitted axis held out by round
reads AUC 1.000 at every stage including the student. The student's embedding
does move between the two scenes; what it does not do is move along the
direction the text query points at. Cosine between the student's class-mean
difference and the teacher's is **+0.031**, against **+0.158** for the book
pair — both small, because the student's geometry is its own, and the ratio is
the signal.

**And that oracle is not evidence of a bound concept**, because mean frame luma
separates the glass pair at AUC 1.000 by itself (108 against 133 — tea is
darker). It separates the book pair too. The oracle rules out *the student threw
the frames away*. It does not rule in *the student knows what tea is*.

So the question moves from capacity to distillation. **Nothing here supports
"the model is too small"**, which is the reading four failed benches invited and
the reason it was worth spending a morning to not act on.

int4, #24's fourth row, is deliberately not run: the fp32 student already loses
the axis, so quantising cannot change the verdict.

#### A number that was measured, believed for ten minutes, and thrown out

Per-frame `cos(student, teacher)` on these stills is 0.475, which reads like a
collapse. `bench/cue` frames from runs where the board scored 100% read
**0.428**, and a *constant* vector scores 0.957 / 0.841 on the same two sets;
`config.json` has `constant_cosine` 0.643 against `best_cosine` 0.672. The
quantity is dominated by the shared cone direction and is not about any axis. A
difference of class means cancels the cone; a per-frame cosine does not. It is
out of the tool's output, and the reasoning is a comment in
`tools/probe_bisect.py` because it will look convincing again.

---

### 2026-08-21, midday — the acquire's doubt outlives the banner, and a settle that was never a settle

Logs in [`bench/soak/20260821-q26/`](../bench/soak/20260821-q26/).

[#26](https://github.com/kazunori279/fpga-open-vocab/issues/26) is the same
shape as #25 above and was found by the same reading. `ft_acquire()` had already
diagnosed the 08-20 dark room correctly — a ramp stuck on the floor, `mean RGB 7
0 7`, an exposure that never moved — and printed all three warnings into a
nine-line banner that `usb_soak.sh` does not echo. **Twelve runs, roughly 2,400
frames, then scored the black picture anyway.** Nothing was broken except how
far the warning reached.

While instrumenting it, the line those runs would have had to re-read turned out
to be lying:

```
camera    : live 128x128 RGB565, ... exposure settled after 41 frames
```

The loop is `for (; warm < 40; warm++)`, so `warm + 1` is the captured count
only when something in the body broke out. When the **bound** ends it, `warm` is
already 40 and the frame count was one too high — and the word `settled` was
exactly backwards, on every stuck run this firmware has ever produced. It now
distinguishes the two exits and counts `nramp = warm < 40 ? warm + 1 : 40`:

```
camera    : live 128x128 RGB565, ... EXPOSURE NEVER SETTLED after 40 frames
```

Forty numbers in the ramp, forty in the count. There is a third warning branch
now too, for a ramp still climbing when the bound stopped it: not dark and not
stuck, but the background gets measured off a mid-ramp frame that the frames
after it will not match.

**The carrier is `ft_acquire_doubt()`** — NULL, or a short phrase naming the
doubt, valid until the next acquire. `m9` prints it in `stopped :`, where the
log is still being read, and `usb_soak.sh` greps for it beside #25's
`enrolment:` and #9's `lastwords:`. That grep is anchored to the summary's
twelve-space indent, because an unanchored `scene: ` also matches
`ft_acquire()`'s own "tuned camera on a neutral scene:" note.

**It still does not refuse**, and the argument for that is already on the record
at `FLOOR` in `frame.c`: a genuinely dark room whose correct exposure is the cold
reading is a legitimate scene, and refusing would be firmware deciding it knows
the lighting better than the person standing in it. What changed is that the
doubt now survives the frame it was about.

**The bound-exhausted log in that directory is from a crippled build**, and is
named `never-settled-forced.log` for it. The room was lit; the image had the
convergence test disabled so the loop could not exit early. There was no way to
stage a dark room to order, and the alternative was shipping a branch nobody had
watched fire. Its doubt phrase is wrong for that scene — the exposure had in
fact settled at 126 — which is the cost of forcing the path and is written at the
top of the log's README rather than left to be rediscovered.

---

### 2026-08-21, morning — the level axis, asked about before the run rather than after it

Logs in [`bench/soak/20260821-q25/`](../bench/soak/20260821-q25/).

[#25](https://github.com/kazunori279/fpga-open-vocab/issues/25) had cost two
benches and the comment describing it had been sitting directly above three
guards that cannot detect it. They all read `qref[]`, and the degeneracy is a
property of `qvec[]`: two contrast queries built from the same two phrases in the
opposite order are **bitwise negatives**, because `host/demo.py` sends
`normalize(e_pos - mean(e_neg))` and swapping the phrases negates every
component.

The check is one pass over the Gram matrix at query load. Every frame's `lvl` is
`mean_i z_i`, and `z_i` is affine in `cos_i = <qvec[i], f>`, so the only
frame-dependent part of that mean is `<m, f>` with `m = mean_i(qvec[i]/qsd[i])`.
**If `m` is zero, `lvl` is a constant** — which is the whole failure, stated as
one number that can be printed before a frame is captured.

```
queries   : 2 accepted, 512-d, crc ok
            level axis carries 0.00 of one query's swing; most opposed pair -1.000
            'an opened book~' AND 'a closed book~' ARE EXACT NEGATIVES, ...
```

against the healthy control, same board, four minutes later:

```
            level axis carries 0.95 of one query's swing; most opposed pair +0.810
```

**The line prints on every set, not only the bad ones.** The failure this is
about produced a log that looked completely normal, and a line that only appears
when something is wrong cannot be used to confirm that nothing is.

**Two numbers, one bar, and the bar is not a measurement.** The axis figure is
continuous and is reported without a verdict — this repo has twice had a
continuous statistic about an enrolment that turned out to be wrong in both
directions, and `FGX_ENROL_SNR` was deleted for it. What is judged is the pair
cosine, which in the failure case is exactly −1; `FGX_Q_ANTIPODAL = -0.999f` is
room for the host's float32 round trip and nothing else. There is deliberately
no "how opposed is too opposed" constant.

**Reported, not refused.** The scores a degenerate set produces are not wrong,
they are narrower than they look: the 08-20 14:22 bench asked a margin question
and its AUC is valid, so refusing would have thrown away a real measurement. The
warning names what is void — every presence and level number — and says it again
in the `stopped :` summary, because the banner carrying it scrolled past 546
frames ago on the run that made this worth writing.

---

### 2026-08-21, early morning — the flash record survives the power cycle, and the camera has two faults rather than one

Logs and harness in
[`bench/soak/20260821-lastwords/`](../bench/soak/20260821-lastwords/).

**[#9](https://github.com/kazunori279/fpga-open-vocab/issues/9)'s item 2 is
finished, including the line the entry below could not verify.** `lw_write()`
now has run live from the frame loop, with core 1 up and the lockout held, and —
the case the whole thing exists for — a record written during an outage was read
back after VBUS had been cut:

```
reset     : chip_reset 00010000  (scratch was cold: ...)
            new this boot: power-on reset - the supply arrived
lastwords : Written on the first re-attach attempt, at frame 28, 176.530 s into that run.
            The bus had been gone 2001 ms by then, since frame 22; ...
            The scratch did NOT survive, so the always-on domain went away between
            that record and this banner: the outage ended in a power cycle.
```

No `usb :` line and no watchdog tag, because the always-on domain is exactly what
went away — and the outage is named anyway. That is the thing #9 could not do.

**The first attempt at that test failed for a host-side reason worth writing
down.** `bootsel.py --power-cycle --run` boots the board *twice*: the first boot
read the record, printed the banner and erased the sector before `demo.py` ever
raised DTR, and `stdio_usb` discards everything written before a host asserts it.
Enumeration is not a reader. One `uhubctl -l 2-1 -p 2 -a cycle -d 3` and nothing
else gives exactly one boot, and the test passed unchanged.

**The camera's two faults are separate, and on any given day only one of them is
present.** The 05:57 `cam_probe` matrix matches 2026-08-03 exactly — rows 1 and 3
constant, rows 0/2/4 pictures — which is the *redundant-write* fault: a second
identical FORMAT/RESOLUTION write blanks the next frame. The 08-20 state, where
rows 0/2/4 are constant too and only a ≥300 ms untriggered stretch produces a
frame, was gone. Nothing was done to the module in between.

So [#27](https://github.com/kazunori279/fpga-open-vocab/issues/27) is not
answered: **the settle threshold cannot be measured on a day the settle fault is
absent**, and the sweep duly returned 3/3 at every value from 0 to 400 ms. The
sweep stays in `cam_probe.c` as the instrument, to be re-run when the matrix
looks like 08-20 again.

**Its first control was wrong in both directions and is worth recording as a
mistake.** It ran a three-row preflight expecting picture / picture / CONSTANT.
Row 2 used `rewrite = true`, so it was itself a second identical register write
and read CONSTANT by reproducing *the other fault*. Row 3 expected CONSTANT from
a reset sensor at settle 0, which only holds when the settle fault is present —
the very thing being asked — so on a clean day it printed *"cam_begin() does NOT
un-start it"* about a `cam_begin()` that was fine. The premise check now writes
no registers at all (`rewrite = false`), and the control is the descending pass:
it opens at 400 ms, which works, so a reset that does not un-start the sensor
makes every row below it work too and `down` disagrees visibly with `up`.

---

### 2026-08-20, night — last words in flash, and 2 KB of heap that no allocator could reach

[#9](https://github.com/kazunori279/fpga-open-vocab/issues/9)'s second owed item
is the one the watchdog scratch cannot do. The scratch registers live in the
always-on domain, and cutting VBUS is both the only known recovery from #9 and
exactly what takes that domain away — so **an outage that ends in `uhubctl` has
been unattributable by construction**. `firmware/lastwords.{c,h}` puts one
48-byte record in the last flash sector instead, sixteen slots to a sector, read
and cleared at the next banner by `lw_report_last()`.

The costs are all in *when* rather than *whether*. An erase is ~50 ms with XIP
down, so it happens once at boot, inside `lw_take()`, before `w1_start()` — no
second core to lock out, because there is not one yet. The outage path only
programs one page, and does it behind `multicore_lockout_start_timeout_us(2000)`
rather than betting that every core-1 job body stays out of flash. That lockout
is why `w1_main()` now opens with `multicore_lockout_victim_init()`: the call has
to run *on core 1*, and that function is the only thing that does.

**The link failed by 1052 bytes, and the fix is a fact rather than a tuning
knob.** `flash_range_erase()` and `flash_range_program()` cannot execute from
flash while they are taking XIP down, so the SDK marks them `.time_critical` and
they land in SRAM. m9 had 484 bytes of RAM left. What paid for it is
`PICO_HEAP_SIZE=0`: `arm-none-eabi-nm forgix_m9.elf` matches no `malloc`, no
`calloc`, no `free` and no `_sbrk`, so the SDK's default 2 KB reservation was
memory nothing in this image could ever hand out. The alternative — moving hot
buffers between SRAM banks with `__scratch_x`, where 4 KB is genuinely idle —
was rejected on purpose: `.heap` sits after every other RAM section, so zeroing
it leaves the address of every buffer exactly where it was, and the bench numbers
in this repo were measured with that layout. Freeing RAM by shuffling the hot
path would have quietly put all of them in question.

**Verified across a real power cycle, except for the one line that writes.**
Two synthetic records were programmed straight into `0x101ff000` with
`picotool load -o`, and the next banner picked the higher `seq` of the two,
decoded every field, and printed *"The scratch did NOT survive … the outage
ended in a power cycle. This is the case issue #9 could not attribute before."*
A `uhubctl` cycle after it came up silent, which is the erase. A page that
starts to look like a record and stops — what a torn program leaves — was
reported as *"bytes that are not a record"* and erased rather than read as a
fact. So the scan, the CRC, the `seq` pick, the report, the power-cycle branch
and the erase-when-dirty are all seen to work on the board.

`lw_write()` itself is not, and the reason is the camera. Driving it needs an
outage, an outage needs `usb_watch()`, and `usb_watch()` only runs inside a
frame loop that `ft_acquire()` refuses to start:

```
camera    : exposure ramp 5 5 5 5 5 5 ... (40 of them)
camera    : still a constant fill (08 01) after 41 frames
RESULT : FAIL - no camera.
```

**That refusal is correct and the third reproduction tonight.** `08 01` is the
exact constant the ArduChip FIFO returns when no frame has been written, which
is not what a dark room gives, and a fresh `cam_probe` at 19:30 splits the same
way the 18:40 one did: rows 0, 2 and 4 — every recipe without a settle — return
`c80a8564`, and rows 5 (`settle300`) and 6 (`everything`) return a picture. The
sensor is alive; the recipe `ft_acquire()` uses is the one that does not work
right now. That is [#27](https://github.com/kazunori279/fpga-open-vocab/issues/27),
and no number is being fitted to it here.

### 2026-08-20, evening — the first soak with a bus-side witness, and the room went dark in the middle of it

Twenty `m9` runs of 200 frames at 280/140, 18:04 to 18:31, with
`host/usb_watch.py` recording every hub port throughout. That watcher is the
whole point: it is the fourth owed item on
[#9](https://github.com/kazunori279/fpga-open-vocab/issues/9) and the first
bus-side record any soak in this repo has had. Archive and detail in
[`bench/soak/20260820-usb-p2/`](../bench/soak/20260820-usb-p2/); the harness is
the new `bench/soak/usb_soak.sh`, which names neither the CDC device nor the
port and looks the port up once while the board is still on the bus.

**No outage, and one long negative result.** Runs 1-7 each closed with
`usb: 0 outages, 0 ms off the bus, 0 re-attaches`. Better, the middle of the
window is an accident worth more than the runs: **18:17:35 to 18:32:19, fourteen
minutes and forty-four seconds, not one port transition**, while the board's own
frame counter ran **0 → 2608 without a reboot**. The next transition after it is
a VBUS cycle of mine, identifiable because `2-2:2` — the USB3 twin of the same
physical socket — moves in the same sample.

Seven clean runs against an event that showed up once in eight is not power
enough to say the new port and cable were the participant. What changed is that
the next recurrence is attributable.

**#9's owed item 1 was asking for something already true.** It read "a different
hub, or the board straight into a Mac port". The board has been straight into a
Mac port the whole time; there is no external hub here and never was. `2-1` is
the Mac mini M4's *internal* two-port USB2 hub (`05ac:800b`) fronting the two
front-panel USB-C ports, `2-2` is its USB3 twin, and the three rear Thunderbolt
ports are separate controllers with no per-port power switching — moving there
would cost both the only known recovery and the only instrument.
`host/bootsel.py:33` said all of that already. What was actually changed tonight
is the port within that hub (`2-1:1` → `2-1:2`) and the cable.

**Runs 8-20 are void, and the reason is not the bus.** The room got dark at
about 18:19. `ft_acquire()`'s exposure ramp read `4 5 4 4 4 ...` for all forty
frames, reported `exposure settled after 41 frames` — which is the loop running
out of its bound, not converging — and handed back a frame with mean RGB 7 0 7.
The run started anyway and scored 200 frames at `led 0/255`. Eleven more runs
followed on the same never-rebooted board.

The gap is narrow and specific: after the ramp, `ft_acquire()` refuses only a
wrong FIFO length and an *exactly* constant frame. A dark scene is neither — it
has sensor noise. `FLOOR` and `rose` decide when to stop waiting and are then
discarded, so the run proceeds on precisely the frame the convergence test
rejected. The downstream per-frame guard was fine and did fire, but only later,
once the room was dark enough for frames to come back exactly constant. Filed as
[#26](https://github.com/kazunori279/fpga-open-vocab/issues/26); `frame.c:1500`
argues on the record for reporting rather than refusing, and that argument now
has a twelve-bench bill attached to it.

**The camera scare, and what it actually was.** After the soak m9 would not
bring the camera up at all — `still a constant fill (08 01) after 41 frames`,
twice, across a reflash and a full VBUS cycle. `forgix_cam_probe` cleared the
hardware completely: sensor id `0x82` agreeing between bit-bang and PIO at every
rate from 0.5 to 16 MHz, thirteen image controls all returning sensible means,
and a real picture out of the closing f128.

But the probe failed **its own recorded matrix**, and printed so. Rows 0, 2 and
4 came back as the `08 01` constant where 2026-08-03 recorded pictures. Rows 2
and 4 write no registers at all, and neither do rows 5 and 6 — so four rows of
byte-identical bus traffic split on nothing but `sleep_ms(300)`. That is not the
08-03 redundant-write fault, and it is not the dark room either: `08 01` is an
exact fill, which is what the FIFO returns when no frame has been written, and a
dark room returns noise. What it looks like is the sensor needing a contiguous
stretch of not being triggered after `cam_begin()`, with each trigger restarting
it — which would explain why m9's forty iterations of capture-plus-50-ms never
get there while one 300 ms sleep does. Filed as
[#27](https://github.com/kazunori279/fpga-open-vocab/issues/27). The threshold
between 50 and 300 ms has not been measured and no fix should be sized until it
is.

It is intermittent: the next boot ramped `5 5 32 54 53 55 56` and settled in
seven frames. The board is back on `forgix_m9` and working.

---

### 2026-08-20, host — a stale RP2350 mount held Finder dead for eight days, and `diskutil unmount force` is not the way out

[building.md](building.md#flashing-the-mcu) has said since 2026-08-15 that
rebooting out of BOOTSEL with the volume mounted leaves the mount on
`diskarbitrationd`'s `danglingVolumeList` and that touching it hangs in the
kernel. That is the prevention, and it was right. What the page did not have is
**what to do once it has already happened**, and the answer turns out to be the
opposite of the obvious one.

**The bill.** Finder did not run on the Mac mini from **2026-08-12 12:53 to
2026-08-20 16:01** — eight days. Nothing else on the host was affected, which is
why it went unnoticed for that long, and which is also why the whole 08-14 →
08-20 bench series was collected on a host in this state.

**The chain, as observed.**

1. Something rebooted the board out of BOOTSEL while `/Volumes/RP2350` was
   mounted. There is no commit and no bench log from 08-12, so which reboot it
   was is not in the record.
2. Finder touched the stale mount and blocked in the kernel.
3. A `SIGTERM` (a `killall Finder`, presumably) started its exit and could not
   finish it. It sat at `PID 8915 PPID 1 STAT ?E` — *exiting* — for eight days.
4. **`launchd` read that as alive.** `launchctl print gui/501/com.apple.Finder`
   gave `state = running, pid = 8915, runs = 2, last terminating signal =
   Terminated: 15, exit timeout = 5`. The five-second exit timeout was exceeded
   without bound and launchd still refused to start a replacement, because by
   its bookkeeping one was already up. **That, and not the crash, is the
   symptom** — Finder was not crashing on launch; it was never being launched.
5. The file-system server behind the mount ran away in the meantime:
   `com.apple.fskit.msdos.appex`, 67% CPU, 56 minutes of CPU accumulated.

**What worked, and what did not.**

| step | result |
| --- | --- |
| `diskutil unmount force /Volumes/RP2350` | **hung** — it blocks on the same kernel path, and had to be killed (exit 137) |
| `kill -9` the runaway `com.apple.fskit.msdos.appex` | **this is the fix.** The mount was released with it |
| `kill -9` the `?E` Finder | unnecessary — it was reaped the moment the kernel block cleared |
| `launchctl kickstart -k gui/501/com.apple.Finder` | unnecessary — launchd started it by itself once the zombie was reaped, `runs` 2 → 3 |

So the rule is: **kill the file-system server, not the mount.** `diskutil` is a
client of the wedged path and cannot unwedge it.

**Where it does not show up.** No Finder crash report. No Finder log entries for
three days — the silence itself was the confirmation the process was already
dead rather than looping. Disk had 566 Gi free and memory was not tight, so
nothing resource-shaped pointed at it either. The only two signals were the `?E`
process state and the fskit appex burning CPU.

**One thing this opens, and it is a question and not a finding.** `picotool
load`'s silent no-op — progress bar to 100%, flash left at `0xff`, twice in one
session — was measured on **2026-08-15**, inside this window, and so were the
three faults recorded on that date. `picotool` writes over PICOBOOT and not over
the mass-storage interface, so there is no mechanism on the table; but a wedged
`fskit` server does hold macOS's claim on the other interface of the same
device, and every observation of the no-op was made with it wedged. **It has
never been reproduced on a healthy host, because nobody knew there was an
unhealthy one.** Worth watching for now that the host is clean — if the no-op
never comes back, that is the answer.

**Follow-through, and it is done.** The board was found still sitting in BOOTSEL
after the recovery — `/dev/disk4s1 RP2350` enumerated, no `/dev/cu.usbmodem` for
VID `2e8a`, `/Volumes` holding only `Macintosh HD`. That is the *safe* half of
the state, and the reason to act on it rather than leave it: the volume was not
mounted, so getting out of BOOTSEL right then could not repeat the fault,
whereas the next thing to mount that volume would have set the trap again.

`uv run host/bootsel.py --flash firmware/build-280/forgix_m9.uf2` did it. Its
log is worth keeping, because every branch it took was one of the failure modes
this page documents:

```
no CDC port for the board at all
falling back to a power cycle
  power-cycling hub 2-1 port 1
  back at /dev/cu.usbmodem21101, nudging again
BOOTSEL up at /Volumes/RP2350
flashing forgix_m9.uf2 (2180608 B) ...
  wrote in 15.4 s, verified
back up at /dev/cu.usbmodem21101
```

15.4 s is a real write and not the 2.5 s no-op, and `verify` agreed. Afterwards:
`/Volumes` clean, **`RP2350` gone from `diskutil list` entirely**, the CDC port
back, and the `fskit.msdos` appex idle at 0.06 s of CPU. The host and the board
are both out of it.

### 2026-08-20, firmware — the preinit park cannot work on this platform, and the map says so in two lines

The entry below this one ends with an open question: the `FGX_QSPI_PARK_PREINIT=1`
image bricked the board, and it was not yet known whether the preinit slot was
the cause or merely the thing that changed. It was the cause, the mechanism is
static and provable without the board, and the flag is gone.

**The boot test.** Same tree, same 280 MHz, same bitstream, one cache variable
apart. `=1`: flashed, `power` with no `connect`, no enumeration, recovered on the
strap. `=0`: flashed, banner, 22 frames, 22 good, 324 ms/frame, 0 USB outages,
core 1.25 V. That narrows it to the registration and nothing else — but it is
still only a differential, so the map was read next.

**The mechanism.** On `rp2350-arm-s` the SDK compiles `gpio_init()`,
`gpio_put()` and `gpio_set_dir()` into GPIO **coprocessor** instructions;
`fgx_qspi_park()` disassembles to `mcrr 0, 4, r3, r2, cr0` and
`mcrr 0, 4, r3, r2, cr4`. That coprocessor is disabled out of reset and is
enabled by `runtime_init_per_core_enable_coprocessors()`, which — being per-core
— is emitted into `.preinit_array.ZZZZZ.00200`. The array is linked with
`SORT_BY_NAME`, and `"Z"` sorts after every digit. In the map of the image that
bricked the board:

```
0x1000f774  __pre_init_fgx_qspi_park                             (00601)
0x1000f798  __pre_init_runtime_init_per_core_enable_coprocessors (ZZZZZ.00200)
```

Nine entries too early. An `mcrr` to a disabled coprocessor is a NOCP
UsageFault, taken before `stdio_init_all()`, which is why no VBUS cycle helped:
the fault is in flash, so every boot repeats it. **The general form is worth
keeping: no numeric preinit priority on this platform can call `hardware_gpio` at
all.** Not `"00601"`, not any other number.

**Why not just move it later.** A per-core slot after `ZZZZZ.00200` would be
legal, but it would also land after `runtime_init_setup_psram()` (`"11080"`,
numeric, therefore earlier), so on `forgix_m5` and `forgix_psram_probe` it would
take the pin straight back off the QMI — and it would run a second time on core 1.
Hand-writing the SIO and `PADS_BANK0` stores would dodge the coprocessor and also
stop the file being the byte-for-byte sequence with 15,008 clean frames behind
it, which is the only property it has.

**What #17 actually gets.** The goal was right — one target parking the pin left
`m2`, `m5b`, `m6`, `m7`, `m8`, `cam_probe` and `diag` exposed to #9 — and it is
met without a hook: all eight non-PSRAM targets now link `qspi_park.c` and call
`fgx_qspi_park()` as the first statement of `main()`. Eight identical lines is
worse than zero and is what this platform costs. `nm` over the ten `.elf`s:
`__pre_init_fgx_qspi_park` absent everywhere, `fgx_qspi_park` present in eight
and absent from `forgix_m5` and `forgix_psram_probe`, which want the part.

**Verified on the board, not just in `nm`.** `forgix_diag` was flashed first,
deliberately — it is one of the seven targets that never had the park — and it
enumerated and ran the whole config ladder to the vendor-`plasm_led` rung.
`forgix_m9` then rebuilt byte-identical to the proven `=0` image
(`fba19a99…`), was flashed back, and returned 21 frames, 21 good, 324 ms/frame,
0 USB outages.

**One cost worth writing down.** Rebuilding meant deleting `firmware/build-280`,
and the fresh configure failed on `cannot find -lg / -lc` — Homebrew's
`arm-none-eabi-gcc`, exactly the trap `building.md` already documents. The
working tree had `PICO_TOOLCHAIN_PATH` in its cache and the command history did
not. Deleting a build directory deletes the configuration too; it is cheaper to
reconfigure in place.

---

### 2026-08-20, firmware — #9 caught in the act and cleared in one command, and the fix for it has never booted

Two things about the same GPIO, on the same morning, pointing opposite ways.

**#9, live, and the whole signature in four commands.** Before any of today's
firmware work, `picotool info` on the board — which was sitting in BOOTSEL after
the four benches — reported `Program Information: none`. Three `picotool save`
reads of the same 4 KB at `0x10000000` came back as three different SHA-256s.
Then `uhubctl -l 2-1 -p 1 -a cycle`, and the board enumerated as a CDC device,
`picotool info` named `forgix_m9`, and `picotool verify` against the very image
that had just read as noise passed clean at 100%.

That is the third recorded instance and the first taken deliberately rather than
in the middle of losing a bench. Nothing was ever corrupted, only removing the
5 V clears it, and the reason is at the top of `firmware/qspi_park.c`: GPIO0's
pad comes out of reset with its pull-down enabled, that pin is U1's chip select,
and the PSRAM therefore sits selected on the flash's bus for the whole run.
Worth writing down that the *diagnosis* now costs about ninety seconds.

**And the fix for it has never run on this board.** `cef3d3b`, on 08-17, moved
the three-line park out of m9's `main()` into the SDK's preinit array at
priority `"00601"`, on all ten targets, so that no future target could forget
it. The placement is correct — the link map shows the entry in
`.preinit_array.00601`, right after `PICO_RUNTIME_INIT_POST_CLOCK_RESETS`
releases the pad banks — and the commit message says all ten targets build and
all ten carry the symbol. They do. **None of them had been flashed.** The only
image ever linked against the hook was `build-320`, built one minute before the
commit and never loaded; `nm` on `build-280`, the image the appliance has
actually been running since 08-16 and which produced every bench including this
morning's four, finds no `fgx_qspi_park` at all.

So `build-280` was rebuilt from current sources — the first 280 MHz image to
carry the hook — and flashed. **The board did not enumerate.** `uhubctl` showed
`power` with no `connect`, through two VBUS cycles and a twelve-second
power-off; no CDC, no BOOTSEL, nothing on `/dev/cu.usbmodem*`. It came back on a
PRG–GND strap.

A wedge before USB exists is the one failure this firmware is arranged to
prevent. `main()` brings USB up *before* the clock for exactly that reason, and
the comment above `stdio_init_all()` says so in six lines: the 2026-08-15 wedge
came from a one-line build flag and cost a strap, and finding the next one must
not cost the board. A pre-`main()` hook is outside that protection by
construction — there is no window in which anything can be told to enter
BOOTSEL.

**What is not the suspect: the three GPIO writes.** They are byte-for-byte the
sequence that ran at the top of `main()` for 15,008 clean frames on 08-16, and
that is also before USB. What is new is the slot. Against that, the rebuild
honestly moved more than one thing — `m9.c` took four commits of enrolment
changes after the 08-16 image was built — so the flash above is evidence and not
proof.

`FGX_QSPI_PARK_PREINIT` is therefore a CMake cache variable now, the same
pattern and for the same reason as `FGX_SYS_KHZ` and `FGX_CORE_MV`: two images
that differ in exactly one thing. `=1` is the hook as committed and stays the
default, because what is in the tree should be what was reviewed. `=0` leaves
the function unregistered and m9 calls it from the top of `main()`, which is the
placement with the frames behind it. Both are built and kept outside the tree,
next to the known-good 08-16 image, and the next flash after the strap decides
it. Nine other targets carry the same hook and none of them has booted either,
so nothing should be flashed from this tree until that is known.

Re-opened #17. The goal it was closed on — integration is listing a file, and
there is nothing to remember — is still the right goal; it just cannot be paid
for with a hook that runs before the board can be recovered.

---

### 2026-08-20, tooling — `lost` was subtracting two different populations, and one bench changed sides

Follow-on from the bench entry below, which found the defect and deferred the
fix. `tools/probe_ceiling.py`'s `lost` column is the one its own docstring calls
"the column to read": `best - state`, the points the decision rule failed to
collect. But `best` was an oracle over all ~240 cued frames and `state` was the
rule over the ~120 that survive dropping the enrolment visits and the frames
before the rule engaged. Two populations, subtracted.

It showed, and had been showing. 08-20 06:37 printed **-2.9**, and the 08-17
11:26 and 15:42 rows had carried -4.2 and -1.2 in the recorded table for three
days. A negative reads as the rule beating the margin ceiling, which cannot
happen: in a one-dimensional centred space nearest-reference *is* a threshold on
the margin, sitting at the midpoint of the two references, so it is one of the
cuts the oracle already tried. Nobody had queried it because the docstring said
`lost` was "slightly generous to the ceiling" — which is the opposite of what a
negative means, and reading it as a small known bias is exactly how a wrong sign
survives.

There is now a `held` column: the same oracle, over exactly the frames `state`
is scored on. `lost` is `held - state`, `state <= held` is asserted on every
bench rather than argued, and it came out non-negative on all twenty-two.
`best` stays, because the ceiling is a property of the two scenes and holding
frames out of it would be answering a different question; where `best` and
`held` differ a lot, the taught half of the run was easier or harder than the
held-out half, which is worth seeing.

**#19 is still four benches and they are not quite the same four.** 08-17 09:33
fell from 18.6 to 14.2 and is out; 08-17 15:27, the first glass run, rose from
7.5 to 15.0 and is in. The three large ones are unchanged in substance — 08-17
08:55 at 71.7, 13:35 at 36.7, 08-16 17:22 at 29.2. The new member sits *exactly*
on the line and should not be leaned on, which is the second thing this fix
turned up: the test was `lost > 0.15` on raw floats, and 0.75 − 0.60 evaluates
to 0.15000000000000002, so that bench qualified by binary representation. The
comparison is now made on the printed figure in points and the constant is named
`LOST_19`, so the prose and the table cannot disagree; it is `>=` deliberately,
and the docstring says the run is on the line rather than pretending it cleared
it.

`docs/architecture.md` and `bench/README.md` carried the old figures and are
corrected in place. 15:37's scatter cost 10.8 points and not 2.1; 13:35's drift
cost 36.7 and not 36.3.

---

### 2026-08-20, bench — the book control on both sides of the glass, and a display that kept a dead session's labels

Four benches at 280 MHz in thirteen minutes, deliberately interleaved
book / glass / glass / book so that the pair that works brackets the pair that
does not. Same desk, same daylight, same board, same image, same operator, same
enrolment schedule, four minutes apart.

    time      pair    HELD OUT /120    |sep|    ceiling   state    lost
    06:24:06  book     116/120 96.7%   1.000     100.0%   95.0%     5.0
    06:29:32  glass     61/120 50.8%   0.591!     61.7%   51.7%     5.0
    06:33:24  glass     58/120 48.3%   0.680      67.9%   49.2%    10.8
    06:37:04  book     118/120 98.3%   0.950      94.6%   97.5%     1.7

**The glass failure is a property of the pair, and the "that run was bad"
explanation is now spent.** It was always the weakest thing said about glass and
it survived only because no glass run had ever been fenced. Two book controls
either side, thirteen minutes apart, both at ceiling — there is no window left
for the desk, the light, the clock, the staging or the operator to have been the
difference. Whatever is wrong was wrong for five minutes in the middle and
nowhere else, which no environmental cause can do.

**Nor can a decision rule fix it.** Measured on the same held-out frames the
rule sees, the best any threshold on the margin could reach is 56.7% and 60.0%,
against a chance of 50.0%. So glass is #23 and not #19; #19 costs 5.0 and 10.8
points on top of a ceiling that was already almost nothing. A pair whose ceiling
is 60% does not have a rule problem.

**The new fact is that the glass axis does not keep its sign.** The two runs read
AUC 0.409 and 0.680 on the same phrase order, the same glass, the same tea, four
minutes apart — inverted in one and not in the other. Inverted is not absent, and
a backwards axis is repairable by naming it the other way round; an axis that
changes direction between runs is not an axis, and there is nothing to name. That
rules out the phrase-swap repair for glass specifically, and it is not something
the book pair has ever done.

**The staging is not it, and the tool's verdict line says otherwise.** All four
archived frames of the 06:33 run and two of the 06:29 run were rendered and
looked at: the same glass in the same place at the same angle, tea a dark amber
full of ice and empty plainly empty, obvious at 128x128 and consistent across
visits. `probe_ceiling.py` reads the within-visit / across-visit gap
(0.867 against 0.680) as "the staging moved". Here it did not move. What is left
is drift in the embedding between visits minutes apart, which is #22's question
and not a staging failure — the verdict line is honest about the number and
wrong about the cause, and should be read as one.

**`probe_ceiling.py`'s `lost` column compares two different frame sets.** `best`
is computed over all 240 cued frames; `state` is the nearest-reference rule on
the ~120 held out after the enrolment visits are dropped. So `lost = best -
state` is not the quantity its own docstring describes, and on the 06:37 book
run it printed **-2.9** — which reads as the rule beating the margin ceiling, an
impossibility in a 1-D centred space and exactly the kind of number that gets
believed. Recomputed on the same frames it is 5.0 / 5.0 / 10.8 / 1.7, never
negative, and the ceiling argument is unharmed. The 06:33 glass run's "lost 19
points, #19" verdict was inflated by the mismatch; the real figure is 10.8. The
column is left as it is for now and fixed under its own commit, because the
eighteen-bench table in that file's docstring has to be regenerated with it.

**#18's gate went on buying nothing.** Held 0/90 on both book runs and on the
first glass run, 49/90 on the second. On both book runs the empty desk was called
present on every single frame and named `an opened book` — a pair that separates
its two states at 96.7% and 98.3% and still cannot tell that neither of them is
there. The worst-case gap is negative on all four (-1.27, -2.19, -2.04, -1.15):
the two populations overlap, so no single pair of edges separates them. Four more
benches saying the same thing.

**The first attempt was thrown away because `host/cue.py` showed a dead
session's bars.** The board was still looping the previous glass run when the
book bench opened, so its frame lines arrived first and `Bars` was built with the
names `an empty glass~` / `a glass with tea~`. `demo.py` then sent `R`, the frame
counter went backwards, and the reset branch dropped `scores`, `segments`,
`pending` and `open_seg` — but not `bars`. After the reboot the board scored the
book queries, `smooth()` filed them under their own keys, and `shares()` went on
reading `self.names`, which still said glass. The rows showed the last glass EMA,
frozen, for the whole run: 97.3% / 2.7% at z +-1.79, identical thirty-three
frames apart, while the board's own `background:` line named the books correctly.
The measurement would have been fine — `scores` was clean and the cues fired on
schedule — but a display that keeps a dead session's labels and never moves is
worse than no display, because it reads as a confident measurement. The reset now
drops `bars` and `enrol_lines` too, and any change of the score key set rebuilds
the block regardless.

---

### 2026-08-17, tooling — a probe that never ran on the version it advertises, and a lint set that was nobody's

Board-free work, and one of the two things found is not cosmetic.

**`tools/probe_rule.py` was a `SyntaxError` on Python 3.11, which its own PEP 723
header declares as the floor.** The held-out percentage line put a line break
inside an f-string replacement field — legal from 3.12 (PEP 701), a parse error
before it. So on 3.11 the file did not run at all: not a wrong number, no number.
It went unseen because `uv run` picks the newest interpreter it can, and this Mac
has 3.13. The arithmetic is now a named `held_out_pct()` outside the f-string,
and the output is byte-identical on `m9_cue-20260817-073335.log`. Every `.py` in
the repo now parses under 3.10, checked rather than assumed.

The general lesson is that `requires-python` is a claim like any other and this
repo had never tested it. There is no CI running the tools, so nothing would
have caught this except somebody with an older interpreter — i.e. nobody, until
the day it matters.

**`ruff` was being run with no configuration, which meant the rule set was
whichever version `uvx` fetched.** It had already drifted: fifteen
`# noqa: E402` comments were flagged as unused because E402 is no longer on by
default, and `--fix` deleted them — along with the sentences beside them saying
*why* an import sits below a `sys.path.insert`. A cleanup that removes
documentation is not a cleanup. `pyproject.toml` now pins `[tool.ruff.lint]`
explicitly, with each omission argued in place: E402 and E731 are out because
nearly every script here is a single PEP 723 file that inserts `model/` or
`tools/` on the path first (sixty-four sites, all the same shape, all correct),
PLR is out because 86 `magic-value-comparison` hits on `if len(parts) == 3` is
not a finding, and PLC0415 is out because the deferred `import torch` is what
keeps `--help` working without it.

The repo is `ruff check` clean at that set. Getting there was 128 findings, of
which the ones worth naming are three `ISC004`s — implicit string concatenation
inside a collection literal, which is the shape a missing comma makes. One of
them, `host/caption.py`'s five-tuple return, was correct but one keystroke away
from silently returning six elements. The rest were mechanical: explicit
`check=False` on twelve `subprocess.run` calls, `strict=False` on nineteen
`zip`s, `re.M` spelled out. Behaviour is unchanged — seven log tools were run
against the same bench log before and after and diffed byte-for-byte, and every
entry point's `--help` still imports.

---

### 2026-08-17, housekeeping — the last logs out of `/tmp`, and the CS park stops being m9's

Two things that were owed rather than discovered, done while the board was idle.

**`bench/soak/` — eight runs from 2026-08-15, rescued from `/tmp` two days and
one reboot's worth of luck late.** `bench/cue/` was archived on 08-17 and these
were not, because they are reliability rather than accuracy and nothing quotes a
percentage off them. They are still the only copy of two things: the **454 ms
against 802 ms** that is issue #1's headline, and the **earliest recorded
instance of the USB outage** — 12:16 and 12:20, both at 150 MHz, board gone from
the bus entirely with the watchdog not getting it and only a VBUS cycle bringing
it back. Three of the eight runs died, in two ways that look identical from the
host: 12:04 at 280 MHz was a `ft_capture` hang the watchdog **caught** and
reported (that is #12), and the two 150 MHz ones were the PSRAM chip select
(#16/#9). Worth having the pair side by side, because "the port vanished" is the
same first line on the host for both. `bench/soak/README.md` is the manifest;
`bench_loop.sh` is checked in with them, including the hard-coded `-l 2-1 -p 1`
that was already wrong by the next morning.

**#17 — the chip-select park is now every target's, and it is a preinit hook.**
`8daa66b` fixed `m9` and left `m2`, `m5b`, `m6`, `m7`, `m8`, `cam_probe` and
`diag` exposed. Nothing had been seen to hit it there for an uninteresting
reason — the bug needs ~1,500 frames and those are two-minute bring-up images —
so the hole was a trap rather than a fault, and `m7`'s clock ladders are where
it would have sprung.

`firmware/qspi_park.c` is the fix and it is deliberately not three lines copied
seven times. It registers `fgx_qspi_park()` in the SDK's preinit array at
`"00601"` — immediately after `PICO_RUNTIME_INIT_POST_CLOCK_RESETS` (`"00600"`)
releases the pad banks, which is the earliest slot where writing `PADS_BANK0`
does anything at all; earlier is not safer, it is a no-op. So the park happens
**before `main()`**, and integrating it into a new target is listing one file in
`add_executable`. There is nothing left to call and therefore nothing to forget.

The three GPIO lines are copied byte-for-byte from `m9`, including leaving the
pad's pull-down alone — the output driver wins against it, and the point of
carrying over a sequence that produced 15,008 clean frames is not to improve it.
`m9.c` keeps a pointer where the essay used to be.

**It is listed on `forgix_m5` and `forgix_psram_probe` too, which link
`hardware_psram` and actually want the part.** That was the one thing worth
checking rather than assuming: `runtime_init_setup_psram()` registers at
`"11080"` and reclaims the pin with `gpio_set_function(..., GPIO_FUNC_XIP_CS1)`,
so ordering makes it safe. `nm -n forgix_m5.elf` shows the array as built —
`post_clock_resets`, `fgx_qspi_park`, … , `setup_psram` — which is the check,
not the reasoning. All ten targets build and all ten carry the symbol.

Unconditional is the whole idea. A file listed everywhere cannot be a target
somebody forgot.

---

### 2026-08-17, later — measure the ceiling first, and half of #19 goes away

The entry below reads four benches off one number each and gets two of them
wrong. `tools/probe_ceiling.py`, written for [#23](https://github.com/kazunori279/fpga-open-vocab/issues/23),
is the correction: it separates **what a bench could ever have scored** from
**what the decision rule got**, and once those are two columns instead of one,
several months of "the rule regressed" turns out to be three different things.

**The ceiling is exact, which is why it is worth having.** The board decides in
the centred space `c[i] = z[i] − mean(z)`. With two queries that space is
one-dimensional — `c = [+D/2, −D/2]` for the margin `D = z[A] − z[B]` — so `D`
carries *everything* the board could use. No enrolment, rule or threshold beats
`D`'s own separability. Call it `|sep|`, folded so direction does not count.

| bench | \|sep\| | within | best | state | lost | pair |
|---|---|---|---|---|---|---|
| 08-11 07:22 | 1.000 | 1.000 | 100.0% | 100.0% | 0.0 | book |
| 08-17 15:20 | 0.999 | 0.999 | 98.8% | 95.8% | 2.9 | hand |
| 08-17 07:33 | 0.994 | 0.995 | 99.4% | 96.7% | 2.8 | book |
| 08-17 09:18 | 0.971 | 0.975 | 94.4% | 91.7% | 2.8 | book |
| 08-17 13:35 | 0.970 | 0.975 | 93.8% | 57.5% | **36.3** | book |
| 08-17 11:26 | 0.932 | 0.915 | 88.3% | 92.5% | −4.2 | book |
| 08-17 15:42 | 0.928 | 0.948 | 89.6% | 90.8% | −1.2 | bag |
| 08-17 13:39 | 0.916 | 0.956 | 82.1% | 74.2% | 7.9 | book |
| 08-17 09:57 | 0.895 | 0.887 | 86.7% | 74.2% | 12.5 | book |
| 08-17 08:55 | 0.873 | 0.836 | 88.9% | 25.8% | **63.1** | book |
| 08-17 09:33 | 0.838 | 0.803 | 77.8% | 59.2% | **18.6** | book |
| 08-16 17:22 | 0.824 ! | 0.819 | 85.6% | 58.3% | **27.2** | book |
| 08-17 08:57 | 0.787 | 0.894 | 85.0% | 76.7% | 8.3 | book |
| 08-17 15:37 | 0.771 ! | 0.863 | 80.4% | 78.3% | 2.1 | person |
| 08-17 11:44 | 0.746 | 0.769 | 72.5% | 68.3% | 4.2 | book |
| 08-17 15:27 | 0.699 ! | 0.699 | 67.5% | 60.0% | 7.5 | glass |
| 08-16 17:35 | 0.599 ! | 0.593 | 62.2% | 56.7% | 5.6 | book |
| 08-17 09:55 | 0.579 | 0.654 | 59.4% | 47.5% | 11.9 | book |

`best` is the best any threshold on the margin could do, `state` is the
nearest-reference rule held out with no presence gate, and **`lost` = best −
state is what the decision rule cost.** `!` marks an inverted margin.

**#19 is four benches, not everything since 08-11.** Only 08:55, 13:35, 17:22
and 09:33 threw away a ceiling they had — 63, 36, 27 and 19 points. Every other
low score collected what was on offer to within about a dozen points and was low
because *the ceiling was low that morning*. That is a much better issue than the
one that was open: four clean cases instead of a drift.

**The book pair's ceiling swings from 1.000 to 0.579 across the same desk.**
Fourteen runs, two phrases, one book, and the ceiling alone spans 42 points
before any rule is reached. This is the same lesson as `sep`-is-not-a-scale
arriving from the other side: a bench measures the staging at least as much as
it measures the appliance. One of #19's own two founding runs — 08-16 17:35, at
0.599 — is on that list, so it never tested the rule at all.

**Two corrections to the entry below.**

*The glass is not proven to be the model.* Its margin reads 0.301, which the
entry below calls "the encoder does not carry it". 0.301 is 0.199 from chance
**in the inverted direction** — a real signal named backwards, not an absent
one — and folded it is 0.699. That is low, but the book pair has read 0.599 on a
morning when the encoder was demonstrably fine. **One run cannot tell a model
limit from a bad morning**, and the glass has exactly one. It is a candidate,
recorded in #23, and not a finding.

*The person bench is not a state-stage failure.* The entry below reads its 50.0%
as staging variance, on the strength of visit centres at −0.77/+0.77/+0.91/+1.22
and `enrolled from 0/6`. The variance is real; it cost **2.1 points**. The state
stage collected 78.3% of an 80.4% ceiling. What took it from 78.3% to the 50.0%
that was quoted is **#18's presence gate**, which called 34 held-out class frames
absent. `enrolled from 0/6` is the same artefact — the board's MATCH is gated, so
that column cannot see the state stage on its own either.

**Which makes the gate's cost measurable for the first time, and it is narrow.**
Comparing every bench's live `HELD OUT` against its ungated state stage, the gate
costs class frames on exactly three of the eighteen — 08:55, 15:27 and 15:37, at
25.8, 25.8 and 28.3 points — and nothing at all on the other fifteen. It is not a
tax spread across the appliance; it is a cliff that three benches fell off. Both
of the benches whose *presence* half inverted worst (15:20 and 15:42, 90/90 empty
frames absorbed) paid nothing here, which is the asymmetry
[#21](https://github.com/kazunori279/fpga-open-vocab/issues/21) is about: a
reference on the origin swallows the empty desk without touching the classes.

**This is a diagnostic and can never be a guard.** `|sep|` needs held-out frames
of both classes, so it does not exist until the run is over — unlike `sep`, the
two ratios and `enrolled from`, all of which were available at enrolment and all
of which were wrong. There is no constant here to fit and nothing for `m9.c` to
do with it. `probe_ceiling.py` has no threshold in it for the same reason: an
absolute floor would have called half the book runs a model limitation.

---

### 2026-08-17 — four object pairs in twenty-two minutes, and the appliance has a shape

Everything before this entry is two books. Four benches at 15:20, 15:27, 15:37
and 15:42 — hands, a glass, a person, bags — same board, same light, same
schedule, four different things to tell apart. All four clean: 546 frames each,
no dropped frames, no USB outage, worst camera-bus gap 15–22 µs against a
2,000 µs deadline. `bench/cue/m9_cue-20260817-1520*.log` through `-1542*.log`.

| bench | pair | held out | margin AUC | sep(2) | spread | ratio | enrolled from | presence AUC |
|---|---|---|---|---|---|---|---|---|
| 15:20 | an opened / a closed hand | **95.8 %** | 0.999 | 2.92 | 0.54 | 5.5x | 6/6 | 0.349 |
| 15:27 | a glass with tea / an empty glass | **34.2 %** | 0.301 | 0.23 | 0.56 | 0.4x | 2/6 | 0.754 |
| 15:37 | a person standing / hands up | **50.0 %** | 0.771 | 0.69 | 1.37 | 0.5x | **0/6** | 0.241 |
| 15:42 | a small / a big bag | **90.8 %** | 0.928 | 4.06 | 1.68 | 2.4x | 6/6 | 0.482 |

**Two of the four work, and 08-11 is no longer alone.** 95.8% and 90.8% are the
second and third best figures in the project, they are the first 90%+ since
2026-08-11, and they are on pairs that are not the book. Whatever went wrong
between 08-11 and 08-16, the appliance does still do this.

**And the two that failed, failed in two different ways, neither of them the
one #19 is about.**

*The glass is the model's fault, not the rule's.* Margin AUC **0.301** — best
cut 67.5% and pointing the wrong way. The encoder does not carry "is there tea
in it". Visit centres sit on top of each other (`a glass with tea` −0.26, +0.16,
−0.24, +0.09 against `an empty glass` +0.22, +0.02, +0.38, +0.15) and `sep` over
two visits is 0.23. The rule did what it could with references a quarter of a
unit apart. This is the first bench that fails for a reason that is not staging,
and it is worth having: 1.40 M int4 parameters through a 512-d PCA is not a
fine-grained attribute detector, and now there is a number for that.

*The person is staging, but a different staging failure from 13:35's.*
Margin AUC 0.771, best cut 80.4% — the model **does** separate them. But:

```
a person standing   -0.41  -0.24  -0.60  +0.03
a person, hands up  -0.77  +0.77  +0.91  +1.22
```

Spread 1.37 against a `sep` of 0.46. Visit 1 at −0.77 and visit 2 at +0.77, so
the pooled reference lands at ≈0.0, where **neither visit was**. 13:35's opened
book walked in one direction; this one scatters. Both come out as "the reference
does not describe the object", and `tools/probe_sepscale.py` now says so in the
visit-centre rows rather than in any ratio.

Its tell is new and blunt: **`enrolled from 0/6`.** The rule cannot classify the
frames the reference was built from.

**15:42 finished off the enrolment bar.** It read **2.4x** — two tenths under the
2.6 deleted three hours earlier — and scored 90.8%. Eight board-side prospective
tests now exist, which is every one the bar ever had:

| ratio | held out | what 2.6 would have done |
|---|---|---|
| 5.5 | 95.8 % | certify — right |
| 3.7 | 57.5 % | certify — **wrong** |
| 2.4 | 90.8 % | reject — **wrong** |
| 2.3 | 74.2 % | reject — right |
| 1.8 | 92.5 % | reject — **wrong** |
| 1.2 | 68.3 % | reject — right |
| 0.5 | 50.0 % | reject — right |
| 0.4 | 34.2 % | reject — right |

Four of the eight are calls that would have mattered — the two best runs and the
two worst calls — and it gets **one of the four right**. Spearman ρ over all
eight is 0.69 and the extremes do line up, which is precisely the shape the last
four mistakes had at the moment each was made. Nothing goes back.

**`enrolled from` is the fifth statistic and it is not being shipped either, but
it is a different kind of thing.** Over all eighteen scoreable benches:

```
below 6/6  (5 benches)   4/6 -> 58.3 %   0/6 -> 57.5 %   0/6 -> 0.0 %
                         2/6 -> 34.2 %   0/6 -> 50.0 %
at 6/6    (13 benches)   47.5 % ... 100.0 %
```

Every run that missed a single one is at 58.3% or below, with no exception; 6/6
says nothing. Three things make it unlike the four that failed: **there is no
constant to fit** (the threshold is "perfect", not a tuned number), it is a
self-consistency check rather than a forecast of staging that has not happened,
and it is free on the board, which already holds both the references and the
frames they came from. Against that: the deleted bar was also one-sided with
three supporting benches on the day it shipped and died on the fourth, this has
five, and it is **structurally blind to drift** — 13:35 scored 6/6 and 57.5%. So
it stays out of the firmware and goes in an issue, to be watched on the next
bench that fires it rather than fitted to the eighteen in hand.

**#18, on fourteen benches: the gain is gone.** The leave-one-bench-out replay
as benches accumulate — blind mean of the best unit against the shipped
`FGX_ABSENT_TRIP = 2.0 sep`, and the cost of not having seen the bench:

```
benches    best blind   shipped    gain    cost
   10         64.3 %     58.3 %     6.0    10.7
   12         61.9 %     58.0 %     3.9    11.4
   13         59.0 %     56.7 %     2.3    12.5
   14         58.7 %     56.2 %     2.5    12.0
```

The cost has not moved and the gain has collapsed. The per-bench optimum now
spans **0.15 to 3.60** in absolute distance, 24× — 15:20 wants 0.15 and 08:55
wants 3.60. There is no radius to retune to, and this is no longer a sweep that
has not been run. Six of the fourteen are genuinely inverted (AUC < 0.5), which
is what remains of #18.

**Both stages fail on the same geometric fact, from opposite sides.** 15:20 is
the clearest statement of it in the tree: state 95.8%, presence 0/90. The two
references are 2.92 apart, which is what state needs — and `an opened hand`
enrolled **0.08 sep from the origin**, which is where "nothing has changed since
the background froze" lands, so every empty frame was called that class. The
board printed exactly that at enrolment, class name included, before the first
held-out frame. 15:42 did the same thing with `a big bag` at 0.26 sep and 90/90.
Origin warnings now stand at **five right, two wrong, one missed** over eight
benches that could have had one.

15:27 is the inverse and it makes the `sep`-is-not-a-scale argument concrete for
the presence radius. Its `sep` is 0.26, so the shipped 2.0 sep trip lands at 0.52
absolute against an ideal 0.35 — close, and it is the only bench since 08-16
where the shipped constant clears the floor (63.3%). 15:20's `sep` is 2.90, so
the same constant lands at 5.80 absolute against an ideal 0.15: **39× too big**.
The constant is only ever right when `sep` happens to fall near the right
absolute radius.

---

### 2026-08-17 — the control run says #19 is not the schedule, and the bar certified the run it settles

Two benches at 13:35 and 13:39, same light, same phrases, same board, four
minutes apart. The first dropped the empty revisit — issue #19's control, meant
to reproduce the 2026-08-11 conditions that produced 120/120. The second put the
empty rotation back for #18. Both clean: 387 and 547 frames, nothing dropped, no
reboot. `bench/cue/m9_cue-20260817-133552.log` and `-133952.log`.

**The hypothesis is dead.** #19 proposed that `cue.py`'s return to an empty desk
made the operator re-stage the objects, so 08-16's 58% was the honest number and
08-11's 120/120 had been measuring a desk that never moved. Remove the revisit
and it should come back near 100%:

| run | schedule | held out |
|---|---|---|
| 2026-08-11 07:22 | no empty revisit | **120/120 (100.0%)** |
| 2026-08-16 17:22 | empty revisit | 70/120 (58.3%) |
| 2026-08-16 17:35 | empty revisit | 69/120 (57.5%) |
| **2026-08-17 13:35** | **no empty revisit** | **69/120 (57.5%)** |

It came back at 57.5%, which is the issue's own outcome (2): whatever changed
between 08-11 and 08-16, it is not the schedule.

**But the reading underneath the hypothesis survives, and now there is a
picture of it.** The claim worth keeping was never about the empty scene, it was
that on 08-11 "held out" meant held out in time and not in pose. Visit centres,
from `tools/probe_sepscale.py`:

```
08-11 07:22   a closed book    -0.86  -0.81  -0.98
              an opened book   -2.47  -3.96  -3.36

08-17 13:35   a closed book    +5.82  +5.33  +6.97  +5.98
              an opened book   +1.96  +3.25  +4.64  +4.39
```

The closed book sits still in both. The opened book walks — monotonically, and
toward the class it is being told apart from. Enrolment pooled visits 1 and 2,
centre +2.6; the two held-out visits arrived at +4.64 and +4.39, by which point
the closed reference at +5.8 was the nearer one. **9 of 60 held-out opened
frames were called right, and 66 of 66 closed ones were** — the same
one-directional collapse #19 recorded for 08-16 run 1.

And the book never left the frame. It was opened and closed in place for the
whole run, which is exactly what the control was for. So pose drift is not
caused by taking the object out of shot; it happens anyway, and 08-11 is the run
where it happened not to.

The `--preview 20` pictures say what moved. Recovered from the log with
`uv run host/cam.py <log> --out /tmp/shots` — the base64 is in there, which is
the whole reason `--preview` costs 44 KB a frame. Visits 1 and 2 of the opened
book are pages filling the frame; visits 3 and 4 are a book that has receded far
enough to show its spine and the desk around it. A book you can see the edges of
looks more like *a book* and less like *a page*, which is the direction the
numbers moved.

**The signal did not degrade, and the encoder is not involved.** Re-scoring
08-11 with today's `score_cue.py`, the margin `an opened book − a closed book`
separates the two scenes at 100.0% (best cut, in sample); on today's control it
separates them at **93.8%**, AUC 0.970. What collapsed is the nearest-reference
rule, on frames a linear margin still splits. Three things are therefore
excluded:

- **the model and the RTL.** `weights crc32=0xF368CC6E` and
  `bitstream crc32 0a2e9953` are byte-identical in the 08-11, 08-16 and 08-17
  logs. Same teacher, same weights, same fabric.
- **the requantize fix (#14, `9e2b887`, 2026-08-15).** It sits exactly in the
  gap and it changed the encode by 80 ms, but `probe()` gates it on all 512
  embedding floats matching `encoder_fast` and reported 512/512 exact. It is
  bit-exact; it cannot have moved an embedding.
- **anything in `m9.c`'s decision path**, which #19 already established.

What is left in the gap is the capture path (`7412684` overlapped the capture,
`c2a787e` stopped shipping a half-second-old frame — both change which photons
land in a frame without changing any arithmetic), the clock and link rate
(280/75 → 320/160), and the enrolment procedure itself: 08-11 enrolled three
references from one visit each including the empty desk, today enrols two from
two visits each. **#19 stays open**, with the schedule ruled out.

**And the enrolment bar certified the 57.5% run.** This is the fourth statistic
to fail and it failed on its first prospective test. The board printed, at
frame 212:

> This enrolment clears the bar: 4.29 apart against 1.17 of spread within a
> class, over 2 visits. The three benches that have done that scored 91.7%,
> 96.7% and 100.0%. **Three runs is all it rests on.**

3.7× is the highest ratio this arithmetic has ever produced on any bench. It
held 69 of 120. The 13:39 run read 2.3×, below the bar, and scored 74.2%.

Four benches have ever produced a board-side two-visit ratio — every prospective
test the bar has had — and it is ordered backwards at both ends:

| board's own ratio | held out | |
|---|---|---|
| 3.7 | 57.5 % | 08-17 13:35, certified |
| 2.3 | 74.2 % | 08-17 13:39 |
| 1.8 | 92.5 % | 08-17 11:26, `THE CLASSES OVERLAP` |
| 1.2 | 68.3 % | 08-17 11:44 |

Note which column that is. The bar ran in firmware on the board's own number,
from the 20 frames after the key press; the eleven-run table in `m9.c` and
`probe_sepscale.py` is an offline replay pooled from the first 20 frames of each
cued span, and the two disagree — 11:26 replays at 1.12 and the board printed
1.8×. This is the same two-columns-that-look-alike trap as the held-out figures
earlier today, so both are now labelled wherever they appear.

`FGX_ENROL_SNR` is **deleted**. Not moved, not re-fitted: `sep`, ratio(1),
ratio(2) and ratio(2)-used-only-upward have now each sorted the benches they
were measured on and broken on the next one, and the fifth statistic would be
the same mistake a fifth time. The ratio is still computed and still printed,
because it is a real property of the enrolment; the board no longer says
anything about what it means, in either direction. Flashed 2026-08-17.

**#18's tenth bench, from the 13:39 run:** AUC 0.911, not inverted, its own best
radius 1.20 absolute for 83.8% balanced — and `FGX_ABSENT_TRIP = 2.0 sep` scores
50.0% on it, the floor. Adding it to the leave-one-bench-out replay moves the
answer past a threshold of its own:

```
unit                          blind mean   LOO cost   worst
scat, the enrolment spread         64.3%      10.7    20.1
absolute distance                  62.7%      12.2    33.8
sep, as FGX_ABSENT_TRIP does       59.9%      12.5    33.5
FGX_ABSENT_TRIP = 2.0 sep          58.3%
```

This morning, on nine benches, the best blind unit beat the shipped constant by
7.9 points against a leave-one-out cost of 6.8 — thin, but positive. On ten it
is 6.0 points against a cost of 10.7. **The cost of not having seen the bench
now exceeds the gain over what ships**, so there is no radius worth changing to,
and that is a stronger statement than "worth about five points". `FGX_ABSENT_TRIP`
is untouched for the fourth day running.

---

### 2026-08-17 — "the presence geometry is inverted" was wrong, and the nine-bench replay says the radius is worth five points

Written into the entry below this morning, and put on issue #18 before anything
was replayed:

> `11:26   empty mean 0.87 sep   classes mean 0.70 sep`
> **The empty desk is FURTHER from the references than the class frames are.**
> The rule cuts on "far means absent" and on this scene the ordering is
> inverted, so every radius calls the desk present or calls everything absent.

**Further is the direction the rule wants.** `absent ⇔ d > radius` wants the
empty desk further out; 0.87 against 0.70 is a correct ordering with a small
margin, and what actually happened is a 2.0-sep threshold sitting above both
populations. Two errors in one sentence, and the word "inverted" was doing the
work in both.

Now that the logs are in `bench/cue/`, `tools/probe_presence.py` replays #18
across all of them at once. Nine benches have a held-out empty rotation:

| bench | AUC | best r | balanced there | at 2.0 sep |
|---|---|---|---|---|
| 08-16 17:22 | 0.956 | 2.50 | 94.9% | 94.2% |
| 08-16 17:35 | 0.909 | 2.55 | 87.5% | 86.4% |
| 08-17 09:18 | 0.923 | 2.50 | 90.8% | 50.0% |
| 08-17 11:44 | 0.904 | 1.55 | 90.0% | 50.0% |
| 08-17 11:26 | 0.726 | 0.55 | 72.6% | 52.2% |
| 08-17 08:55 | 0.711 | 3.60 | 80.0% | 51.7% |
| 08-17 07:33 | 0.319 | — | 50.0% | 50.0% |
| 08-17 09:57 | 0.276 | — | 50.0% | 50.0% |
| 08-17 09:55 | 0.274 | — | 50.0% | 50.0% |

**Three of nine are genuinely inverted** — AUC below 0.5, the empty desk nearer
the references than the objects — and nothing thresholded on this distance
recovers those. **Six are not**, four of them strongly. So it is not the scene
in the sense claimed this morning, and the radius sweep is not blocked.

It is also not the fix. The per-bench optimum is 0.55 to 3.60 absolute, 0.50 to
3.90 in sep — a seven-fold range. Fit the radius on eight benches and score it
on the ninth, which is the only measurement that describes the appliance:

```
sep, as FGX_ABSENT_TRIP does     64.1 %   (worst fold -20.7)
scat, the enrolment spread       67.1 %   (worst fold -17.1)
absolute distance                67.8 %   (worst fold -22.6)
FGX_ABSENT_TRIP = 2.0 sep, today 59.2 %
```

**Five to eight points, not thirty**, and the three units are within four points
of each other, so the unit is not the bug either. This is the same structural
fact for the fourth time: the right constant is a property of the bench and
nothing measurable beforehand predicts it. A radius sweep is therefore still not
worth a morning of daylight — not because the geometry is inverted, which it
mostly is not, but because the number it would produce is worth five points and
would be the fifth constant fitted to these same benches.

What #18 needs is evidence the distance to a reference does not carry. That is a
different experiment, not a different threshold.

---

### 2026-08-17 — the benches move into the repository, and two rows of the table turn out to be the wrong column

Every number in this project that says how well the appliance recognises
anything came out of one of twenty-five log files, and until this afternoon all
twenty-five lived in `/tmp`. `FGX_ENROL_SNR`, the eleven-run table, the argument
in the entry below that three enrolment-time quantities have each failed the
same way — all of it rested on files macOS deletes. A bench costs a morning of
daylight and cannot be regenerated from anything on disk. They are in
`bench/cue/` now, 2.8 MB, with `bench/README.md` as the manifest.

**Writing that manifest is what caught the error.** `tools/score_cue.py` prints
two held-out figures and they are not the same measurement: the live `HELD OUT`
line, which is what the board's own rule did, and `one visit per state, then
held out`, an offline replay under a different enrolment. Scoring all
twenty-five logs to fill the manifest showed the two disagreeing by as much as
83 points — `m9_cue-20260817-085504.log` is 0.0% live and 83.3% replayed.

Re-scored, the **nine original rows of the table match the live figure exactly,
all nine.** The two rows added this morning were filled in from the replay
column, and the "metric inconsistency" corrected just before `2e48d86` was
corrected the wrong way round:

| | in `2e48d86` | correct |
|---|---|---|
| 08-17 11:26 | 91.7 % | **92.5 %** |
| 08-17 11:44 | 76.1 % | **68.3 %** |

Nothing in the argument moves. 11:26 still read 1.12, still sat on the reject
side, and is still the best bench in the project — by a wider margin than
claimed, since 92.5% beats the 91.7% of the worst run *above* the bar rather
than tying it. What does change is a count repeated in five places: below the
bar there are **eight** runs, not eleven, and they span **92.5% to 47.5%**. The
firmware's printed message said 91.7% and now says 92.5%; `m9.c`,
`probe_sepscale.py`, `probe_reject.py`, `architecture.md` and the README are all
corrected, and each now names which of the two columns it means.

This is a fourth thing got wrong about the same eleven numbers in one day, and
it is the reason the manifest carries both columns for every log rather than the
one that happened to be in use.

---

### 2026-08-17 — the two-visit guard's first prospective test rejects the best run of the day

The entry below shipped `FGX_ENROL_V = 2` on a nine-bench table with a void in
it, between 1.24 and 2.64, and put the reject threshold inside that void. Two
benches at 11:26 and 11:44 are the first runs where the **board itself** built
its references from two visits, rather than the ratio being replayed off a log
afterwards. The first one filled the void.

| | 11:26 | 11:44 |
|---|---|---|
| ratio(2), the board | **1.8x** | **1.2x** |
| ratio(2), `probe_sepscale.py` | **1.12** | **0.92** |
| what the board printed | `THE CLASSES OVERLAP` | `THE CLASSES OVERLAP` |
| state stage, held out | **111/120 (92.5%)** | 82/120 (68.3%) |
| same, one-visit replay for comparability | **165/180 (91.7%)** | 137/180 (76.1%) |

**11:26 is as good as any bench this project has run, and the board told the
operator to throw it away.** Eleven runs now, sorted by ratio(2):

```
3.24 -> 96.7 %      0.94 -> 74.2 %
2.94 -> 100.0 %     0.92 -> 76.1 %
2.64 -> 91.7 %      0.87 -> 76.7 %
1.24 -> 59.2 %      0.44 -> 47.5 %
1.12 -> 91.7 %      0.22 -> 58.3 %
                    0.15 -> 57.5 %
```

**That is the third quantity measurable at enrolment to fail the same way** —
`sep`, then ratio(1), then ratio(2). Each sorted the benches it was fitted to
and broke on the next one, and three times is enough to stop calling it bad
luck with a statistic. What decides a run is where the object lands on visits
that have not happened yet, and nothing measured at the enrolment can contain
that. 11:26 is the cleanest possible statement of it: its two enrolment visits
sat 0.05 apart on the state axis and its held-out visits sat 1.67 and 1.72
apart. **The reference pair was tiny and pointed the right way, which is all
classification needs** — the ratio measures magnitude.

The obvious follow-up, that the first visit is systematically the worst and
enrolment should move to visits 2 and 3, **does not survive 11:44**: its visit
gaps went 0.39, 2.17, 0.51, 0.45, with the outlier at visit 2 and no trend.
Visit centres wander; they do not drift.

**So the guard is one-sided from now on.** Above the bar it says so — 2.64,
2.94 and 3.24 scored 91.7%, 100.0% and 96.7%, three for three — and below it
the board prints the numbers and **no advice at all**. `FGX_ENROL_SNR` moves
2.0 → 2.6 for the same reason: 2.64 is the lowest ratio that has ever certified
anything and the interval below it is not measured, so erring upward costs a
line of praise and erring downward costs a bench. The one-visit note now comes
*before* the bar rather than after it, since on one visit the ratio has now been
wrong in both directions (09:33 passed at 2.71 and scored 59.2%; 11:26 read 0.9x
and scored 91.7%). Three runs is what the bar rests on and the message says so.

While rewiring that: the origin guard had been hanging off the end of the same
`else if` chain, so **an enrolment that cleared the ratio was never told its
reference sat on the origin.** Two unrelated failure modes sharing one `else`.
It is its own `if` now.

**#18 got measured twice more and failed twice more, in a way no radius fixes.**
Both runs had the empty rotation on, so both have held-out empty frames — 7/90
(7.8%) and 0/90 (0.0%). The reason is the same on both and it is not the
threshold:

```
11:26   empty mean 0.87 sep   classes mean 0.70 sep
11:44   empty mean 1.06 sep   classes mean 0.44 sep
```

**The empty desk is FURTHER from the references than the class frames are.** The
rule cuts on "far means absent" and on this scene the ordering is inverted, so
every radius calls the desk present or calls everything absent. `an opened book`
took 78 and 86 of the 90 empty frames. This is the 07:33 geometry again and it
is now three benches, so it is the scene and not an accident: a book on a desk
with the background frozen on that same desk leaves the opened state sitting
between the closed state and nothing-at-all.

---

### 2026-08-17 — the guard fires for the first time, and then a fourth run says what it is still not measuring

Two more benches, and between them they finish the argument the three entries
below were circling.

**09:55 is the first time `THE CLASSES OVERLAP` has appeared on hardware.**
`nearest pair 0.79 apart, scatter 1.13 (0.7x)`, printed about ninety seconds
in. The run was allowed to finish anyway, to find out whether the guard was
right: **47.5% held out, presence AUC 0.274.** It was right. That is the whole
case for the guard — ten minutes of bench time and a scoring pass, replaced by
one line at the enrolment.

**09:57, re-enrolled, then read 5.1x — the cleanest ratio of any bench so far —
and scored 74.2%.** So the retraction below stands and gets sharper: the
within-window ratio is not merely an imperfect predictor, it is close to
useless above the floor. Sorted by it, the nine benches go 13.91 (100%), 3.69
(76.7%), 2.71 (59.2%), 2.52 (96.7%), 2.17 (91.7%), 1.81 (74.2%), 0.67, 0.10,
0.05.

**What does sort them is the same ratio measured over more than one visit.**
`tools/probe_sepscale.py`, written for this, pools every visit of a class into
one centre and measures the frame spread about *that*:

| run | held out | sep (1 visit) | ratio(1) | **ratio(2)** |
|---|---|---|---|---|
| 07:33 | 96.7% | 2.40 | 2.52 | **3.24** |
| 08-11 07:22 | 100.0% | 2.28 | 13.91 | **2.94** |
| 09:18 | 91.7% | 3.61 | 2.17 | **2.64** |
| | | | | *— nothing here —* |
| 09:33 | 59.2% | 3.83 | 2.71 | **1.24** |
| 09:57 | 74.2% | 3.69 | 1.81 | **0.94** |
| 08:57 | 76.7% | **5.83** | 3.69 | **0.87** |
| 09:55 | 47.5% | 0.84 | 0.67 | **0.44** |
| 08-16 17:22 | 58.3% | 0.17 | 0.05 | **0.22** |
| 08-16 17:35 | 57.5% | 0.26 | 0.10 | **0.15** |

Every run on the correct side, and the void between 1.24 and 2.64 is wide
enough that **2.0 did not have to move** — the constant was never the problem,
the quantity was. Note the `sep` column while it is here: the largest value of
all nine belongs to a 76.7% run and the smallest three include a 58.3%. **`sep`
as this board has been printing it is not a scale**, which also means #18's
`2.0 sep` radius has been quoted in a unit that means something different every
run, and that is why the radius is still not being touched.

So `FGX_ENROL_V = 2`: a repeat press on a class folds a second visit into the
same reference instead of replacing it, `host/cue.py` schedules the second
press, and `--repeat` defaults to 4 so two visits per class are still held out.
The firmware cost is eight bytes a class — the obvious per-query sum of squares
is 144 bytes and **does not link**, this image having about twenty bytes of
headroom, so it accumulates the scalar `Σ|cz|²` and uses
`Σⱼvar(xⱼ) = E[|x|²] − |μ|²` instead.

**The second visit is not an accuracy win and should not be sold as one.**
`tools/probe_multivisit.py` replays leave-one-visit-out over all seven scorable
logs: two-visit references beat one-visit ones by 1.1 and 0.6 points on the two
clean benches, lose by 18 points on 08-16 17:35, and a longer *single*-visit
window does just as well wherever either helps. It is here to make the guard
measurable, nothing else.

**Two host bugs found while trying to see the second visit on hardware, both of
them the same event read twice.** m9's `'R'` is `watchdog_reboot()`, and
`demo.py` presses it when it finds the board still looping from a killed run —
so the port vanishes and `follow_reboot()` marks the run rebooted. Two benches
in a row therefore came back `>>> VOID: the board wedged mid-run` with a clean
log underneath: **a reboot this end asked for, counted as a wedge.** Cleared now
at the point it is caused, before the run starts, so a later one still voids.
Underneath it, the frames the old loop had already printed — numbered 1114 on
the run that caught this — were in the log and had been through `cue.py`, which
opened its baseline at 1114 and fired the first cue against the previous
session; after the reboot the counter restarted at 1, `i - start` went negative,
and no further cue ever came. The enrolment keys still landed, because those
ride `demo.py`'s own schedule off the board's counter, which is exactly why it
read as *cues are broken* rather than *wrong session*. `cue.py` now re-arms when
the frame counter goes backwards and `demo.py` rebuilds the log from the
reattach, so one file is one session. Verified by killing a run to leave the
board looping and re-running: ten cue boundaries, frame 0 to 361, no void.

---

### 2026-08-17 — two clean enrolments, and the second one retracts what the first one seemed to prove

09:18 and 09:33, both with the new guard live, both passed it: `2.99 apart,
scatter 1.05 (2.8x)` and `3.23 apart, scatter 1.17 (2.7x)`, neither with an
origin warning. **They are the first two benches in this project with an
enrolment nothing objects to**, and they disagree with each other.

| | 09:18 | 09:33 |
|---|---|---|
| sep / scatter | 2.99 / 1.05 = **2.85** | 3.23 / 1.17 = **2.75** |
| state stage, held out | **110/120 (91.7%)** | **71/120 (59.2%)** |
| presence AUC | 0.923 | 0.937 (baseline only) |

**So the entry below is wrong where it says the ratio orders the benches, and
this is the retraction.** Two runs a tenth of a ratio apart scored 91.7% and
59.2%. The guard is a **floor and not a predictor**: it still catches the
catastrophic enrolments it was measured on, and 2.0 is still in the right place,
but nothing above it is a promise. #19 is not answered and the sentence that
said it was has been taken out of the README and `architecture.md`.

**What actually separates the two runs is between-visit staging, which the
guard is structurally unable to see.** Scatter is measured inside one enrolment
window — one visit, twenty frames, the operator holding still. The variable that
decides the run is where the *same object staged again* lands. Per-visit centres
on the state axis, 09:33:

```
   an opened book   +2.28   +3.71   +2.75      its reference  +1.72
   a closed book    +4.02   +3.68   +4.61      its reference  +4.00
```

The opened book's second visit sat at +3.71 — **0.13 sep from the closed book's
reference and 0.87 from its own** — so the board called all thirty of its frames
closed, correctly by its own rule. 09:18's visits spread just as widely (+2.23,
+0.88, +2.93) but all of them on the right side of the boundary. That is the
difference between 91.7% and 59.2%, and it is luck about where the boundary fell,
not enrolment quality.

Measured across all eight cue benches: the sign of the **worst held-out visit's
margin** (distance to the nearest other reference minus distance to its own,
saturating at −1.00 sep when a visit centre passes the other reference) orders
them where the scatter ratio does not — +0.86, +0.58, +0.55 for the runs at
100.0, 96.7 and 91.7%, negative for every run below 77%. It is a **diagnostic
and cannot be a guard**: it needs the later visits, which do not exist yet at
the moment the reference is taken. The fix it points at is either staging the
object the same way each visit, or enrolling a class from more than one visit so
its reference sits in the middle of its own spread rather than at one edge of it
— the second is testable offline against these logs before anything is reflashed.

**And the radius question is still open, because 09:33 was run with
`--no-revisit-empty`.** No held-out empty scene, so the only "nothing there"
frames are the baseline, which sits at the origin by construction. Its sweep
says best r = 0.50 sep against 09:18's 0.75 — same direction, both far under the
shipped 2.0, but one honest data point and one degenerate one is not two. The
next run needs the empty rotation on.

---

### 2026-08-17 — the bars were still showing the rule the board stopped using

Noticed at the bench, from the display and the LED disagreeing on the same
frame. `cue.py`'s bars ran a softmax over the raw z; the LED has read the #18
geometry since it shipped. Three separate disagreements, and none of them is a
rounding difference:

* **The bars had no way to say "nothing there".** `ab.sh --enrol` drops the gate
  query, so the two-stage branch never fired and the shares summed to 100% on
  every frame — an empty desk read 91% / 9% while the LED went dark and the log
  said `- (nothing there)`. That is the display being wrong on precisely the
  frames the presence stage exists for.
* **The bars carried the drift the LED does not.** z is raw; the LED reads
  `c[] = z[] − lvl`. The sensor's ~1.5 z warm-up is common to every query, so it
  cancels out of one and moves the other.
* **Rank is not the rule.** Frame 215 of the 09:18 run: `a closed book` scores
  **+12.36** against `an opened book`'s **+9.98**, so the softmax pointed at the
  closed book — while the board matched the **opened** one, at 0.57 sep against
  1.57, and the LED agreed with the board. The z ordering and the reference
  geometry are different questions and this run answers them differently.

Fixed by mirroring instead of re-deriving. Everything needed is already on the
frame line after `led` — `d` in units of sep, and the `MATCH ... nearer by`
that gives the runner-up — so with two references enrolled the rows become
distance per class, filled `1 − d / 2.0` the way the LED's brightness is, and
the presence verdict is copied from the board rather than recomputed. Not
smoothed, unlike the softmax rows: the verdict is hysteretic and a filtered `d`
next to an unfiltered THERE would be a third quantity again. With fewer than
two references the old display is still the right one, because it is still the
rule the board is running.

---

### 2026-08-17 — the guard the entry below asked for, and it turns out to order every bench there is

`m9` now measures the **scatter inside each enrolment window** and refuses to
be quiet when the classes overlap. Twenty frames were already being averaged
into a reference, so a second accumulator — `Σ cz²` beside `Σ cz` — buys the
RMS distance of one enrolment frame from its own window mean for nothing:

```
enrol     : an opened book, level +0.34, scatter 0.10 (20 frames)
enrol     : 2 classes, nearest pair 0.04 apart, scatter 0.20 (0.2x), absent beyond 0.09 (2.0 sep)
```

**Why this is the missing measurement and `sep` alone can never be.** `sep` is
the unit every other distance is quoted in, so a collapsed pair does not read
as small — it makes everything else read as huge, which is exactly how the
08:55 run put its references 26 *sep* from the origin and walked past the
origin guard. The enrolment frames' own spread is the one scale in the problem
that `sep` does not set. A frame lands nearer the wrong reference once the
noise exceeds half the gap, so **`sep < 2 × scatter` is the classes being one
blob**, and that is the whole rule (`FGX_ENROL_SNR`).

**The threshold was measured before it was written, on all six cue benches**
(worst of the two windows, replayed offline by `tools/probe_reject.py`, which
now prints the same figures):

| run | sep | scatter | ratio | state stage, held out |
|---|---|---|---|---|
| 08-11 07:22 | 2.35 | 0.151 | **15.59** | 120/120 100.0% |
| 08-17 08:57 | 6.73 | 1.654 | **4.07** | 92/120 76.7% |
| 08-17 07:33 | 3.25 | 0.897 | **3.62** | 116/120 96.7% |
| 08-16 17:35 | 1.22 | 1.376 | **0.89** | 70/120 58.3% |
| 08-16 17:22 | 1.41 | 2.749 | **0.51** | 69/120 57.5% |
| 08-17 08:55 | 0.20 | 2.353 | **0.08** | 0/126 0.0% |

The runs that worked and the runs that did not are separated by 0.89 → 3.62, a
gap four times wide, and 2.0 sits in the middle of it. That is a constant
placed in a void rather than fitted to an edge — the first threshold in this
project that did not need a sweep.

**And read the right-hand column.** [#19](https://github.com/kazunori279/fpga-open-vocab/issues/19)
has been trying to explain that list by the empty rotation, which does not
order it. **This ratio does**, monotonically apart from one adjacent swap at
3.62/4.07. The variable was never the rotation; it was how good the enrolment
was, and nothing had been measuring that.

Smoke-tested against a still desk, which is the degenerate case that needs
nobody at the bench: two windows on an unchanged scene came out `nearest pair
0.04 apart, scatter 0.20 (0.2x)`. It fell to the older absolute floor rather
than the new branch — right precedence, and the floor stays, because a frozen
sensor could put two identical references 0.04 apart with 0.001 of scatter and
pass the ratio. The board's `0.10 / 0.20` matched the offline replay of its own
log to the printed digit, so the firmware and the calibration are the same
arithmetic. **The overlap message itself has not been seen on hardware yet** —
no live scene here reproduces it on demand — and the next bench is where it
gets its first real chance.

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
