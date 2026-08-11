# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Fetch COCO, shrink it for training, and turn its annotations into queries.

    uv run model/data.py fetch   --split val2017
    uv run model/data.py queries --split val2017
    uv run model/data.py resize  --split train2017

`fetch` downloads and unzips into model/data/ (gitignored, resumable-by-skip).
`queries` writes model/cache/queries_<split>.json: for each of COCO's 80 classes,
the image indices where that class is *prominently* present.
`resize` writes a shrunken copy of the corpus for the student to train on.

Parsed with stdlib json rather than pycocotools - the schema needed here is three
fields deep and pycocotools wants a C build. Pillow is the only dependency, so
`fetch` still does not drag torch in.
"""

import argparse
import json
import sys
import urllib.request
import zipfile
from multiprocessing import Pool
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = ROOT / "cache"

BASE_IMAGES = "http://images.cocodataset.org/zips"
BASE_ANNOTATIONS = "http://images.cocodataset.org/annotations"

# A class counts as present only if it covers at least this fraction of the
# image. "Contains a cat annotation somewhere" is a bad target for a whole-image
# embedding - a 12-pixel cat in the corner would score as positive and no
# encoder could see it. Images where the class is present but smaller than this
# are excluded from the query entirely rather than counted as negatives, because
# they are genuinely ambiguous and would only add noise.
AREA_THRESHOLD = 0.05


# Shorter side of the resized training copy. The student's RandomResizedCrop
# takes 65-100% of the frame area and lands on 128x128, so a 180px short side is
# still a downsample in the worst case - no upscaling, no invented detail.
SMALL_SIZE = 180
SMALL_QUALITY = 92


def image_dir(split: str) -> Path:
    return DATA / split


def small_dir(split: str) -> Path:
    return DATA / f"{split}_small"


def student_image_dir(split: str) -> Path:
    """Where the *student* reads pixels from, preferring the resized copy.

    Deliberately separate from image_dir(): the teacher must always see the
    originals at 224, and silently feeding it 180px JPEGs would produce a
    plausible-looking but degraded embedding cache. Only DistillSet calls this.
    Filenames match, so image_list() ordering still applies.
    """
    d = small_dir(split)
    return d if d.is_dir() else image_dir(split)


def _shrink(job: tuple[str, str, str, int]) -> None:
    src, dst, name, size = job
    out = Path(dst) / name
    if out.exists():
        return
    img = Image.open(Path(src) / name).convert("RGB")
    w, h = img.size
    if min(w, h) > size:
        scale = size / min(w, h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         Image.BICUBIC)
    img.save(out, "JPEG", quality=SMALL_QUALITY)


def cmd_resize(split: str, size: int, workers: int) -> int:
    """Pre-shrink the corpus so training is not bound by JPEG decode.

    Full-size COCO decode dominates a 128x128 training step so completely that
    the GPU sits at 0% while six worker processes saturate the CPU. Decoding the
    same 118k images 40 times over is the actual cost of a run; paying it once
    turns a ten-hour job into a bit over an hour, and the shrunken corpus is
    small enough to stay in the page cache.
    """
    src, dst = image_dir(split), small_dir(split)
    names = image_list(split)
    dst.mkdir(parents=True, exist_ok=True)

    jobs = [(str(src), str(dst), n, size) for n in names]
    done = 0
    with Pool(workers) as pool:
        for _ in pool.imap_unordered(_shrink, jobs, chunksize=64):
            done += 1
            if done % 2000 == 0 or done == len(names):
                print(f"\rresize : {done}/{len(names)}", end="", flush=True)
    print()

    written = len(list(dst.glob("*.jpg")))
    total = sum(p.stat().st_size for p in dst.glob("*.jpg"))
    print(f"src    : {sum(p.stat().st_size for p in src.glob('*.jpg')) / 2**30:.1f} GiB")
    print(f"dst    : {total / 2**30:.2f} GiB in {dst}")
    ok = written == len(names)
    print("\nRESULT : " + (f"PASS - {written} images at {size}px short side"
                           if ok else f"FAIL - {written} of {len(names)} written"))
    return 0 if ok else 1


def image_list(split: str) -> list[str]:
    """The canonical image order for a split.

    Every downstream artifact - the teacher embedding cache, the query index
    lists - is positional against this list, so it has to be derived the same
    way everywhere. Sorted filenames, nothing clever.
    """
    d = image_dir(split)
    if not d.is_dir():
        raise SystemExit(f"{d} not found - run: uv run model/data.py fetch --split {split}")
    return sorted(p.name for p in d.iterdir() if p.suffix.lower() == ".jpg")


def download(url: str, dst: Path) -> None:
    if dst.exists():
        print(f"have   : {dst.name}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    print(f"get    : {url}")

    # Only emit on a percentage change. urlretrieve calls back per 8 KB block,
    # and a bare \r redraw turns into ~900 KB of log when stdout is a file
    # rather than a terminal.
    last = -1

    def hook(blocks: int, block_size: int, total: int) -> None:
        nonlocal last
        if total <= 0:
            return
        got = blocks * block_size
        pct = min(100, 100 * got // total)
        if pct != last:
            last = pct
            print(f"\r         {got / 2**20:.0f}/{total / 2**20:.0f} MiB ({pct}%)", end="", flush=True)

    urllib.request.urlretrieve(url, tmp, hook)
    print()
    tmp.rename(dst)


def unzip(archive: Path, marker: Path) -> None:
    """Extract `archive` into DATA unless `marker` already exists."""
    if marker.exists():
        print(f"have   : {marker.relative_to(DATA)}")
        return
    print(f"unzip  : {archive.name}")
    with zipfile.ZipFile(archive) as z:
        z.extractall(DATA)


def cmd_fetch(split: str) -> int:
    DATA.mkdir(parents=True, exist_ok=True)

    img_zip = DATA / f"{split}.zip"
    ann_zip = DATA / "annotations_trainval2017.zip"
    download(f"{BASE_IMAGES}/{split}.zip", img_zip)
    download(f"{BASE_ANNOTATIONS}/annotations_trainval2017.zip", ann_zip)

    unzip(img_zip, image_dir(split))
    unzip(ann_zip, DATA / "annotations" / f"instances_{split}.json")

    names = image_list(split)
    print(f"images : {len(names)} in {image_dir(split)}")
    print("\nRESULT : PASS - COCO ready")
    return 0


def cmd_queries(split: str, threshold: float) -> int:
    ann_path = DATA / "annotations" / f"instances_{split}.json"
    if not ann_path.exists():
        raise SystemExit(f"{ann_path} not found - run: uv run model/data.py fetch --split {split}")

    print(f"parse  : {ann_path.name}")
    with ann_path.open() as f:
        coco = json.load(f)

    # COCO ships the class names in the annotation file, so there is no list to
    # hardcode and drift out of sync.
    categories = {c["id"]: c["name"] for c in coco["categories"]}
    pixels = {im["id"]: im["width"] * im["height"] for im in coco["images"]}
    file_of = {im["id"]: im["file_name"] for im in coco["images"]}

    names = image_list(split)
    index_of = {name: i for i, name in enumerate(names)}

    # image index -> category id -> summed annotation area, as a fraction of the
    # frame. Instances of one class can overlap, which double-counts and nudges
    # borderline images toward "present" - the safe direction, since the
    # alternative is calling a clearly visible object absent.
    coverage: dict[int, dict[int, float]] = {}
    for ann in coco["annotations"]:
        i = index_of.get(file_of.get(ann["image_id"], ""))
        if i is None:
            continue
        frame = pixels[ann["image_id"]]
        per_class = coverage.setdefault(i, {})
        cid = ann["category_id"]
        per_class[cid] = per_class.get(cid, 0.0) + ann["area"] / frame

    queries = []
    for cid, name in sorted(categories.items(), key=lambda kv: kv[1]):
        pos, excluded = [], []
        for i in range(len(names)):
            frac = coverage.get(i, {}).get(cid)
            if frac is None:
                continue  # absent -> negative, recorded implicitly
            (pos if frac >= threshold else excluded).append(i)
        queries.append({"name": name, "pos": pos, "excluded": excluded})

    # Negatives are the overwhelming majority, so storing them would triple the
    # file for no information: anything not positive and not excluded is negative.
    out = {
        "split": split,
        "area_threshold": threshold,
        "n_images": len(names),
        "queries": queries,
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / f"queries_{split}.json"
    dst.write_text(json.dumps(out))

    usable = [q for q in queries if len(q["pos"]) >= 10]
    print(f"classes: {len(queries)}, {len(usable)} with >= 10 positives")
    print(f"images : {len(names)}")
    print("\nprominent-object counts (>= {:.0%} of frame):".format(threshold))
    ranked = sorted(queries, key=lambda q: -len(q["pos"]))
    for q in ranked[:5]:
        print(f"         {q['name']:<16} {len(q['pos']):>5} pos  {len(q['excluded']):>5} excluded")
    print("         ...")
    for q in ranked[-3:]:
        print(f"         {q['name']:<16} {len(q['pos']):>5} pos  {len(q['excluded']):>5} excluded")

    print(f"\nwrote  : {dst}")
    ok = len(usable) >= 40
    print("\nRESULT : " + ("PASS" if ok else "FAIL - too few usable queries"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="download and unzip COCO")
    p.add_argument("--split", default="val2017", choices=("val2017", "train2017"))

    p = sub.add_parser("queries", help="build binary query ground truth")
    p.add_argument("--split", default="val2017", choices=("val2017", "train2017"))
    p.add_argument("--threshold", type=float, default=AREA_THRESHOLD)

    p = sub.add_parser("resize", help="shrink the corpus for student training")
    p.add_argument("--split", default="train2017", choices=("val2017", "train2017"))
    p.add_argument("--size", type=int, default=SMALL_SIZE)
    p.add_argument("--workers", type=int, default=10)

    args = ap.parse_args()
    if args.cmd == "fetch":
        return cmd_fetch(args.split)
    if args.cmd == "resize":
        return cmd_resize(args.split, args.size, args.workers)
    return cmd_queries(args.split, args.threshold)


if __name__ == "__main__":
    sys.exit(main())
