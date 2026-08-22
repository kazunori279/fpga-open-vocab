# A book on the left against a book on the right

The position row of [#23](https://github.com/kazunori279/fpga-open-vocab/issues/23)'s
envelope map — *which distinctions does the encoder simply not carry?* — staged
on 2026-08-21 and measured on 2026-08-22.

```sh
uv run --script tools/mirror_pairs.py \
    --out bench/stills/20260821-envelope-position --cat book \
    --pos "a book on the left" --neg "a book on the right"
```

Ten pairs. Each `neg/x.png` is `pos/x.png` flipped left to right, so **the two
sides of a pair are the same photograph** — same room, same lamp, same noise,
same JPEG history — and the only thing that differs is which side of the frame
the book is on. There is no confound left to argue about, which is the point.

| stage | cross-scene AUC | fitted, held out by scene | within-scene, right way round |
| --- | --- | --- | --- |
| teacher 1152 | 0.510 | 0.30 | 6/10 |
| pca 512 | 0.520 | 0.30 | 5/10 |
| student fp32 | 0.450 | 0.75 | 3/10 |

**Read none of those numbers on their own.** Ten scenes over five folds is two
scenes a fold, which is why the fitted column swings 0.30 to 0.75 while the
teacher sits at chance — that column is noise at this n, not a finding. The
answer this set is part of is the eleven-contrast one in
[`../README.md`](../README.md#left-and-right-and-the-axis-that-is-not-there):
teacher 0.499 ± 0.004 over 278 pairs.

## Why the set is this small, and why that is the tool's fault and not COCO's

`mirror_pairs.py` admits a photograph only when COCO annotates **exactly one**
instance of the category in it — a stack of books labelled as six is not "a
book" — and when the box is large enough that a 2.6× window around it is not an
upsample at 128. val2017 has eleven `book` photographs that clear both.

The first draw of this set had twenty pairs and was worthless. It framed each
source with its maximal centred square, and a COCO `book` box of 60 pixels in a
480-pixel square is sixteen pixels once the square is squeezed into 128. The
contact sheet was twenty photographs of a room with no book findable in any of
them. Cutting a window around the object instead is what the tool does now, and
it is why ten pairs is the honest yield rather than twenty.

## What was staged here and is not measured

`f8a1ef0` staged four envelope sets. This is the one that is done. `count`
(`three books` / `one book`) is still a `queries.txt` and needs a different
instrument again: mirroring cannot change a count, and `synth_pairs.py` cannot
either without dropping the clause — *"the same objects around it"* — that makes
its pairs readable at all. `bowl` and `laptop` were overtaken by
[`../20260822-synth-bowl-crop/`](../20260822-synth-bowl-crop/) and
[`../20260822-synth-laptop-crop/`](../20260822-synth-laptop-crop/), which measure
the same two axes on thirty scenes each.
