# An unmade bed against a neatly made one, generated

Square crops of COCO `bed` boxes out of val2017, edited into both states by
`gemini-3-pro-image` and downsampled to 128×128. Twenty-nine of thirty survived
generation and twenty-two a blind screen; `keep.txt` names them and the seven it
dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-bed-crop \
    --cat bed --min-side 120 -n 30 --skip <every earlier synth set> \
    --pos "an unmade bed" --neg "a neatly made bed" \
    --pos-edit "the bed is unmade, its sheets and blanket rumpled and thrown back in a heap" \
    --neg-edit "the bed is neatly made, the blanket pulled smooth and flat and the pillows squared"
```

## The crop is the problem here

All seven drops are `object_both`, six of them for that alone. A COCO `bed` box
is usually most of the frame, so cropping to it and downsampling to 128 leaves a
field of fabric with no headboard, no floor and no room — the judges could not
tell they were looking at a bed rather than a texture. Every other contrast in
the fleet crops to an *object in a room*; this one crops to a surface.

There is also a set-wide shortcut the judges both named: **the edit always adds
rumpled cloth rather than smoothing it.** So the unmade half is reliably the one
with more stuff in it, and that is answerable without reading state. It inflates
every checkpoint equally and so largely cancels in the paired comparison the
fleet is for, which is the whole reason the fleet does not quote absolute
numbers — see [`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

Of the ten contrasts this is the one whose *contrast phrasing* is least like the
appliance's: `unmade` / `neatly made` is a property of an arrangement rather than
a state of a rigid object with two configurations. It is kept because dropping a
contrast for looking hard would bias the fleet toward the easy ones.

## Why this set is here

One of ten contrasts; see [`../README.md`](../README.md#ten-contrasts-because-two-was-measuring-the-wrong-noise).

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
