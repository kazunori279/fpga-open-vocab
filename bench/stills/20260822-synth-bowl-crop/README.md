# A bowl full of food against an empty bowl, generated

Thirty square crops of COCO `bowl` boxes out of val2017, edited into both states
by `gemini-3-pro-image` and downsampled to 128×128. Twenty-five survived a blind
screen; `keep.txt` names them and the five it dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-bowl-crop \
    --cat bowl --min-side 120 -n 30 --skip <every earlier synth set> \
    --pos "a bowl full of food" --neg "an empty bowl" \
    --pos-edit "the bowl is heaped full of food, filled to the rim" \
    --neg-edit "the bowl is completely empty, clean and bare, nothing in it"
```

## Why this set is here

Not to settle anything on its own. It is one of ten contrasts in a fleet, and
the fleet exists because the contrast-to-contrast spread turned out to be the
larger of the two variances in this eval — see
[`../README.md`](../README.md#ten-contrasts-because-two-was-measuring-the-wrong-noise). **No absolute number off a
generated set means anything about the appliance**; the reason is in
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

It is also the nearest generated relative of the desk contrast the appliance is
actually built around, `a glass with tea` / `an empty glass`: a fill state on a
vessel rather than a hinge on a rigid object. That makes it the one set in the
fleet whose *direction* of result is worth reading against
[`../20260822-synth-glass-crop/`](../20260822-synth-glass-crop/).

## What the judges found

The heaviest shortcut load of the fleet after `oven`: eighteen of thirty by
judge a, twenty-two by judge b, leaving roughly eight pairs both judges read as
clean. Emptying a bowl means inpainting whatever the food was resting against,
so the empty side is usually the visibly repainted one.

The five drops are three `object_both` and three `same_scene` (`000000002149`
failed both).

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
