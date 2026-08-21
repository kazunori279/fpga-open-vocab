# 2026-08-21 — where the glass axis is lost

Eighty-eight 128×128 stills off the appliance's own camera, and the four-stage
bisection they were shot for:
[#24](https://github.com/kazunori279/fpga-open-vocab/issues/24).

**Not a bench.** No cue schedule, no enrolment, no held-out set, no `.cues`
sidecar — nothing in here is comparable to a row in
[`../../cue/README.md`](../../cue/README.md)'s manifest. What the board scored
while it was capturing is in the logs and is provenance only. These are pixels,
shot so that a teacher, a projection and a student can be asked about *the same
pixels* off the bench.

## The question

Four glass benches read a margin AUC of 0.699 / 0.591 / 0.680 / 0.674, one of
them with the sign flipped, fenced on both sides by book controls that scored
96.7% and 98.3% on the same desk thirteen minutes apart. The pair fails and the
staging is not why. #24 asks *where* in the chain the failure is, because the
four stages have four different fixes and three of them are expensive:

    SigLIP 2 SO400M teacher (1152-d)
      → frozen joint PCA to 512
        → the 1.4 M-parameter distilled student
          → int4 weights

## How they were shot

`../shoot.sh` — `host/demo.py --snap-every 2`, rendered with
`host/cam.py --rot 0`. `FT_MOUNT_ROT` is `CAM_ROT_0`, so a `-hi.png` here is
byte for byte the frame the board handed its own encoder.

**This set predates the `queries.txt` convention** and holds two pairs in one
directory, which is why there is no such file here. `shoot.sh` now takes
`SET CLASS ROUND` and reads the pair from `bench/stills/<SET>/queries.txt`; one
set is one pair. The pairs these four directories were shot under are the ones
named in the tables below.

    tea/          33   r1 11  r2 11  r3 11
    empty/        33   r1 11  r2 11  r3 11
    book-open/    11   r1 11
    book-closed/  11   r1 11
    logs/         the eight capture runs, ~500 KB each

**Rounds, alternating, not one long run of each scene.** Thirty consecutive tea
frames followed by thirty empty ones confounds the class with the AEC, the
daylight and the operator — the confound that made four glass benches
unreadable. Alternating means a "margin" that is really drift shows up as a
per-round sign that will not hold still, and the tool measures it as a null.

All eight runs printed `exposure settled after 6–8 frames` and no `scene:` or
`enrolment:` line, so #25's and #26's guards had nothing to say about any of
these frames. That check is why the guards were worth building.

## What it found

    uv run --script tools/probe_bisect.py --a tea --b empty \
        --pos "a glass with tea" --neg "an empty glass"

`bisect-glass.log` and `bisect-book.log` are those two runs. Both quote the
board's own quantity: `z = (cos − background)/std` per query, differenced. The
std matters and a raw `cos_A − cos_B` is the wrong number — it hands the vote to
whichever query happens to swing more.

| pair | teacher 1152 | pca 512 | student fp32 | student &#124;sep&#124; |
| --- | --- | --- | --- | --- |
| `an opened book` / `a closed book` | 26.0 sd | 24.1 sd | **8.2 sd** | 1.000 |
| `a glass with tea` / `an empty glass` | 7.9 sd | 5.4 sd | **0.2 sd** | 0.533 |

Effect size, not AUC, because AUC saturates: the teacher reads 1.000 on the
glass pair and so does its own drift null, and two 1.000s that mean opposite
things are not a measurement. `sd` is pooled within-class-within-round.

**The axis is lost at the student, and only at the student.** Two candidates die
on the teacher row alone:

- **It is not the resolution.** The teacher was fed these same 128×128 PNGs —
  upscaled to its own input size, which adds nothing — and read 7.9 sd. "128×128
  does not resolve the fill state" was on
  [#23](https://github.com/kazunori279/fpga-open-vocab/issues/23)'s list and is
  now off it.
- **It is not the projection, so the cheap fix is not a fix.** #24 called
  refitting the 1152→512 basis on a bank with fill-state contrasts "the cheapest
  possible fix". The basis passes 5.4 sd through. There is nothing there to
  recover.

## The book control is what makes the student row readable

Shot in the same session, same camera, same script, same rounds machinery, on
the pair the board scores 96.7% and 98.3% on. It is the answer to the only
serious objection to the table above — that a student row reading 0.2 sd might
mean *the way this tool measures students* is broken.

It is not. The student carries the book axis at 8.2 sd and the glass axis at
0.2 sd, which is **0.8 sd below that pair's own round-to-round drift**. This is
not a student that scores everything low.

The control is one round, so it has no drift null of its own. It did not need
one — nothing about it is surprising.

## Two things this does *not* show

Both are worth more than the table.

**The difference is not gone from the student.** The held-out oracle — the best
fitted direction, scored on a round it was not fitted on — reads 1.000 for the
glass pair at every stage, the student included. The student's embedding does
move between the two scenes. What it does not do is move along the direction the
*text query* points at: the class-mean difference has cosine **+0.031** with the
teacher's, against **+0.158** for the book pair. Both are small, because the
student's geometry is its own; the ratio is the signal.

**And the oracle is not evidence of a bound concept**, because mean frame luma
separates the glass pair at AUC 1.000 on its own — 108 against 133, tea being
darker, and the capture logs' exposure ramps settling twenty counts apart. It
separates the book pair too. Any encoder will "hold the difference" between two
sets of images that differ in brightness. The oracle rules out *the student threw
the frames away*; it does not rule in *the student knows what tea is*.

So the question moves from capacity to distillation: the student has the frames
apart and puts them apart in the wrong direction. **Nothing here supports "the
model is too small"**, which was the reading the four glass benches invited.

## The fourth stage is not run

int4 is #24's last row and it is deliberately missing. The fp32 student already
loses the axis, so a quantisation result cannot change the verdict — it can only
say how much further down an already-flat number goes. Running it needs the COCO
calibration loader (`model/data/train2017` is present, so it is available when
there is a reason).

## A cosine that was measured, believed, and thrown out

Per-frame `cos(student, teacher)` on these stills is 0.475, which reads like a
collapse and is not one. `bench/cue` frames from runs where the board scored
100% read **0.428**, and a *constant* vector scores 0.957 / 0.841 on the same two
sets. `config.json` has `constant_cosine` 0.643 against `best_cosine` 0.672. The
number is dominated by the shared cone direction and says nothing about any
axis. A difference of class means cancels the cone; a per-frame cosine does not.

It is out of the tool's output and the reasoning is kept as a comment in
`tools/probe_bisect.py`, because it is the kind of number that will look
convincing again.
