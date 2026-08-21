# Open book against closed book, generated — the control, and what it caught

Twenty-seven square crops of COCO `book` boxes out of val2017, each edited by
`gemini-3-pro-image` into both states and downsampled to 128×128. Eighteen
survived a blind screen; `keep.txt` names them and lists what the other nine
failed on.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-book-crop \
    --cat book --min-side 120 -n 30 \
    --pos "an opened book" --neg "a closed book" \
    --pos-edit "the book is wide open, both pages visible with printed text" \
    --neg-edit "the book is completely shut, only its front cover visible"
uv run --script tools/synth_sheet.py bench/stills/20260822-synth-book-crop
#   ... two judges, blind, on /tmp/judge/<set>/*.png -> judge-a.json, judge-b.json
uv run --script tools/probe_bisect.py --paired --keep keep.txt \
    --a pos --b neg --pos "an opened book" --neg "a closed book"
```

**This pair is the control, not the question.** The appliance carries it: on
real stills of one desk it reads 26.0 sd at the teacher and **8.2 sd at the
student, AUC 1.000** ([`../20260821-bisect/`](../20260821-bisect/)). Anything a
generated set says about the student has to be checked against that number
before it is believed, and this set exists to do the checking.

## What it says

| | teacher 1152 | pca 512 | student fp32 |
| --- | --- | --- | --- |
| all 27 pairs | 26/27, 1.5 sd | 25/27, 1.5 sd | 18/27, 0.6 sd |
| the 18 kept | **18/18, 2.0 sd** | 18/18, 2.0 sd | **12/18, 0.6 sd** |
| real stills, one desk | 26.0 sd | 24.1 sd | 8.2 sd |

Read the within-scene rows in [`bisect.log`](bisect.log), not the AUCs above
them: the two sides are the same photograph, so the scene cancels and a sign
count is the honest statistic. The sd there is spread *across scenes*, which is
scene variety rather than repeat measurement.

**The screen works on the teacher.** Filtering moves it from 26/27 to 18 out of
18 and from 1.5 sd to 2.0, which is what a validity filter is supposed to do —
the pairs it drops are the ones where the generator changed nothing or swapped
the object, and those were dragging a real signal down.

**The screen does not rescue the student, and nothing here will.** 12 of 18 is
what a coin does about 24% of the time. The same encoder, on the same contrast,
on frames of one real desk, is at 8.2 sd and does not miss.

## Why, and why it is not a bug in the images

Set this beside its sibling [`../20260822-synth-glass-crop/`](../20260822-synth-glass-crop/),
which is the pair the appliance is known to **lose** — 0.2 sd and AUC 0.533 on
real stills. Generated, it reads **0.9 sd, 19/25**: *higher* than the pair the
board carries perfectly.

That ordering is upside down, it survives the screen, and it survives n = 18
and n = 25. So it is not image quality and not sample size. It is the question:

- eighteen different books on eighteen different desks asks whether **any** open
  book outranks **any** closed book — a scene-independent state axis;
- a bench asks about **one** book on **one** desk across frames.

A 1.4 M-parameter student can hold the second and not the first, and this set
is the evidence that it does exactly that. Which also means **a generated set
cannot rank two candidate pairs for the appliance**, because the ranking it
produces is not the appliance's.

## What it is still good for

The teacher row. `18/18` and `25/25` say SigLIP binds both contrasts, and
[`../README.md`](../README.md) already records the one asymmetry that matters:
nothing downstream can recover what the teacher never had. A generated set is a
cheap, propless **teacher-side** gate, and that is the whole of its remit.

The two judges named the positive side correctly on 26 of 27 pairs and agreed
with each other on 26 of 27 — the screen is repeatable, not one model's mood.
Their verdicts are in [`judge-a.json`](judge-a.json) and
[`judge-b.json`](judge-b.json), the ground truth they were graded against in
[`key.json`](key.json).

## Caveats that did not go away

`frame mean luma AUC 0.633` — above chance. Opening a book adds white paper, so
some of the teacher's 18/18 is available to a photometer. Smaller than the
0.556 the earlier sets managed and far below the 1.000 the real stills hit, but
not zero.

And the screen keeps the pairs a vision model finds legible, which is the
population the teacher is best at. The teacher's margin here is inflated by
construction and is not a number to quote anywhere else.
