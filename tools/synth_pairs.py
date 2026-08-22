#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai", "pillow"]
# ///
"""Build a contrast pair by editing real photographs into both of its states.

    uv run --script tools/synth_pairs.py \
        --out bench/stills/20260822-synth-glass \
        --cat "cup,wine glass" \
        --pos "a glass with tea" --neg "an empty glass" \
        --pos-edit "the glass is filled to the top with dark brown tea" \
        --neg-edit "the glass is completely empty, clean and dry, nothing in it" \
        -n 30

Writes `<out>/queries.txt`, `<out>/pos/*.png` and `<out>/neg/*.png` at 128x128 -
the shape `tools/probe_bisect.py` reads and the size the board's camera hands
its encoder.

WHY THIS EXISTS
---------------
`bench/stills/shoot.sh` costs four minutes of desk time and, more to the point,
an object. Some contrasts cannot be staged at all on one desk. This one costs a
minute and no object, so a pair can be screened before anyone decides whether it
is worth owning the props for.

It also removes, by construction, the confound that made four glass benches
unreadable: both states come from the *same source photograph*, so the lighting,
the camera pose, the background and the operator are identical. Rounds exist in
`shoot.sh` to break that confound statistically. Here there is nothing to break.

BOTH SIDES ARE EDITED, AND THAT IS NOT AN ACCIDENT
--------------------------------------------------
The positive state is generated too, even when the source photograph already
shows it. If one side were the original JPEG and the other a render, an encoder
could separate the pair on *photograph against render* and score a perfect
margin without ever seeing the contrast. Both sides go through the same
generator, at the same size, so that difference cancels.

SOURCES ARE val2017, WHICH IS HELD OUT OF THE DISTILLATION
-----------------------------------------------------------
`model/distill.py` trains on train2017. Editing val2017 keeps the student from
being screened on pictures it was fitted to. The captions are only used to find
plausible source images; the generator is given the picture, not the caption.

A GENERATED PAIR IS NOT A VALID PAIR UNTIL SOMETHING HAS LOOKED AT IT
----------------------------------------------------------------------
The generator obeys the state clause and quietly ignores the rest often enough
that it has to be checked. On the first four sets it swapped the room, deleted
the object, or swapped a pint tumbler for a wine glass in a third to a half of
the pairs, and none of that is visible in the margins it produces - a
re-composed pair separates beautifully and measures nothing.

Screen every pair blind before measuring it: show the two sides as A and B,
with the sides alternating so there is no fixed answer, and ask which side
holds the positive state. Keep a pair only when the sides are named correctly,
the object is present and large in both, and the scene is the same. Record the
survivors in a `keep.txt` and hand it to `probe_bisect.py --keep`, so the
dropped pairs stay in the set and the filter stays auditable.

Do the screening *before* looking at any encoder output. Selecting on the
stimulus is a validity filter; selecting after seeing the margins is fitting.

THE THREE THINGS A SYNTHETIC PAIR CANNOT TELL YOU
--------------------------------------------------
**It has not been through the camera.** A 1024x1024 render downsampled to 128 is
cleaner than an OV sensor's 128 - no read noise, no AEC, no colour cast. A pair
that reads well here has cleared an easier bar than a pair shot by
`shoot.sh`.

**The generator and the teacher may share a prior.** Asked for an empty bowl,
the generator draws a *prototypical* empty bowl, which is exactly the thing a
web-trained encoder is best at recognising. So a synthetic pair can read high at
the teacher while the real object on a desk reads low. This inflates the screen
in one direction only, which is the useful direction: **a pair that fails here
will not be rescued by a camera**, and that is what a screen is for. The blind
screen above sharpens this: it keeps exactly the pairs a vision model finds
legible, so the teacher's reading is inflated by construction.

**Twelve scenes is not twelve frames of one scene.** A set like this asks
whether *any* open book outranks *any* closed book. The appliance asks about
one book on one desk, and a student can be fluent at the second and helpless at
the first. Judge the stages against each other, never a stage against a bench.

Both of those are why this tool's output is calibrated against pairs whose real
answer is already known - see the README of whatever set it wrote.
"""

import argparse
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

