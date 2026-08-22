# An open umbrella against a folded one, generated

Square crops of COCO `umbrella` boxes out of val2017, edited into both states by
`gemini-3-pro-image` and downsampled to 128×128. Twenty-nine of thirty survived
generation and twenty-five a blind screen; `keep.txt` names them and the four it
dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-umbrella-crop \
    --cat umbrella --min-side 120 -n 30 --skip <every earlier synth set> \
    --pos "an open umbrella" --neg "a folded umbrella" \
    --pos-edit "the umbrella is fully open, its canopy spread wide" \
    --neg-edit "the umbrella is folded shut and wrapped tight around its shaft"
```

## Read this set with a discount

**The editor often added a folded umbrella instead of folding the one that was
there.** Both judges independently described exactly that on five pairs that are
still in `keep.txt` — `000000074058`, `000000136633`, `000000232563`,
`000000250127`, `000000253742` — where the negative half shows a furled umbrella
pasted onto an arm, a chest or a pole while the open canopy above it is
untouched. The screen did not catch them because it asks three questions (did
the judge name the right side, is the object readable on both halves, is it the
same scene) and all three are satisfied: the judge *can* see which half was
edited, and names it correctly.

The three criteria were fixed before the set was shot, and adding a fourth after
reading the verdicts would be screening on what was seen. So the pairs stay, and
the effect is stated here instead:

**it costs power, not validity.** A negative frame that contains both states
gets a near-zero contrast margin rather than a wrong-signed one, so it pulls the
set's AUC toward 0.5. That happens identically for every checkpoint being
ranked, so a *paired* comparison across checkpoints is unbiased by it — just
less sensitive, as if the set were twenty pairs rather than twenty-five.

Several of the scenes are also outdoors with other people's open umbrellas in
frame, which does the same thing for the same reason.

## Why this set is here

One of ten contrasts in a fleet; see [`../README.md`](../README.md#ten-contrasts).
**No absolute number off a generated set means anything about the appliance** —
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

The four drops are two `side` and two `object_both`.

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
