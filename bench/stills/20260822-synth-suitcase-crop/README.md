# An open suitcase against a closed one, generated

Square crops of COCO `suitcase` boxes out of val2017, edited into both states by
`gemini-3-pro-image` and downsampled to 128×128. Twenty-nine of thirty survived
generation; **twenty-seven survived a blind screen**, the highest rate of the
eight contrasts shot this day. `keep.txt` names them and the two it dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-suitcase-crop \
    --cat suitcase --min-side 120 -n 30 --skip <every earlier synth set> \
    --pos "an open suitcase" --neg "a closed suitcase" \
    --pos-edit "the suitcase is unzipped and lying open, showing the clothes packed inside" \
    --neg-edit "the suitcase is zipped shut and standing closed"
```

## Why this set is here

Not to settle anything on its own. It is one of ten contrasts in a fleet, and
the fleet exists because the contrast-to-contrast spread turned out to be the
larger of the two variances in this eval — see
[`../README.md`](../README.md#ten-contrasts). **No absolute number off a
generated set means anything about the appliance**; the reason is in
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

## What the judges found

Both drops are `same_scene`: `000000127660` (one half is a different
photograph — extra models, a new pose, the whole composition replaced) and
`000000432468` (one half an extreme close-up against a wide shot, with
different background and lighting).

`object_both` is true on all twenty-nine for both judges — no suitcase vanished
or shrank out of readability, which is what sank `bed` and `oven`.

Six pairs carry a state-independent shortcut, all of the disappearance kind: a
luggage tag, a chalk mark, a child's hand on the lid, or a leather panel gone or
repainted on the edited side. One pair, `000000350019`, reads as closed on both
halves; both judges named the same side anyway, on the strength of the vanished
hand rather than the state, so it is in `keep.txt` on a weak basis.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