VAL = Path("model/data/val2017")
CAPS = Path("model/data/annotations/captions_val2017.json")
INST = Path("model/data/annotations/instances_val2017.json")
MODEL = "gemini-3-pro-image"

# Everything after the state clause. The generator will happily re-light and
# re-frame a photograph if not told twice not to, and a pair that differs in
# camera angle measures the angle.
KEEP = (" Do not re-photograph or re-compose the scene: return the same image "
        "with only that one change made. Same camera angle, same framing, same "
        "crop, same zoom, same lighting, same shadows, same background, same "
        "surface, same objects around it, and the same physical object at the "
        "same position and the same size in the frame. Photorealistic, same "
        "photographic style as the original.")


def sources(find: str, need: str | None, n: int) -> list[Path]:
    """val2017 images whose captions match, in id order so a run is repeatable."""
    if not CAPS.exists():
        sys.exit(f"no {CAPS} - run from the repo root")
    d = json.load(CAPS.open())
    names = {i["id"]: i["file_name"] for i in d["images"]}
    caps: dict[int, list[str]] = {}
    for a in d["annotations"]:
        caps.setdefault(a["image_id"], []).append(a["caption"].lower())
    f, g = re.compile(find, re.IGNORECASE), re.compile(need, re.IGNORECASE) if need else None
    out = []
    for i in sorted(caps):
        # The object and the qualifier in the *same* caption, for the same
        # reason probe_captions.py insists on it: two captions of one image do
        # not make it an image of the thing.
        if any(f.search(c) and (not g or g.search(c)) for c in caps[i]):
            p = VAL / names[i]
            if p.exists():
                out.append(p)
        if len(out) >= n:
            break
    if not out:
        sys.exit(f"no val2017 image matched /{find}/" + (f" and /{need}/" if need else ""))
    return out


def used(paths: list[Path]) -> set[str]:
    """Stems already spent on another set.

    A second draw exists to say how far the *first* one's numbers could have
    fallen by luck, and it can only say that if it shares no photograph with
    it. Without this the two sets overlap, agree, and the agreement means
    nothing.
    """
    seen: set[str] = set()
    for p in paths:
        d = p / "pos" if (p / "pos").is_dir() else p
        seen |= {f.stem for f in d.glob("*.png")}
    return seen


def crops(cats: list[str], min_side: int, n: int,
          skip: set[str] | None = None,
          only: set[str] | None = None) -> list[tuple[str, bytes]]:
    """Square crops around one COCO instance box, largest box per image.

    THIS IS THE SOURCE MODE TO PREFER, and the caption mode above is kept only
    for contrasts COCO has no category for. Two reasons, both learned the hard
    way on 2026-08-22:

    A COCO photograph is a *scene*; the appliance's object fills its 128x128
    frame. Asking the generator for a close-up instead closes that gap, but it
    grants it licence to re-compose - a third of one set came back as a
    different room, or with the object gone. Cropping first closes the same gap
    with no licence granted: the object already fills the frame, so the edit
    instruction can insist on holding everything still.

    `min_side` is a floor on the box in *source* pixels. Below it the crop is
    upsampled to 128 and the pair measures the blur.

    `only` is a hand-picked allowlist of stems and exists because `min_side`
    guarantees a large *box* and not a large *thing you asked for*. COCO `oven`
    is the case that forced it: eight of the first set's thirty were cooktops
    with the oven out of frame, or an outdoor barbecue, and no threshold
    separates those from an oven door. Look at the crops with --sources-only,
    write the ones that show the contrast, and pass the file back. Selecting
    *sources* on what is in the photograph is not selecting *results* - the
    blind screen after generation is still the filter that decides anything,
    and it still runs on every pair this admits.
    """
    if not INST.exists():
        sys.exit(f"no {INST} - run from the repo root")
    d = json.load(INST.open())
    ids = {c["name"]: c["id"] for c in d["categories"]}
    unknown = [c for c in cats if c not in ids]
    if unknown:
        sys.exit(f"not COCO categories: {unknown}\n  have: {', '.join(sorted(ids))}")
    want = {ids[c] for c in cats}
    meta = {i["id"]: i for i in d["images"]}
    best: dict[int, list[float]] = {}
    for a in d["annotations"]:
        if a["iscrowd"] or a["category_id"] not in want:
            continue
        x, y, w, h = a["bbox"]
        if min(w, h) < min_side:
            continue
        # One crop per photograph. Two boxes in one image would put two views
        # of the same lighting into the set and inflate the scene count.
        if a["image_id"] not in best or w * h > best[a["image_id"]][2] * best[a["image_id"]][3]:
            best[a["image_id"]] = [x, y, w, h]
    out = []
    for iid in sorted(best):
        x, y, w, h = best[iid]
        im = meta[iid]
        p = VAL / im["file_name"]
        if not p.exists() or (skip and p.stem in skip):
            continue
        if only is not None and p.stem not in only:
            continue
        # 1.25x the long side, so the object keeps a little of its context and
        # the generator has somewhere to put a shadow.
        side = min(max(w, h) * 1.25, im["width"], im["height"])
        cx, cy = x + w / 2, y + h / 2
        x0 = min(max(cx - side / 2, 0), im["width"] - side)
        y0 = min(max(cy - side / 2, 0), im["height"] - side)
        box = (int(x0), int(y0), int(x0 + side), int(y0 + side))
        buf = io.BytesIO()
        Image.open(p).convert("RGB").crop(box).save(buf, "JPEG", quality=95)
        out.append((p.stem, buf.getvalue()))
        if len(out) >= n:
            break
    if not out:
        sys.exit(f"no val2017 box of {cats} with a side >= {min_side}px")
    return out


