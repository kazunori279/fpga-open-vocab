# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow"]
# ///
"""Read the board's 512 floats back out in words.

    uv run host/caption.py /tmp/m9.log
    uv run host/caption.py --npy /tmp/vecs.npy --labels OPEN,CLOSED

The board presses 'V' worth of output into a BEGIN/END block (m9.c, the same
cam_dump_frame() envelope pixels use). This turns that vector into English by
*retrieval*, not generation: find what it is nearest to among things whose
words are already known, and print those.

Two banks, and the difference between them is the point
--------------------------------------------------------
**Nearest images.** model/cache/emb_val2017_*.npy is 5000 COCO images the
teacher already embedded, and COCO ships five human captions per image. So the
nearest neighbours of the device vector come with English attached at no cost -
no text encoder, no new cache, nothing to build. Both sides of this comparison
are *image* embeddings, which is what makes it the trustworthy one.

**Nearest captions.** model/captions.py builds the other bank, of caption text
embedded directly. It reads more like a caption because it is one, but it is
measured across the teacher's modality gap: image and text embeddings occupy
separate cones, so these cosines run far lower than the image-to-image ones
and the ranking is the softer of the two. Shown second, and only if built.

What this is and is not for
---------------------------
It is an interpretability readout. If the open book and the closed book decode
to the same five captions, that is the M12 capacity loss stated in words
instead of in standard deviations.

It is **not** a better detector, and cannot be. A caption is downstream of the
embedding and coarser than it - ten tokens against 512 dimensions - so any
signal that reaches the words was already in the dot product the board is
computing. M12 measured AUC 0.000 on opened-vs-closed, i.e. the vector
separates those two states perfectly; the fault is in which way the *text*
axis points. Retrieval inherits that fault rather than repairing it.

One more caveat, in the same spirit: the vector is the *student's* and the bank
is the *teacher's*. They agree at cosine 0.70-0.88 (probe_open.py), not 1.0, so
the neighbours are blurrier than a teacher-against-teacher query would give.
Pass --bank on a student-side cache to remove that term.
"""
import argparse
import binascii
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Below the inserts above, necessarily.
import data  # model/data.py, for the canonical image order
from cam import parse  # one BEGIN/END parser, not two

TAG = "m9emb"
# m9.c prints this immediately before the block. cam.py's SNAPAT deliberately
# does not match it, so the frame numbers are picked up here and paired with the
# blocks positionally - sound because the line and its block are adjacent in a
# sequential log.
AT = re.compile(r"^embedding\s*:\s*frame (\d+)")

# Not a stopword list so much as a list of words that are true of every COCO
# photograph and so carry no information about which one this is.
DULL = {
    "a", "an", "the", "of", "on", "in", "at", "to", "with", "and", "is", "are",
    "his", "her", "its", "their", "there", "that", "this", "it", "for", "from",
    "by", "as", "some", "two", "three", "up", "down", "next", "near", "over",
    "photo", "picture", "image", "view", "shot", "close",
}


def centre(vecs, mu):
    """Subtract the bank's mean direction, then renormalize.

    Without this the readout is nonsense that looks fine. Teacher embeddings
    are not spread over the sphere - they sit in a narrow cone, and on this bank
    the mean vector has norm **0.738**, so about three quarters of any embedding
    is a shared "this is a photograph" component that says nothing about which
    photograph. Two random COCO images already agree at cosine 0.544 for that
    reason alone, and the four bench frames - an open book, a closed book, a
    close-up and an empty desk - agree with each other at 0.89 to 0.96.

    Rank on that and one image becomes the nearest neighbour of everything.
    Measured here before the fix: 000000051598.jpg, "a black trash bag in a
    restroom next to a sink", was top-1 for three of the four frames. That is
    hubness, and it is a property of the geometry rather than of the picture.

    Centring is the same move the board already makes. m9.c's zscore() is
    `(cos - qbg[i]) / qsd[i]` - subtract what this scene scores anyway, keep
    what is left. The retrieval readout needs it for the same reason and was
    simply missing it. Afterwards the spread over the bank goes from
    0.685 +- 0.080 to 0.007 +- 0.181, and three of the four top-1s change.
    """
    out = vecs - mu
    return out / np.linalg.norm(out, axis=-1, keepdims=True)


def load_bank(bank: Path):
    """(vectors, per-row captions, a line saying what this bank is)."""
    if not bank.exists():
        raise SystemExit(
            f"{bank} not found - run: uv run model/teacher.py embed --split val2017")
    meta = json.loads(bank.with_suffix(".json").read_text())
    split = meta["split"]
    vecs = np.load(bank).astype("float32")
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    ann = data.DATA / "annotations" / f"captions_{split}.json"
    if not ann.exists():
        raise SystemExit(
            f"{ann} not found - run: uv run model/data.py fetch --split {split}")
    by_id = {}
    for a in json.loads(ann.read_text())["annotations"]:
        by_id.setdefault(a["image_id"], []).append(a["caption"].strip())

    # Row i of the cache is image i of data.image_list(), which is the invariant
    # teacher.py's `verify` exists to defend. COCO's filenames are the zero-
    # padded image id, so the join needs no extra table.
    names = data.image_list(split)
    if len(names) != vecs.shape[0]:
        raise SystemExit(f"{bank}: {vecs.shape[0]} rows but {len(names)} images")
    caps = [by_id.get(int(Path(nm).stem), []) for nm in names]
    mu = vecs.mean(axis=0)
    return (vecs, mu, caps, names,
            (f"{vecs.shape[0]} {split} images, {meta['model']}, "
             f"mean direction |mu| = {np.linalg.norm(mu):.3f}"))


