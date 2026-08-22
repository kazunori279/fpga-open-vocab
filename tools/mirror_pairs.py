#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Build a left/right contrast pair by mirroring real photographs.

    uv run --script tools/mirror_pairs.py \
        --out bench/stills/20260821-envelope-position \
        --cat book \
        --pos "a book on the left" --neg "a book on the right"

Writes `<out>/queries.txt`, `<out>/pos/*.png` and `<out>/neg/*.png` at 128x128 -
the same shape `tools/probe_bisect.py` reads from a shot or generated set.

WHY THIS IS NOT synth_pairs.py
-------------------------------
`tools/synth_pairs.py` builds a pair by editing one photograph into both of its
states, and every instruction it sends carries a clause forbidding the generator
to move anything: *"the same physical object at the same position and the same
size in the frame."* That clause is the reason its pairs are readable at all.

Position is the one axis that clause forbids. Asking a generator to move the
object to the other side of the frame is asking it to break the invariant the
tool is built on, and what comes back is a re-composed scene - the failure mode
that cost a third of the first four sets.

Mirroring needs no generator. **The two sides of a pair are the same photograph
and its horizontal flip**, so the lighting, the object, the room, the noise and
the JPEG history are not merely matched, they are identical to the pixel. The
only thing that differs is which side of the frame the object is on. There is no
confound left to argue about, which is what makes a null here worth quoting.

THE MIRROR CUE, AND WHY THE SET IS COUNTERBALANCED
---------------------------------------------------
A flipped photograph is detectable: lettering reads backwards, faces and hands
are wrong-handed. If every positive were a flip and every negative an original,
an encoder could score the set on *was this mirrored* and never look at the
object.

So the window is cut in one of two ways, alternating down the source list. Half
the sources are framed with the object at `--place` from the left, and those
contribute their original to `pos` and their flip to `neg`. The other half are
framed with it at `1 - place`, and contribute the other way round. **"Is this
image mirrored" therefore carries exactly zero information about the label**,
and the two halves are equal by construction rather than by luck. The counts are
printed and `sources.txt` records, per stem, which way its window was cut, so
the balance is checkable after the fact rather than promised here.

WHAT COUNTS AS "ON THE LEFT"
-----------------------------
The frame is not the photograph. It is a square window of `--scale` times the
object's long side, positioned so the object's centroid lands at `--place`
across it - so the object is a known fraction of the frame and a known distance
from its edge, and neither is left to what the photographer happened to do.

Cutting the window is what makes the set possible at all. The first draw used
the maximal centred square of each source instead, and COCO objects are small in
it: a `book` box of 60 source pixels in a 480-pixel square is sixteen pixels
once the square is squeezed into 128. Twenty pairs came back and they were
twenty photographs of a room.

A photograph is admitted only when the category has *exactly one* instance in it
(a stack of books annotated as six is not "a book"), the window fits inside the
source without clamping the object away from its mark by more than `--tol`, and
the box clears `--min-side` source pixels so the crop is not an upsample.

The label is COCO's own box, not anybody's eye, so the ground truth here is an
annotation - and unlike a generated set there is nothing to screen blind,
because no pair was invented.

WHAT A NULL WOULD MEAN
-----------------------
This asks whether the encoder carries left/right at all. If the teacher cannot
separate a photograph from its own mirror image under these two phrases, then no
amount of distillation, resolution or enrolment downstream will put the axis
there, and the appliance should say so on the page where people choose a
contrast rather than after they have spent a morning benching one.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

VAL = Path("model/data/val2017")
INST = Path("model/data/annotations/instances_val2017.json")


def window(im: dict, bbox: list[float], scale: float, place: float,
           tol: float) -> tuple[int, int, int, int] | None:
    """A square window holding the box, with its centroid at `place` across.

    Returns None when the source is too small to hold the window, or when
    pushing the window inside the frame would drag the object more than `tol`
    off its mark - a clamped window quietly re-centres the object, which is the
    one thing this set is measuring.
    """
    x, y, w, h = bbox
    side = scale * max(w, h)
    if side > min(im["width"], im["height"]):
        return None
    cx, cy = x + w / 2, y + h / 2
    x0 = min(max(cx - place * side, 0.0), im["width"] - side)
    y0 = min(max(cy - side / 2, 0.0), im["height"] - side)
    if abs((cx - x0) / side - place) > tol:
        return None
    return (int(x0), int(y0), int(x0 + side), int(y0 + side))


