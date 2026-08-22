# `a bowl on the left` against `a bowl on the right`

One column of the mirror fleet. **The discussion is in
[`../README.md`](../README.md#left-and-right-and-the-axis-that-is-not-there)** —
this file exists so the set carries its own numbers, and there is nothing
particular to say about this contrast that is not true of the other ten.

```sh
uv run --script tools/mirror_pairs.py \
    --out bench/stills/20260822-mirror-bowl --cat "bowl" \
    --pos "a bowl on the left" --neg "a bowl on the right" \
    --skip <every mirror set before it>
```

18 pairs. Each `neg/x.png` is `pos/x.png` flipped, so the two sides are the same
photograph and `sources.txt` records which of them the source was.

| stage | cross-scene AUC | fitted, held out by scene | within-scene, right way round |
| --- | --- | --- | --- |
| teacher 1152 | 0.515 | 0.47 | 12/18 |
| pca 512 | 0.503 | 0.49 | 10/18 |
| student fp32 | 0.420 | 0.67 | 5/18 |

Frame-mean luma reads exactly 0.500, as it must: a mirror image has the same
mean. That is the set's own check that the stimulus carries no global cue.

Scored in [`../20260822-mirror-11contrast.log`](../20260822-mirror-11contrast.log).
