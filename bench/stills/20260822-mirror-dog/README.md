# `a dog on the left` against `a dog on the right`

One column of the mirror fleet. **The discussion is in
[`../README.md`](../README.md#left-and-right-and-the-axis-that-is-not-there)** —
this file exists so the set carries its own numbers, and there is nothing
particular to say about this contrast that is not true of the other ten.

```sh
uv run --script tools/mirror_pairs.py \
    --out bench/stills/20260822-mirror-dog --cat "dog" \
    --pos "a dog on the left" --neg "a dog on the right" \
    --skip <every mirror set before it>
```

20 pairs. Each `neg/x.png` is `pos/x.png` flipped, so the two sides are the same
photograph and `sources.txt` records which of them the source was.

| stage | cross-scene AUC | fitted, held out by scene | within-scene, right way round |
| --- | --- | --- | --- |
| teacher 1152 | 0.495 | 0.49 | 9/20 |
| pca 512 | 0.497 | 0.47 | 9/20 |
| student fp32 | 0.480 | 0.38 | 9/20 |

Frame-mean luma reads exactly 0.500, as it must: a mirror image has the same
mean. That is the set's own check that the stimulus carries no global cue.

Scored in [`../20260822-mirror-11contrast.log`](../20260822-mirror-11contrast.log).