def candidates(cat: str, min_side: int, scale: float, place: float, tol: float,
               skip: set[str],
               ) -> list[tuple[str, Path, dict, list[float]]]:
    """(stem, path, image meta, bbox) for every source with one usable instance.

    Where the window goes is decided by the caller, alternating down this list,
    so the ordering here is by COCO id and is the same on every run.
    """
    if not INST.exists():
        sys.exit(f"no {INST} - run from the repo root")
    d = json.load(INST.open())
    ids = {c["name"]: c["id"] for c in d["categories"]}
    if cat not in ids:
        sys.exit(f"not a COCO category: {cat}\n  have: {', '.join(sorted(ids))}")
    want = ids[cat]
    meta = {i["id"]: i for i in d["images"]}

    # Every instance, not just the big ones: the exactly-one test below has to
    # see the small ones too, or a stack of books passes as a book.
    boxes: dict[int, list[list[float]]] = {}
    for a in d["annotations"]:
        if a["category_id"] == want:
            boxes.setdefault(a["image_id"], []).append(a["bbox"])

    out = []
    for iid in sorted(boxes):
        if len(boxes[iid]) != 1:
            continue
        bbox = boxes[iid][0]
        im = meta[iid]
        p = VAL / im["file_name"]
        if not p.exists() or p.stem in skip:
            continue
        # The pixel floor is the one thing the window cannot fix: below it the
        # crop is an upsample and the pair measures the blur.
        if max(bbox[2], bbox[3]) < min_side:
            continue
        # Admit only if it can be framed both ways, so the alternation below is
        # free to assign either and the two halves are drawn from one pool.
        if window(im, bbox, scale, place, tol) is None:
            continue
        if window(im, bbox, scale, 1.0 - place, tol) is None:
            continue
        out.append((p.stem, p, im, bbox))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cat", default="book", help="COCO category, exactly one per image")
    ap.add_argument("--pos", required=True, help="the phrase for the left side")
    ap.add_argument("--neg", required=True, help="the phrase for the right side")
    ap.add_argument("--min-side", type=int, default=0,
                    help="floor on the box's long side in source pixels; 0 "
                         "derives the floor at which the window stops being "
                         "an upsample, which is size/scale")
    ap.add_argument("--scale", type=float, default=2.6,
                    help="window side as a multiple of the box's long side - "
                         "the object is 1/scale of the frame")
    ap.add_argument("--place", type=float, default=0.25,
                    help="where the centroid sits across the window; the other "
                         "half of the set is framed at 1 - place")
    ap.add_argument("--tol", type=float, default=0.05,
                    help="how far the window may be clamped off that mark")
    ap.add_argument("--skip", type=Path, nargs="*", default=(),
                    help="sets whose photographs this one may not reuse")
    ap.add_argument("-n", type=int, default=30, help="pairs, before balancing")
    ap.add_argument("--size", type=int, default=128)
    ap.add_argument("--sources-only", action="store_true",
                    help="report the yield and write nothing")
    args = ap.parse_args()

    skip: set[str] = set()
    for s in args.skip:
        d = s / "pos" if (s / "pos").is_dir() else s
        skip |= {f.stem for f in d.glob("*.png")}

    # A window smaller than --size is an upsample, and the pair would measure
    # the interpolation rather than the object. That threshold is arithmetic,
    # not a preference, so it is derived unless somebody overrides it.
    floor = args.min_side or int(args.size / args.scale) + 1
    cands = candidates(args.cat, floor, args.scale, args.place, args.tol, skip)
    # Alternating, then truncated to an even count: the two framings are equal
    # in number whatever -n and the yield happen to be.
    k = min(len(cands), args.n) // 2 * 2
    print(f"sources   : {len(cands)} admissible -> {k} pairs, "
          f"{k // 2} framed left and {k // 2} framed right")
    if k == 0:
        sys.exit("nothing usable - lower --min-side or --scale, or raise --tol")
    chosen = [(c, "left" if i % 2 == 0 else "right")
              for i, c in enumerate(cands[:k])]
    if args.sources_only:
        for (stem, _, _, _), where in chosen:
            print(f"  {stem}  {where}")
        return 0

    pos, neg = args.out / "pos", args.out / "neg"
    pos.mkdir(parents=True, exist_ok=True)
    neg.mkdir(parents=True, exist_ok=True)
    args.out.joinpath("queries.txt").write_text(f"{args.pos}\n{args.neg}\n")

    lines = [("# stem  where-the-window-put-the-object  (pos is always the "
              "left-facing side, so 'right' means pos is the mirrored one)")]
    for (stem, p, meta, bbox), where in chosen:
        at = args.place if where == "left" else 1.0 - args.place
        box = window(meta, bbox, args.scale, at, args.tol)
        im = Image.open(p).convert("RGB").crop(box).resize(
            (args.size, args.size), Image.BICUBIC)
        flip = im.transpose(Image.FLIP_LEFT_RIGHT)
        a, b = (im, flip) if where == "left" else (flip, im)
        a.save(pos / f"{stem}.png")
        b.save(neg / f"{stem}.png")
        lines.append(f"{stem}  {where}")
    args.out.joinpath("sources.txt").write_text("\n".join(lines) + "\n")
    print(f"wrote     : {k} pairs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
