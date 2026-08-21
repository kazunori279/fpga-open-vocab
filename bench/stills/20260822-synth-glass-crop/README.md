# A glass with tea against an empty glass, generated

Thirty square crops of COCO `cup` and `wine glass` boxes out of val2017, edited
into both states by `gemini-3-pro-image` and downsampled to 128×128.
Twenty-five survived a blind screen; `keep.txt` names them and the five it
dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-glass-crop \
    --cat "cup,wine glass" --min-side 120 -n 30 \
    --pos "a glass with tea" --neg "an empty glass" \
    --pos-edit "the glass is filled to the top with dark brown tea" \
    --neg-edit "the glass is completely empty, clean and dry, nothing in it"
```

| | teacher 1152 | pca 512 | student fp32 |
| --- | --- | --- | --- |
| all 30 pairs | 30/30, 1.8 sd | 30/30, 1.8 sd | 22/30, 0.9 sd |
| the 25 kept | **25/25, 1.8 sd** | 25/25, 1.9 sd | 19/25, 0.9 sd |
| real stills, one desk | 7.9 sd | 5.4 sd | **0.2 sd, AUC 0.533** |

The argument for reading these rows the way they are read, and the reason the
last column cannot be compared to a bench, is in the control set's README:
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/). In short, the
student reads *higher* here than on the pair the board carries perfectly, which
is the ordering upside down, so no student row from a generated set means
anything about the appliance.

## The one thing this set did settle

[#28](https://github.com/kazunori279/fpga-open-vocab/issues/28) was closed with
a caveat: on real stills the mean frame luma separates tea from empty at AUC
1.000, so the teacher's perfect score there proved nothing about whether SigLIP
binds *fill state* or merely brightness. Here the luma cue is at **AUC 0.658**
and the teacher is still at 25 out of 25. Both judges were asked specifically
whether the amber spread past the rim, and flagged it on three pairs of thirty.

**The teacher binds fill state.** It is not reading the lamp. The distinction
is present at 1152-d and at 512-d and is lost at the student, exactly as
[`../20260821-bisect/`](../20260821-bisect/) concluded, and now without the
photometric escape hatch.

## Also worth knowing

COCO `cup` is mostly mugs and paper cups, so `--cat "cup,wine glass"` gives a
set of *vessels*, not the tea glass on the desk. That widens the question again
in the direction this whole family of sets already widens it. It does not
affect the teacher verdict above, which is about a contrast rather than an
object.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json), the numbers in
[`bisect.log`](bisect.log). The judges named the filled side correctly on 29
and 30 of 30 pairs and agreed with each other on all 30.
