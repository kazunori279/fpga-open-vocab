# Stills, and why they are not benches

PNGs off the appliance's own camera, shot so that a question about the *encoder*
can be asked without spending a morning of daylight on a bench.

**Nothing in here is comparable to a row in [`../README.md`](../README.md)'s
manifest.** No cue schedule, no enrolment, no held-out set, no `.cues` sidecar,
no held-out percentage. A bench measures the whole appliance — camera, staging,
enrolment, decision rule — and costs a morning. A set in here measures one thing
about the model and costs about four minutes.

## Shooting a set

```sh
mkdir -p bench/stills/20260822-laptop
printf 'an opened laptop\na closed laptop\n' > bench/stills/20260822-laptop/queries.txt

sh bench/stills/shoot.sh 20260822-laptop open   1     # then close it by hand
sh bench/stills/shoot.sh 20260822-laptop closed 1
sh bench/stills/shoot.sh 20260822-laptop open   2     # and again
sh bench/stills/shoot.sh 20260822-laptop closed 2
```

**One set is one pair**, and `queries.txt` is what records which pair — beside
the pixels, rather than in a commit message or someone's memory.
`20260821-bisect/` predates the convention and holds two pairs; its README says
so.

**Alternate the rounds. This is not optional.** Twenty consecutive frames of one
scene followed by twenty of the other confounds the class with the AEC, the
daylight and the operator, which is the confound that made four glass benches
unreadable. Two rounds is the floor — below that `probe_bisect.py` cannot
measure its drift null, and it prints `n/a` rather than a zero.

`shoot.sh` grep-checks every run for #25's `enrolment:` and #26's `scene:`
flags, so a set shot through a bad exposure ramp says so at capture time instead
of after the analysis.

## Reading a set

```sh
uv run --script tools/probe_bisect.py \
    --a bench/stills/20260822-laptop/open --b bench/stills/20260822-laptop/closed \
    --pos "an opened laptop" --neg "a closed laptop"
```

Teacher 1152 → PCA 512 → student fp32, on the same pixels, in the board's own
`z` margin, quoted as effect size because AUC saturates. Archive the output next
to the stills.

**A control belongs in any set that might come back low.** A student row reading
0.2 sd means nothing until a pair the board is known to carry has gone through
the identical path — that is what the book pair does in `20260821-bisect/`, and
it is the reason that set's verdict is trustworthy.

## Generated sets, and the one question they answer

A set can also be built without a camera or an object:
[`tools/synth_pairs.py`](../../tools/synth_pairs.py) crops a COCO instance box
out of val2017 and has `gemini-3-pro-image` edit that crop into both states.
Both sides are generated, so *photograph against render* cancels; val2017 is
held out of the distillation; and cropping first means the object already fills
the frame, so the edit instruction can insist that nothing move.

**Screen it blind before measuring it.** The generator obeys the state clause
and quietly ignores the rest often enough that a third to a half of the pairs
in the first four sets were invalid — the room swapped, the object gone, a pint
tumbler become a wine glass — none of which is visible in the margins they
produce. [`tools/synth_sheet.py`](../../tools/synth_sheet.py) lays each pair
out as A|B, the positive side chosen by a hash of the filename; hand the sheets
to two judges that have seen no encoder output, ask *which side holds the
positive state*, and keep only the pairs both named correctly with the object
large and the scene intact.
[`tools/synth_keep.py`](../../tools/synth_keep.py) turns the two verdict files
into the `keep.txt` that `probe_bisect.py --keep` reads. The dropped pairs stay
in the set and the header names the criterion each failed, so the filter is
auditable.

Screening the stimulus before encoding anything is a validity filter. Screening
after seeing the margins would be fitting. The order is the difference.

**A generated set does not predict a bench, and it is not trying to.** Thirty
books on thirty desks asks whether *any* open book outranks *any* closed one; a
bench asks about one book on one desk, with the enrolment and the decision rule
in the loop. Neither number converts into the other, and a set here that reads
well still has to be benched before an accuracy figure is quoted.

**What it does measure is scene-invariance, which is what the product needs.**
"Is the book open" has to hold when the room, the lamp and the exposure all
changed, and thirty different rooms is the only cheap way to ask that. The
answer as of 2026-08-22 is that the teacher does it at AUC 0.91–0.95 and the
student at 0.60–0.70, on both pairs and on two disjoint draws. That gap is the
finding, and it is larger than anything the distillation sweeps move.

