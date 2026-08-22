# An open oven against a closed one, generated

Square crops of COCO `oven` boxes out of val2017, edited into both states by
`gemini-3-pro-image` and downsampled to 128×128. Twenty-nine of thirty survived
generation and **only nineteen a blind screen** — the worst rate of the eight
contrasts shot this day. `keep.txt` names them and the ten it dropped.

```sh
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-oven-crop \
    --cat oven --min-side 120 -n 30 --skip <every earlier synth set> \
    --pos "an open oven" --neg "a closed oven" \
    --pos-edit "the oven door is pulled wide open, showing the empty rack inside" \
    --neg-edit "the oven door is completely shut"
```

## Why it screened out so hard: the category, then the editor

**COCO `oven` is largely not an oven door.** Eight pairs dropped on
`object_both`: `000000035326` and `000000138856` are cooktops with the oven out
of frame, `000000137294` is an outdoor barbecue smoker, and `000000243204`,
`000000246436`, `000000290768` have the oven tiny, occluded, or painted over. A
`--min-side 120` box guarantees a big *box*, not a big *door*, and for this
category those are different things.

On top of that, three pairs came back with **the two halves identical to the
pixel** apart from the A/B glyph the sheet draws (`000000035326`,
`000000064868`, `000000138856` by judge a) — the edit did nothing at all. A
fourth, `000000213086`, changed only a person's pose while the door stayed open
on both halves; its half-to-half difference is 24 %, which is a useful
counterexample to reading "large diff" as "the edit landed".

Judge b found a shortcut on twenty-one of twenty-nine, most often a whole-image
softness on the regenerated half — a judge can win on sharpness without ever
looking at the door.

## Why this set is here anyway

One of ten contrasts in a fleet; see [`../README.md`](../README.md#ten-contrasts-because-two-was-measuring-the-wrong-noise).
Nineteen pairs is a thin set, and it was the one of the ten most worth
regenerating from a hand-picked category before it is quoted on its own.

**That regeneration happened the same day and is in
[`../20260822-synth-oven-picked/`](../20260822-synth-oven-picked/README.md).**
Twenty-six sources picked off a contact sheet, 23 kept. It moved the teacher
from 0.850 to 0.957 and the student from 0.612 to 0.599 — so the objection this
paragraph was written to anticipate turns out to cost the student nothing. Both
sets stay: the pair of them is the measurement.
**No absolute number off a generated set means anything about the appliance** —
[`../20260822-synth-book-crop/`](../20260822-synth-book-crop/).

Verdicts in [`judge-a.json`](judge-a.json) and [`judge-b.json`](judge-b.json),
the blind key in [`key.json`](key.json).
