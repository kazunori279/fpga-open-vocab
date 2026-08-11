# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "open_clip_torch"]
# ///
"""Embed COCO's captions, so host/caption.py can retrieve sentences directly.

    uv run model/captions.py --split val2017

The other bank host/caption.py uses needs nothing built: the teacher's image
embeddings are already cached and COCO's captions are already on disk, so the
nearest *images* come with English attached for free. This builds the bank for
the other readout - the caption text embedded as text - which reads better
because it is a sentence rather than a sentence about a neighbour.

**No prompt templates here, and that is the difference from teacher.py.**
encode_queries() wraps a bare class name in "a photo of a {}." seven times and
averages, which is how CLIP zero-shot classification is normally measured and
is right for the word `book`. A caption is already a well-formed sentence; the
templates would produce "a photo of a A man riding a horse on the beach." and
embed the wrapper's grammar along with the content. So these go through the
tokenizer as written.

Read the resulting cosines knowing they cross the modality gap: text and image
embeddings sit in separate cones, so an image-to-caption cosine is far smaller
than an image-to-image one and comparing the two numbers is meaningless.
host/caption.py centres each bank on its own mean, which is what makes the
*rankings* comparable even though the raw values are not.
"""
import argparse
import json
import sys

import numpy as np
import open_clip
import torch

import data
import teacher


@torch.no_grad()
def encode_texts(model, texts: list[str], device, batch: int = 256) -> np.ndarray:
    tokenizer = open_clip.get_tokenizer(teacher.MODEL)
    out = np.zeros((len(texts), teacher.EMBED_DIM), dtype=np.float16)
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        feats = model.encode_text(tokenizer(chunk).to(device))
        feats = feats / feats.norm(dim=-1, keepdim=True)
        out[i:i + len(chunk)] = feats.cpu().numpy().astype(np.float16)
        print(f"\rencode : {min(i + batch, len(texts))}/{len(texts)}",
              end="", flush=True)
    print()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", default="val2017")
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--limit", type=int, default=0,
                    help="keep only the first N captions after dedup (0 = all)")
    args = ap.parse_args()

    ann = data.DATA / "annotations" / f"captions_{args.split}.json"
    if not ann.exists():
        raise SystemExit(f"{ann} not found - run: uv run model/data.py fetch "
                         f"--split {args.split}")

    # Dedup on the normalised form but keep the original spelling. COCO has five
    # captions per image from five annotators and the collisions are real -
    # "A man riding a wave on top of a surfboard." appears verbatim many times.
    # Duplicates would otherwise fill a top-5 with one sentence said five ways.
    seen, texts = set(), []
    for a in json.loads(ann.read_text())["annotations"]:
        c = " ".join(a["caption"].split())
        key = c.lower().rstrip(".")
        if key and key not in seen:
            seen.add(key)
            texts.append(c)
    raw = sum(1 for _ in json.loads(ann.read_text())["annotations"])
    if args.limit:
        texts = texts[:args.limit]
    print(f"captions : {len(texts)} unique of {raw} in {ann.name}")

    device = teacher.pick_device()
    model, _ = teacher.load_clip(device)
    print(f"teacher  : {teacher.tag()} text encoder, no templates, on {device}")
    vecs = encode_texts(model, texts, device, args.batch)

    out = data.CACHE / f"captions_{args.split}.npy"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, vecs)
    out.with_suffix(".json").write_text(json.dumps({
        "model": teacher.MODEL, "pretrained": teacher.PRETRAINED,
        "split": args.split, "n": len(texts), "dim": teacher.EMBED_DIM,
        "templates": None, "captions": texts,
    }))
    print(f"wrote    : {out}  {vecs.shape}")
    print(f"           {out.with_suffix('.json')}  (the captions, in row order)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
