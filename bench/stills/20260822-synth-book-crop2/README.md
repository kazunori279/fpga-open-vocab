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

A sweep over checkpoints on one set of pixels reports differences, and a second
disjoint draw is what says which of them survive a change of scenes. Held to
the **same** checkpoint (`so400m-full-a05`, epoch 37), the two draws also say
which *statistic* is worth reading.

| statistic | book d1 → d2 | glass d1 → d2 |
| --- | --- | --- |
| `--paired`, within-scene effect size | 0.6 → 0.7 sd | 0.9 → **0.3 sd** |
| `--paired`, sign count | 0.67 → 0.70 | 0.76 → 0.70 |
| `sep`, pooled cross-scene AUC | 0.648 → 0.700 | 0.661 → 0.605 |

**Read `sep`, not the paired column.** The paired statistic subtracts the two
states of one scene, so the scene cancels — which is right on stills of one desk
and wrong here, because the whole point of thirty different rooms is to ask
whether the state survives them. Its effect size is also a mean over a handful
of heterogeneous scenes over their own spread, and it moved 0.6 sd on the glass
pair with the model held fixed. `sep` is the question a user actually has —
*is the book open, whatever else changed* — and it repeats to about ±0.05.

## What the sweeps say once the right column is read

Both sweeps now exist on both draws (`sweep-so400m.log`, `sweep-sieve.log`, in
this set and beside the first draw). Cross-scene AUC, draw 1 / draw 2:

| | book | glass |
| --- | --- | --- |
| teacher, SO400M | .907 / .940 | .933 / .949 |
| so400m 30k baseline | .565 / .540 | .600 / .590 |
| so400m + RKD 10 | **.685 / .630** | .565 / .616 |
| so400m + RKD 100 | .728 / .590 | .498 / .677 |
| teacher, ViT-B/16 | .700 / .700 | .888 / .888 |
| sieve baseline | .509 / .410 | .672 / .660 |
| + InfoNCE 0.3 | .562 / .500 | .610 / .618 |
| + InfoNCE 1.0 | .519 / .530 | .557 / .571 |
| + RKD 10 | **.574 / .570** | .610 / .590 |
| + InfoNCE 0.3 & RKD 10 | .596 / .520 | .664 / .658 |

**RKD 10 is worth about +0.10 AUC on the book pair**, in both draws and in both
model families, and **nothing on the glass pair** — where the baseline is
already the best row. RKD 100 swings .73/.59 and .50/.68 and is not the same
result with more of it; it does not replicate. InfoNCE alone never separates
from baseline.

An earlier reading of this set said the sweeps measured nothing. That was the
paired column talking, and it was wrong.

## The number that matters more than the ranking

The student sits near **0.6** where its teacher sits near **0.93**. Every
setting in the sweep is a rounding error against that gap. A generated set is
the harness for closing it, because scene-invariance is precisely what a set of
different scenes measures and a set of stills of one desk cannot.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json), the numbers in
[`bisect.log`](bisect.log), the kept list in [`keep.txt`](keep.txt).