def vectors_from_log(log: Path):
    """Every 'V' dump in a session log, newest parser, oldest envelope."""
    blocks = [b for b in parse(log) if b["tag"] == TAG]
    frames = [int(m[1]) for m in (AT.match(ln) for ln in
                                  log.read_text(errors="replace").splitlines()) if m]
    out = []
    for i, b in enumerate(blocks):
        raw = binascii.a2b_base64("".join(b["b64"]))
        crc = binascii.crc32(raw) & 0xFFFFFFFF
        ok = crc == b["crc"] and len(raw) == b["n"]
        if not ok:
            # Loud, and keep going: a corrupted block among good ones is worth
            # naming, and dropping the rest to report it would be worse.
            print(f"WARN  : block {i} crc {crc:08x} vs {b['crc']:08x} announced, "
                  f"{len(raw)} of {b['n']} bytes - skipped", file=sys.stderr)
            continue
        label = f"frame {frames[i]}" if i < len(frames) else f"block {i}"
        out.append((label, np.frombuffer(raw, dtype="<f4").astype("float32")))
    return out


def report(label, v, vecs, caps, names, k, show):
    s = vecs @ v
    top = np.argsort(-s)[:k]

    print(f"\n{label}")
    for r in top:
        line = caps[r][0] if caps[r] else "(no caption)"
        print(f"  {s[r]:+.4f}  {names[r]:<20} {line}")

    # The consensus line is the part worth reading. One nearest neighbour is an
    # anecdote; a word that shows up across five independent photographs is what
    # the vector actually encodes, with the photographers' idiosyncrasies
    # averaged out.
    words = Counter()
    for r in top:
        for c in caps[r]:
            for w in re.findall(r"[a-z]+", c.lower()):
                if w not in DULL and len(w) > 2:
                    words[w] += 1
    common = [f"{w}({n})" for w, n in words.most_common(show) if n > 1]
    print(f"  consensus : {' '.join(common) if common else '(nothing repeats)'}")
    return dict(words.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", nargs="?", type=Path, help="a session log with 'V' dumps")
    ap.add_argument("--npy", type=Path,
                    help="read vectors from an N x 512 .npy instead of a log, "
                         "which is how this gets tested with no board attached")
    ap.add_argument("--labels", default="",
                    help="comma-separated names for the --npy rows")
    ap.add_argument("--bank", type=Path,
                    default=ROOT / "model/cache/emb_val2017_ViT-B-16-openai.npy",
                    help="the image-embedding cache to retrieve against")
    ap.add_argument("--text-bank", type=Path,
                    default=ROOT / "model/cache/captions_val2017.npy",
                    help="the caption-text bank from model/captions.py, if built")
    ap.add_argument("-k", type=int, default=5, help="neighbours to show")
    ap.add_argument("--words", type=int, default=8, help="consensus words to show")
    ap.add_argument("--raw", action="store_true",
                    help="skip mean-centring. Only for seeing what it fixes: "
                         "uncentred, one hub image wins nearly every query and "
                         "the output looks plausible while being meaningless")
    args = ap.parse_args()

    if args.npy:
        rows = np.load(args.npy).astype("float32")
        labels = [x.strip() for x in args.labels.split(",") if x.strip()]
        vecs_in = [(labels[i] if i < len(labels) else f"row {i}", rows[i])
                   for i in range(rows.shape[0])]
    elif args.log:
        vecs_in = vectors_from_log(args.log)
    else:
        return ap.error("give a log, or --npy")

    if not vecs_in:
        print("nothing to read: no 'V' dumps in that log. Press 'V' in the demo.",
              file=sys.stderr)
        return 1

    vecs, mu, caps, names, what = load_bank(args.bank)
    print(f"bank      : {what}")
    print(f"vectors   : {len(vecs_in)}")
    print(f"centring  : {'OFF (--raw) - expect one hub to win everything' if args.raw else 'ON, against the bank mean'}")

    def prep(v):
        v = v / np.linalg.norm(v)
        return v if args.raw else centre(v, mu)

    ivecs = vecs if args.raw else centre(vecs, mu)
    print("\n=== nearest images (image-to-image, no modality gap) ===")
    for label, v in vecs_in:
        report(label, prep(v), ivecs, caps, names, args.k, args.words)

    if args.text_bank.exists():
        tv = np.load(args.text_bank).astype("float32")
        tv /= np.linalg.norm(tv, axis=1, keepdims=True)
        texts = json.loads(args.text_bank.with_suffix(".json").read_text())["captions"]
        # Each side centred on *its own* modality's mean - the image query on
        # the image bank's, the captions on the caption bank's. That is what the
        # modality gap is: two cones sitting in different places, so subtracting
        # one cone's centre from the other's members leaves the offset between
        # them in the result. Getting this wrong is not subtle and does not look
        # wrong: centring the image query on the text mean made four long
        # "a photo of X on a Y background" captions the top hits for every
        # frame, including the teacher's, which is how the bug was caught.
        tmu = tv.mean(axis=0)
        tvc = tv if args.raw else centre(tv, tmu)
        print(f"\n=== nearest captions (across the modality gap, {len(texts)} texts) ===")
        for label, v in vecs_in:
            s = tvc @ prep(v)
            print(f"\n{label}")
            for r in np.argsort(-s)[:args.k]:
                print(f"  {s[r]:+.4f}  {texts[r]}")
    else:
        print(f"\n(no text bank at {args.text_bank} - "
              f"run model/captions.py to add the second readout)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
