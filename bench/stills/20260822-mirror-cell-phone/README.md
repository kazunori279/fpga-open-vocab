# `a cell phone on the left` against `a cell phone on the right`

One column of the mirror fleet. **The discussion is in
[`../README.md`](../README.md#left-and-right-and-the-axis-that-is-not-there)** —
this file exists so the set carries its own numbers, and there is nothing
particular to say about this contrast that is not true of the other ten.

```sh
uv run --script tools/mirror_pairs.py \
    --out bench/stills/20260822-mirror-cell-phone --cat "cell phone" \
    --pos "a cell phone on the left" --neg "a cell phone on the right" \
    --skip <every mirror set before it>
```

30 pairs. Each `neg/x.png` is `pos/x.png` flipped, so the two sides are the same
photograph and `sources.txt` records which of them the source was.

| stage | cross-scene AUC | fitted, held out by scene | within-scene, right way round |
| --- | --- | --- | --- |
| teacher 1152 | 0.509 | 0.56 | 18/30 |
| pca 512 | 0.509 | 0.55 | 16/30 |
| student fp32 | 0.484 | 0.48 | 12/30 |

Frame-mean luma reads exactly 0.500, as it must: a mirror image has the same
mean. That is the set's own check that the stimulus carries no global cue.

Scored in [`../20260822-mirror-11contrast.log`](../20260822-mirror-11contrast.log).
