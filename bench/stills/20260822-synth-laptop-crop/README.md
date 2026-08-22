# An opened laptop against a closed one, generated

Square crops of COCO `laptop` boxes out of val2017, edited into both states by
`gemini-3-pro-image` and downsampled to 128×128. This was the pilot run for the
eight-contrast fleet, so it is the one set with no `--skip` list behind it.
Twenty-eight of thirty survived generation and twenty-three a blind screen;
`keep.txt` names them and the five it dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-laptop-crop \
    --cat laptop --min-side 120 -n 30 \
    --pos "an opened laptop" --neg "a closed laptop" \
    --pos-edit "the laptop is fully open, its screen raised upright and clearly visible" \
    --neg-edit "the laptop is completely closed, the lid shut flat down onto the keyboard"
```

## Read this set with a discount

**The editor mostly could not close a laptop.** Two failure modes, both from
the judges:

*Seven pairs are not closed at all* — `000000009400`, `000000022371`,
`000000027620`, `000000032610`, `000000051610`, `000000080949`,
`000000119233`. Both halves stand geometrically open and the only difference is
whether the screen is lit or filled with a black or white plate. The model read
"closed" as "screen off". Three of those were near-identical enough that the
judges split and the pair dropped on `side`; the rest are in `keep.txt`.

*About ten pairs paste a lid* — a flat panel floats over a chassis whose
keyboard, and sometimes whose lit screen, is still visible underneath. These do
read as closed, but a judge can pick the edited half off the seam rather than
off the state.

Twenty-three of twenty-eight sheets carry some shortcut; only five —
`000000014226`, `000000046031`, `000000077595`, `000000134112`, `000000148620` —
were clean to both judges.

The three fixed screen criteria stayed fixed; the cost of the survivors is
sensitivity, not bias, for the reason spelled out in
[`../20260822-synth-umbrella-crop/`](../20260822-synth-umbrella-crop/).

## Why this set is here

One of ten contrasts in a fleet; see [`../README.md`](../README.md#ten-contrasts-because-two-was-measuring-the-wrong-noise).
**No absolute number off a generated set means anything about the appliance** —
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

`object_both` is true on all twenty-eight; the two remaining drops are
`same_scene`, both a re-shot closed half at a different angle or zoom.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