def sheet(srcs: list[tuple[str, bytes]], out: Path, cell: int = 256,
          cols: int = 6) -> None:
    """One JPEG of every candidate crop, labelled, for picking --only by eye.

    Labelled with the last four digits of the stem rather than the whole
    twelve-digit COCO id: at 256px the full id is unreadable and the last four
    are unique within any one draw.
    """
    from PIL import ImageDraw
    rows = (len(srcs) + cols - 1) // cols
    grid = Image.new("RGB", (cols * cell, rows * cell), "black")
    draw = ImageDraw.Draw(grid)
    for k, (stem, data) in enumerate(srcs):
        x, y = (k % cols) * cell, (k // cols) * cell
        grid.paste(Image.open(io.BytesIO(data)).resize((cell, cell)), (x, y))
        draw.rectangle((x + 2, y + 2, x + 52, y + 18), fill="black")
        draw.text((x + 5, y + 5), stem[-4:], fill="white")
    out.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out, "JPEG", quality=92)


def edit(client, data: bytes, instruction: str, size: int) -> Image.Image | None:
    part = types.Part.from_bytes(data=data, mime_type="image/jpeg")
    r = client.models.generate_content(
        model=MODEL, contents=[part, instruction],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
    for p in r.candidates[0].content.parts:
        if p.inline_data and p.inline_data.data:
            im = Image.open(io.BytesIO(p.inline_data.data)).convert("RGB")
            # Centre crop to square before the resize, so the aspect ratio is
            # not squashed into a difference between the two sides.
            w, h = im.size
            s = min(w, h)
            im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
            return im.resize((size, size), Image.BICUBIC)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cat", default=None,
                    help="comma-separated COCO categories; sources are square "
                         "crops around one instance box. Prefer this to --find")
    ap.add_argument("--min-side", type=int, default=120,
                    help="floor on the box's short side, in source pixels")
    ap.add_argument("--skip", type=Path, nargs="*", default=(),
                    help="sets whose sources this one must not reuse - how a "
                         "second, independent draw is taken")
    ap.add_argument("--only", type=Path, default=None,
                    help="a file of source stems to admit, one per line - the "
                         "hand-picked draw. --min-side gives a big box, which "
                         "for some categories is not a big subject")
    ap.add_argument("--sources-only", action="store_true",
                    help="write the crops to <out>/src and a contact sheet "
                         "beside them, then stop. No generation, no cost. This "
                         "is how the --only list gets written")
    ap.add_argument("--find", default=None, help="caption regex picking sources")
    ap.add_argument("--need", default=None, help="second regex the same caption must match")
    ap.add_argument("--pos", required=True, help="the phrase probe_bisect.py will use")
    ap.add_argument("--neg", required=True)
    ap.add_argument("--pos-edit", required=True, help="the state, as an instruction")
    ap.add_argument("--neg-edit", required=True)
    ap.add_argument("-n", type=int, default=10, help="source photographs")
    ap.add_argument("--size", type=int, default=128)
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    if bool(args.cat) == bool(args.find):
        ap.error("give exactly one of --cat (preferred) or --find")
    skip = used(list(args.skip))
    if skip:
        print(f"skipping  : {len(skip)} sources already spent on {len(args.skip)} set(s)")
    only = None
    if args.only:
        only = {s.split()[0] for s in args.only.read_text().split("\n")
                if s.strip() and not s.startswith("#")}
        print(f"only      : {len(only)} hand-picked stems from {args.only}")
    if args.cat:
        cats = [c.strip() for c in args.cat.split(",")]
        srcs = crops(cats, args.min_side, len(only) if only else args.n, skip, only)
        print(f"sources   : {len(srcs)} crops of {cats} (box >= {args.min_side}px) from {VAL}")
        if only and len(srcs) < len(only):
            miss = only - {s for s, _ in srcs}
            print(f"  !! {len(miss)} of --only did not survive --min-side "
                  f"{args.min_side} or --skip: {', '.join(sorted(miss))}")
    else:
        srcs = [(p.stem, p.read_bytes()) for p in sources(args.find, args.need, args.n)]
        print(f"sources   : {len(srcs)} whole photographs from {VAL}")
    for stem, _ in srcs:
        print(f"            {stem}")

    if args.sources_only:
        # Written full size, not at --size. The point is to be looked at, and a
        # 128x128 thumbnail is too small to tell an oven door from a cooktop -
        # which is the exact judgement this mode exists to support.
        d = args.out / "src"
        d.mkdir(parents=True, exist_ok=True)
        for stem, data in srcs:
            (d / f"{stem}.jpg").write_bytes(data)
        sheet(srcs, args.out / "src" / "_contact.jpg")
        print(f"\nwrote     : {len(srcs)} crops to {d}, contact sheet in "
              f"{d / '_contact.jpg'}\n  Pick the ones that actually show the "
              f"contrast, put their stems in a file, and pass it as --only.")
        return 0

    client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                          location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
    (args.out / "pos").mkdir(parents=True, exist_ok=True)
    (args.out / "neg").mkdir(parents=True, exist_ok=True)
    (args.out / "queries.txt").write_text(f"{args.pos}\n{args.neg}\n")

    jobs = [(side, stem, data, f"Edit this photograph so that {clause}." + KEEP)
            for side, clause in (("pos", args.pos_edit), ("neg", args.neg_edit))
            for stem, data in srcs]

    def run(job):
        side, stem, data, instruction = job
        try:
            im = edit(client, data, instruction, args.size)
        except Exception as e:                      # noqa: BLE001 - one bad
            return side, stem, f"{type(e).__name__}: {str(e)[:70]}"  # gen must
        if im is None:                                               # not kill
            return side, stem, "no image returned"                   # the set
        im.save(args.out / side / f"{stem}.png")
        return side, stem, None

    bad = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for side, stem, err in ex.map(run, jobs):
            if err:
                bad += 1
                print(f"  !! {side}/{stem}: {err}")

    got = {s: len(list((args.out / s).glob("*.png"))) for s in ("pos", "neg")}
    print(f"\nwrote     : {got['pos']} pos, {got['neg']} neg at {args.size}x{args.size}"
          f"  ({bad} failed)")
    # An unpaired source is worse than a missing one: the two sides stop being
    # the same scenes and any difference between the classes is confounded.
    paired = {p.name for p in (args.out / "pos").glob("*.png")} & \
             {p.name for p in (args.out / "neg").glob("*.png")}
    for side in ("pos", "neg"):
        for p in (args.out / side).glob("*.png"):
            if p.name not in paired:
                print(f"  dropping unpaired {side}/{p.name}")
                p.unlink()
    print(f"paired    : {len(paired)} scenes in both states")
    print(f"\n  uv run --script tools/probe_bisect.py --a {args.out}/pos "
          f"--b {args.out}/neg \\\n      --pos {args.pos!r} --neg {args.neg!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
