# A glass with tea against an empty glass, second draw

Thirty more `cup` and `wine glass` crops out of val2017, none of them used by
[`../20260822-synth-glass-crop/`](../20260822-synth-glass-crop/), so the two
sets are independent draws of the same question.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-glass-crop2 \
    --cat "cup,wine glass" --min-side 120 -n 30 \
    --skip bench/stills/20260822-synth-glass-crop \
    --pos "a glass with tea" --neg "an empty glass" \
    --pos-edit "the glass is filled to the top with dark brown tea" \
    --neg-edit "the glass is completely empty, clean and dry, nothing in it"
```

| | teacher 1152 | pca 512 | student fp32 | class axis |
| --- | --- | --- | --- | --- |
| all 30 pairs | 30/30, 1.5 sd | 30/30, 1.5 sd | 19/30, 0.5 sd | +0.209 |
| the 23 kept | **23/23, 1.6 sd** | 23/23, 1.6 sd | 16/23, 0.3 sd | +0.183 |
| first draw, 25 kept | 25/25, 1.8 sd | 25/25, 1.9 sd | 19/25, 0.9 sd | +0.263 |

**The teacher replicates exactly**: 30 of 30 before screening and 23 of 23
after, on scenes the first draw never saw, with the luma cue at AUC 0.617.
That is the second independent confirmation that SigLIP binds fill state rather
than brightness, which was the caveat left on
[#28](https://github.com/kazunori279/fpga-open-vocab/issues/28).

**The student does not.** Its pooled cross-scene AUC is 0.605 here and 0.661 on
the first draw, against a teacher at 0.949 — the student barely carries fill
state from one room to another. The paired effect size on the same two draws
reads 0.9 sd then 0.3 sd, which is the statistic swinging rather than the model,
and the reason to read the cross-scene AUC instead is argued in the control
set's README, [`../20260822-synth-book-crop2/`](../20260822-synth-book-crop2/),
together with what the checkpoint sweeps say once that column is the one being
read.

## The screen caught something about itself

Seven of thirty dropped, six of them for the scene being re-composed: a glass
becoming a different glass, an object appearing on one side, the top of the
frame cropped differently. One went for `side` — a cup with cutlery standing in
it on one side and nothing on the other, tea on neither, which is a pair the
generator simply did not make.

The judges also flagged straws, lids and garnishes on one side only: a cue that
answers "which side is the drink" without anyone looking at the fill. Those
pairs are kept, because the object and the scene and the state are all correct
and dropping them would be selecting on how the teacher might succeed. It is
recorded here because it is the direction this set is biased in.

And one judge finished its thirty sheets and remarked that the sides looked
perfectly alternating — which they were, because `synth_sheet.py` flipped them
by index. It had not used that (14 A's against the key's 15), but a screen that
*can* be answered without looking fails silently when it is. The sides are now
assigned by a hash of the stem; the tool's docstring says why, and the
[`key.json`](key.json) archived here is the parity one these verdicts were
graded against.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the numbers in [`bisect.log`](bisect.log), the kept list in
[`keep.txt`](keep.txt).
