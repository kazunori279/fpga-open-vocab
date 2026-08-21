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

**Draw a second set before believing a difference between two checkpoints.**
`synth_pairs.py --skip <set>` builds one on scenes the first never used, and it
is what separates a replicated effect from a lucky draw: RKD 10 holds +0.10 AUC
on the book pair across both draws and both model families, while RKD 100 swings
0.73/0.59 and 0.50/0.68 and holds nothing.

## Sets

| set | pair | what it settled |
| --- | --- | --- |
| [`20260821-bisect/`](20260821-bisect/) | `a glass with tea` / `an empty glass`, plus a book control | the axis is lost at the student and nowhere earlier — not the resolution, not the projection ([#24](https://github.com/kazunori279/fpga-open-vocab/issues/24)) |
| [`20260822-synth-book-crop/`](20260822-synth-book-crop/) | `an opened book` / `a closed book`, generated | the control that caught it: a generated set cannot rank pairs for the appliance, and the teacher-only remit that leaves |
| [`20260822-synth-glass-crop/`](20260822-synth-glass-crop/) | `a glass with tea` / `an empty glass`, generated | the teacher binds fill state, not brightness — 25/25 with the luma cue at AUC 0.658 ([#28](https://github.com/kazunori279/fpga-open-vocab/issues/28)) |
| [`20260822-synth-book-crop2/`](20260822-synth-book-crop2/) | `an opened book` / `a closed book`, second draw | which column to read — cross-scene AUC, not the paired one — and the sweep that reading turns into a result: RKD 10 is +0.10 on this pair and nothing on the other |
| [`20260822-synth-glass-crop2/`](20260822-synth-glass-crop2/) | `a glass with tea` / `an empty glass`, second draw | the teacher's 25/25 replicates on unseen scenes at 23/23; the student's cross-scene AUC is 0.61 against its 0.95 |
| `20260822-synth-{book,glass}` and `-closeup` | the same two pairs | superseded first attempts, kept as the evidence for cropping the source: object too small, then scene re-composed |

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
