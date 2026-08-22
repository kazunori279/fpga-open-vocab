# An open oven against a closed one, from hand-picked sources

The regeneration [`../20260822-synth-oven-crop/`](../20260822-synth-oven-crop/README.md)
asked for. That set kept 19 of 29 pairs — the worst rate of the ten contrasts
— because COCO `oven` boxes are largely cooktops, barbecues and occluded
fronts, and `--min-side` cannot tell a big *box* from a big *door*. This set
picks the sources by eye first.

```sh
# 1. dump every oven box in val2017 above the floor, with a contact sheet
uv run --script tools/synth_pairs.py --out /tmp/ovensrc --cat oven \
    --min-side 100 -n 200 --sources-only --pos x --neg y --pos-edit x --neg-edit y

# 2. look at the 76 crops, keep the 26 that show an oven door, large,
#    whichever state it is in.  Their stems go in a file.

# 3. generate from those and nothing else
uv run --script tools/synth_pairs.py --out bench/stills/20260822-synth-oven-picked \
    --cat oven --min-side 100 --only oven-picked.txt \
    --pos "an open oven" --neg "a closed oven" \
    --pos-edit "the oven door is pulled wide open, showing the empty rack inside" \
    --neg-edit "the oven door is completely shut"
```

**The source's own state does not matter and the picking rule says so.** Both
halves are generated from the same crop, so a source photographed with the
door already open is as good a source as one shot closed — three of the 26 are.
What is picked for is the door being *in the frame and large enough to edit*.

## The screen

26 generated, 26 paired, **23 kept**, against 19 of 29 before. No pair was
named backwards. The three drops:

| pair | why |
| --- | --- |
| `000000410487` | both halves show the door down; one cavity is repainted a flat white slab |
| `000000429598` | the two halves are indistinguishable — the edit landed on neither |
| `000000485424` | the source is motion-blurred and the door state is not readable on either half |

Three shortcuts are recorded in `judge.json` and were not disqualifying, which
is how the earlier set treated them too: `…1036` is softer over the whole
frame on one half rather than just at the door, `…6497` reframes the range
smaller and further back, and `…0930` adds a hand and a tray that are in
neither the source nor the prompt.

**This set did not go through `tools/synth_sheet.py` and `tools/synth_keep.py`,
and that is a real difference from the other ten.** Those two enforce the
criterion in code: two judges, independently, unanimous on all three of side /
`object_both` / `same_scene`. Here the A/B sheets and `key.json` were built ad
hoc and there was **one screening pass, not two**, so `keep.txt` is hand-written
to the same three criteria without the unanimity rule — hence `judge.json`
rather than `judge-a.json`, which would have let `synth_keep.py` run on half a
panel. The side call, the part with a wrong answer, was made blind against a
key written before the sheets were looked at, and came back 23 of 23 on the
pairs with a readable state. The keep rates are therefore **not exactly
like-for-like**: a one-judge screen is looser than a unanimous two. 88% against
65% is far more than that gap can account for, so read the direction as solid
and do not quote the four points either way.

## What it bought, and what it did not

| | teacher 1152 | pca 512 | student fp32 | axis |
| --- | --- | --- | --- | --- |
| `…-oven-crop` (19 pairs) | 0.850 | 0.870 | 0.612 | 0.305 |
| `…-oven-picked` (23 pairs) | **0.957** | **0.949** | 0.599 | 0.351 |

`sep` is the pooled cross-scene AUC. **Cleaning the set moved the teacher by
+0.107 and the student by −0.013.** Within scene the teacher now gets 23 of 23
right way round; the student gets 13 of 23, which on a paired contrast is
barely off a coin.

That is the result worth keeping. The oven row was the one of the ten most
open to the objection "that number is low because the set is bad", and the set
*was* bad — 65% survival, cooktops in the sources, a category that does not
mean what its name means. Fixing all of it made the contrast nearly perfect
for the teacher and left the student exactly where it was. The drop widened
from −0.258 to −0.350 because the ceiling rose and the floor did not follow.

Pooled over all ten contrasts, substituting this row for the old one moves the
product metric from **0.6454 to 0.6441** — a set regeneration that cost an
afternoon and changed the shipped number by −0.001.

## Files

`pos/` and `neg/` are 128×128 PNGs, one pair per COCO stem. `keep.txt` names
the 23 and the 3 with reasons, `key.json` is which side held the positive in
the screening sheets, `judge.json` is the screen itself, `queries.txt` is the
two phrases. The stems that went into `--only` are the 26 filenames in `pos/`.

See [`../README.md`](../README.md#ten-contrasts-because-two-was-measuring-the-wrong-noise)
for the fleet this belongs to and
[`../20260822-axis-inheritance.log`](../20260822-axis-inheritance.log) for
what the `axis` column above is.
