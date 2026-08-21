# An opened book against a closed book, second draw

The same recipe as [`../20260822-synth-book-crop/`](../20260822-synth-book-crop/)
on scenes that set never touched, so that a difference between two checkpoints
can be told apart from a difference between two draws.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-book-crop2 \
    --cat book --min-side 90 -n 30 \
    --skip bench/stills/20260822-synth-book-crop \
    --pos "an opened book" --neg "a closed book" \
    --pos-edit "the book is wide open, both pages visible with printed text" \
    --neg-edit "the book is completely shut, only its front cover visible"
```

**It asked for thirty and got fourteen.** `--skip` spent the first set's 27
sources, and val2017 has only fourteen more `book` boxes left at 90 px, which is
already below the 120 px the first draw used. A third independent book draw at
this size does not exist. That is a property of COCO, not of the pipeline, and
it is the reason the two crop sets are not the same size.

| | teacher 1152 | pca 512 | student fp32 | class axis |
| --- | --- | --- | --- | --- |
| all 14 pairs | 13/14, 1.4 sd | 13/14, 1.4 sd | 10/14, 0.2 sd | +0.005 |
| the 10 kept | 9/10, 1.3 sd | 9/10, 1.3 sd | 7/10, 0.7 sd | +0.058 |
| first draw, 18 kept | 18/18, 2.0 sd | 18/18, 2.0 sd | 12/18, 0.6 sd | +0.141 |

Four of fourteen dropped, three of them for the object being too small — which
is what 90 px buys. The teacher misses one pair even after screening; on the
first draw it missed none of eighteen.

## What the second draw is for

A sweep over checkpoints on one set of pixels reports differences, and until
now there was nothing to say which of them are real. Two draws of the same pair,
measured with the **same** checkpoint (`so400m-full-a05`, epoch 37), give the
spread that comes from the scenes alone:

| pair | draw 1, kept | draw 2, kept | moved by |
| --- | --- | --- | --- |
| book | 12/18 = 0.67, 0.6 sd, cos +0.141 | 7/10 = 0.70, 0.7 sd, cos +0.058 | 0.03, 0.1 sd, 0.083 |
| glass | 19/25 = 0.76, 0.9 sd, cos +0.263 | 16/23 = 0.70, 0.3 sd, cos +0.183 | 0.06, **0.6 sd**, 0.080 |

Nothing about the model changed between those two columns. The glass pair's
effect size moved 0.6 sd and its class-axis cosine 0.08 anyway.

## So the sweeps measured nothing

Both sweeps are archived beside the first draw
([`sweep-so400m.log`](../20260822-synth-book-crop/sweep-so400m.log),
[`sweep-sieve.log`](../20260822-synth-book-crop/sweep-sieve.log), and the same
two files under `../20260822-synth-glass-crop/`). Across five InfoNCE and RKD
settings the student rows span 0.0–0.4 sd and cos +0.024…+0.068 on the book
pair, 0.3–0.8 sd and cos +0.127…+0.315 on the glass pair. **Every one of those
spans is inside the draw-to-draw spread above.** `rkd-10`'s 16/18 on the book
pair is the kind of number that looks like a result and is not: it does not
survive into the glass set, where the same setting reads 18/25 against a
baseline of 17/25.

A generated set is a regression harness for the teacher, and the sign count on
it is stable to about ±0.05. It is not sensitive enough to rank distillation
settings, and no larger n fixes that — the variance is between draws of scenes,
not within one. Ranking distillation settings needs frames of one scene, which
means a camera.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json), the numbers in
[`bisect.log`](bisect.log), the kept list in [`keep.txt`](keep.txt).
