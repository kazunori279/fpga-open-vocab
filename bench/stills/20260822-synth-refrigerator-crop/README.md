# An open refrigerator against a closed one, generated

Thirty square crops of COCO `refrigerator` boxes out of val2017, edited into
both states by `gemini-3-pro-image` and downsampled to 128×128. Twenty-four
survived a blind screen; `keep.txt` names them and the six it dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-refrigerator-crop \
    --cat refrigerator --min-side 120 -n 30 --skip <every earlier synth set> \
    --pos "an open refrigerator" --neg "a closed refrigerator" \
    --pos-edit "the refrigerator door is wide open, showing the shelves inside" \
    --neg-edit "the refrigerator door is completely shut"
```

## Why this set is here

One of ten contrasts in a fleet, and the fleet exists because the
contrast-to-contrast spread turned out to be the larger of the two variances in
this eval — see [`../README.md`](../README.md#ten-contrasts-because-two-was-measuring-the-wrong-noise). **No absolute
number off a generated set means anything about the appliance**; the reason is
in [`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

## What the judges found

Closing the door is where this one breaks. Half the set — fifteen sheets by
judge b — carries a shortcut, and the commonest by far is a *closed* door
rendered as a texture-free white slab whose edges do not line up with the door
frame: `000000024243`, `000000028452`, `000000073326`, `000000117908`,
`000000144003`, `000000215114`.

Six of those go further and leave the shelves visible behind the slab, so both
halves read as open. Where that made the judges disagree the pair dropped on
`side`; where it did not, it stays in `keep.txt` and costs sensitivity rather
than correctness, for the reason spelled out in
[`../20260822-synth-umbrella-crop/`](../20260822-synth-umbrella-crop/).

Three `object_both` drops: `000000127182` is a dishwasher, not a refrigerator,
and `000000117908` and `000000144003` lose the appliance entirely on one half.
Three `same_scene` drops, two of them a tight crop against a wide shot.

Against that, ten or more pairs are clean — the same scene with nothing but the
door changed, `000000030213` (an old icebox), `000000187271`, `000000273712`,
`000000282296` among them.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
