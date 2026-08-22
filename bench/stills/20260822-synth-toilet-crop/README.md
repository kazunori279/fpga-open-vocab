# A toilet with the lid up against the lid down, generated

Thirty square crops of COCO `toilet` boxes out of val2017, edited into both
states by `gemini-3-pro-image` and downsampled to 128×128. Twenty-five survived
a blind screen; `keep.txt` names them and the five it dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-toilet-crop \
    --cat toilet --min-side 120 -n 30 --skip <every earlier synth set> \
    --pos "a toilet with the lid up" --neg "a toilet with the lid down" \
    --pos-edit "the toilet seat and lid are both lifted upright" \
    --neg-edit "the toilet lid is closed flat down over the bowl"
```

## Why this set is here

Not to settle anything on its own. It is one of ten contrasts in a fleet, and
the fleet exists because the contrast-to-contrast spread turned out to be the
larger of the two variances in this eval — see
[`../README.md`](../README.md#ten-contrasts). **No absolute number off a
generated set means anything about the appliance**; the reason is in
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

## What the judges found

The best-preserved scenes of the eight. Judge b called `same_scene` true on all
thirty: background, camera and lighting hold across the edit, so almost nothing
here can be answered by framing. Only seven of thirty carry a shortcut at all,
against twenty-one on `oven` and twenty-three on `laptop`.

The drops are three `object_both` and one `same_scene`, plus `000000104803` on
`side` — a pair where the lid stayed up on both halves and the judges split.

One asymmetry worth carrying to the next generation run: **the failures are
almost all in the "lid down" direction.** Painting a flat closed lid is where
the model smears — a lid stretched into a white slab reaching the floor
(`000000061471`), a lumpy mass (`000000110042`), a wall flattened where the lid
was removed (`000000157807`). Lifting the lid does not seem to break the same
way.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