**On a generated set, read `sep` and not the paired column.** `--paired`
subtracts the two states of one scene, so the scene cancels — right on stills of
one desk, wrong on thirty different rooms, where the whole question is whether
the state survives the room. It is also unstable: 0.9 sd → 0.3 sd between two
draws with the model held fixed. The pooled cross-scene AUC repeats to about
±0.05 and is the number a user's requirement is actually written in — *is the
book open, whatever else changed*.

**Draw a second set before believing a difference between two checkpoints, and
then draw a different CONTRAST before believing it again.** `synth_pairs.py
--skip <set>` builds a set on scenes the first never used, which catches the
loudest failures — RKD 100 swings 0.73/0.59 and 0.50/0.68 across two draws and
holds nothing. But a second draw resamples *scenes*, and the larger variance in
this eval is across *contrasts*: RKD 10 held +0.10 on the book pair through both
draws and both model families and still turned out to be worth −0.022 ± 0.023
over ten contrasts. See the section below, and the retraction in
[`20260822-synth-book-crop2/`](20260822-synth-book-crop2/README.md#retracted-2026-08-22-rkd-10-is-not-worth-010).

## Sets

| set | pair | what it settled |
| --- | --- | --- |
| [`20260821-bisect/`](20260821-bisect/) | `a glass with tea` / `an empty glass`, plus a book control | the axis is lost at the student and nowhere earlier — not the resolution, not the projection ([#24](https://github.com/kazunori279/fpga-open-vocab/issues/24)) |
| [`20260822-synth-book-crop/`](20260822-synth-book-crop/) | `an opened book` / `a closed book`, generated | the control that caught it: a generated set cannot rank pairs for the appliance, and the teacher-only remit that leaves |
| [`20260822-synth-glass-crop/`](20260822-synth-glass-crop/) | `a glass with tea` / `an empty glass`, generated | the teacher binds fill state, not brightness — 25/25 with the luma cue at AUC 0.658 ([#28](https://github.com/kazunori279/fpga-open-vocab/issues/28)) |
| [`20260822-synth-book-crop2/`](20260822-synth-book-crop2/) | `an opened book` / `a closed book`, second draw | which column to read — cross-scene AUC, not the paired one. Its sweep table also carries the project's most instructive retraction: `RKD 10 is +0.10` survived a second draw and did not survive a second contrast |
| [`20260822-synth-glass-crop2/`](20260822-synth-glass-crop2/) | `a glass with tea` / `an empty glass`, second draw | the teacher's 25/25 replicates on unseen scenes at 23/23; the student's cross-scene AUC is 0.61 against its 0.95 |
| `20260822-synth-{book,glass}` and `-closeup` | the same two pairs | superseded first attempts, kept as the evidence for cropping the source: object too small, then scene re-composed |
| the eight `-crop` sets below | eight more object states in rooms | not a result each — a fleet, for the variance in the section that follows |

## Ten contrasts, because two was measuring the wrong noise

Adding scenes to a contrast shrinks one variance and not the other.

*Within* a contrast, the Hanley–McNeil standard error of a cross-scene AUC is
0.095 at n = 18 and 0.080 at n = 25 — real, and it does shrink with more scenes.
*Between* contrasts it does not: RKD 10 beats the plain baseline by **+0.120 on
the book pair and −0.035 on the glass pair.** The effect changes sign depending
on which object you asked about. The spread across contrasts is about 0.11, and
no number of extra rooms per contrast touches it, because it is not sampling
error in the contrast — it is the contrast.

So the book/glass sweeps were quoting one number that is a mean of two, with a
standard error of 0.11/√2 ≈ 0.078 on a difference of 0.05. That is the whole
reason the `--text` term could not be resolved.

Eight more contrasts were shot on 2026-08-22 to make that mean worth reading:

| set | pair | kept | note |
| --- | --- | --- | --- |
| [`20260822-synth-suitcase-crop/`](20260822-synth-suitcase-crop/) | `an open suitcase` / `a closed suitcase` | 27/29 | the healthiest of the eight |
| [`20260822-synth-toilet-crop/`](20260822-synth-toilet-crop/) | `a toilet with the lid up` / `lid down` | 25/30 | best-preserved scenes; 30/30 `same_scene` |
| [`20260822-synth-bowl-crop/`](20260822-synth-bowl-crop/) | `a bowl full of food` / `an empty bowl` | 25/30 | the fill state nearest the desk contrast |
| [`20260822-synth-umbrella-crop/`](20260822-synth-umbrella-crop/) | `an open umbrella` / `a folded umbrella` | 25/29 | five kept pairs *add* a furled umbrella instead of folding one |
| [`20260822-synth-refrigerator-crop/`](20260822-synth-refrigerator-crop/) | `an open refrigerator` / `a closed refrigerator` | 24/30 | "closed" is often a flat slab with the shelves still behind it |
| [`20260822-synth-laptop-crop/`](20260822-synth-laptop-crop/) | `an opened laptop` / `a closed laptop` | 23/28 | the editor read "closed" as "screen off" on seven pairs |
| [`20260822-synth-bed-crop/`](20260822-synth-bed-crop/) | `an unmade bed` / `a neatly made bed` | 22/29 | the box fills the frame, so the crop is fabric, not a room |
| [`20260822-synth-oven-crop/`](20260822-synth-oven-crop/) | `an open oven` / `a closed oven` | 19/29 | COCO `oven` is largely cooktops; regenerate before quoting alone |

190 pairs, 234 generated, ten contrasts with book and glass. Each set was shot
with `--skip` naming every set before it, so **no photograph appears in two of
them**, and the two second draws stay unspent for confirmation.

Two contrasts were considered and left out on purpose. `tv` on/off is answerable
by mean frame luma, which is the trivial cue `probe_bisect.py` already prints.
`dining table` set/cleared is a state of the scene rather than of the object.

### What the ten said

Cross-scene AUC, `tools/sieve_text.py --score-only`, log in
`model/runs/_text_sieve_10contrast.log`:

| run | book | glass | laptop | refrig | oven | toilet | umbrel | suitca | bowl | bed | mean | vs base |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| so400m-s30k | .565 | .600 | .616 | .714 | .604 | .435 | .421 | .646 | .742 | .618 | **.596** | (baseline) |
| + `--text 0.1` | .673 | .589 | .533 | .726 | .554 | .374 | .405 | .636 | .779 | .626 | .590 | −0.007 ±0.017 |
| + `--rkd 10` | .685 | .565 | .448 | .655 | .587 | .453 | .405 | .613 | .746 | .585 | .574 | −0.022 ±0.023 |
| + `--text 0.3` | .614 | .576 | .571 | .741 | .535 | .339 | .435 | .602 | .728 | .568 | .571 | −0.025 ±0.014 |
| + `--text 1.0` | .577 | .586 | .588 | .618 | .512 | .394 | .507 | .620 | .749 | .519 | .567 | −0.029 ±0.018 |
| + `--text 0.3 --rkd 10` | .664 | .563 | .512 | .597 | .557 | .378 | .386 | .646 | .762 | .556 | .562 | −0.034 ±0.020 |
| teacher 1152 | .907 | .933 | .781 | .786 | .850 | .579 | .715 | .894 | .819 | .847 | .811 | |
| pca 512 | .917 | .933 | .775 | .804 | .873 | .595 | .725 | .888 | .866 | .872 | .825 | |

**The base objective is the top row and every added term is below it.** None of
the gaps clears two standard errors, so the honest statement is not "these hurt"
but **"not one of them helps, and the two that looked like they did were reading
two contrasts"** — `--rkd 10`, retracted above, and `--text`, which never
separated from baseline at any weight and did not shrink the
`oracle_scene − sep` alignment gap it was designed to shrink.

Read that baseline literally. **It is `1 - cos` plus `--infonce 0.3`**, not a
bare cosine loss — every row in the table sits on top of InfoNCE 0.3, as does
every checkpoint the board has flashed. So the sweep says `--text` and `--rkd`
buy nothing *given InfoNCE*; it does not measure InfoNCE, and no number here
covers a run with all three weights at zero.

The fleet did the job it was built for. The paired difference has a
between-contrast sd of 0.045–0.071, so its standard error is ~0.05 at C = 2 and
0.014–0.023 at C = 10 — a three-to-five-fold gain, and the first time this eval
could resolve an effect the size it is being asked about.

Two caveats on the columns. **`toilet` and `umbrella` have no headroom**: the
teacher is at 0.579 and 0.715 there, so nothing downstream can be measured
against much. Dropping both changes no conclusion (`--rkd 10` −0.028 ± 0.028,
`--text 0.1` +0.001 ± 0.020), and they stay in because gating a contrast on the
teacher after the fact is one more decision made with the answers in view. And
the student is *below chance* on exactly those two — 0.435 and 0.421 — which is
an axis pointing backwards, not a weak axis.

**Each set's own README lists what its judges broke**, and several are ugly. The
distinction that decides whether that matters: a *shortcut* — a visible edit
seam, a resolution mismatch, an object that changed colour — inflates every
checkpoint alike and largely cancels in a paired comparison across checkpoints,
which is what this fleet is for. A *negative frame that contains the positive
state* is different in kind, but it too pulls toward 0.5 rather than toward a
wrong answer, so it costs sensitivity and not validity. Neither licenses quoting
an absolute number, which was never on offer here anyway.

The screen's three criteria were fixed before the sets were shot and stayed
fixed after the verdicts came back. Adding a fourth on reading them would be
screening on what was seen, which is the failure `tools/synth_keep.py` was
written to avoid.

### And then one thing did move it, and it was not a loss term

The table above ranks six *losses* trained on the same 30 000 images. Run the
same ten contrasts across the four checkpoints that differ in **how much data
and how long**, and the fleet finally reads a difference. Log in
[`20260822-datasize-10contrast.log`](20260822-datasize-10contrast.log):

| run | data | epochs | book | glass | laptop | refrig | oven | toilet | umbrel | suitca | bowl | bed | mean | vs s30k |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| so400m-s30k | 30k | 20 | .565 | .600 | .616 | .714 | .604 | .435 | .421 | .646 | .742 | .618 | .596 | (baseline) |
| so400m-full | 118k | 40 | .605 | .654 | .722 | .760 | .540 | .382 | .533 | .683 | .774 | .705 | .636 | **+0.040 ±0.019** |
| **so400m-full-a05** | 118k | 40 | .648 | .661 | .679 | .807 | .612 | .448 | .478 | .687 | .747 | .686 | **.645** | **+0.049 ±0.010** |
| so400m-s30k-a05 | 30k | 20 | .596 | .574 | .522 | .707 | .645 | .397 | .453 | .593 | .739 | .525 | .575 | −0.021 ±0.016 |

**More data and more epochs is worth about +0.04 to +0.07, and it replicates in
two different 512-d spaces**: `full` over `s30k` is +0.040 ± 0.019 in the plain
basis, and `full-a05` over `s30k-a05` is +0.070 ± 0.020 in the α = 0.5 one. Those
two comparisons are each within a basis, which is what makes them clean. Set
against six loss terms that between them moved the mean by −0.034 to −0.007, this
is the first setting in the project that the eval can see.

The axis is **confounded**: `so400m-full` has 4× the images *and* 2× the epochs.
Splitting it needs one more run — 30k for 40 epochs — and until that exists,
"data" here means "data or schedule".

**Whitening α = 0.5 is not the reason.** It is +0.009 ± 0.014 at 118k and
−0.021 ± 0.016 at 30k: the sign flips with the other variable, which is the same
shape as `--rkd 10` reading +0.120 on book and −0.168 on laptop. The α = 0.5
`pca 512` ceiling is also slightly *lower* — 0.816 against 0.825 — so it is not
buying headroom either.

Two things this settles that were open. **`so400m-full-a05` is the checkpoint the
board actually flashes**, and it is the best of the four rather than the worst, so
the fortnight of loss-term rankings run on `so400m-s30k` were ranking a weaker
sibling but were not ranking something unrelated to the product. And the gap the
whole exercise is about is still **0.645 against a teacher at 0.811**. Quadrupling
the data closed 0.04 of 0.215. At that exchange rate the rest is not a data
problem; it is the 1.4 M parameters or the shape of the distillation, and both of
those run straight into what the fabric will hold.

## What a set can and cannot answer

It can say **which stage of the encoder chain drops a distinction**, which is
what [#24](https://github.com/kazunori279/fpga-open-vocab/issues/24) needed and
what no bench can see.

It cannot say **what the appliance will score**. Sixteen runs of the same book
pair on the same desk span a margin of 1.000 to 0.579; the staging is a real
variable and a set of stills holds it still on purpose. A pair that reads well
here still has to be benched before any accuracy number is quoted.

And a **low reading is not permission to skip a bench** either — only a low
*teacher* reading is, because nothing downstream can recover what the teacher
never had.
