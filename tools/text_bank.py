#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "open_clip_torch", "transformers",
#                 "sentencepiece"]
# ///
"""A bank of text directions, in a student's own target space, for `--text`.

    uv run --script tools/text_bank.py --teacher emb_train2017_SO400M-pca512_s30000
    uv run --script tools/text_bank.py --teacher ViT-B-16-openai -n 4096

WHAT IT IS FOR
--------------
`model/distill.py --text W --text-bank <this>` adds a term that asks the student
to reproduce the TEACHER'S RANKING OF IMAGES AGAINST TEXT, rather than only the
teacher's image vector. The measurement that motivates it is in
`bench/stills/20260822-synth-book-crop2/README.md`: on generated cross-scene
sets the student reaches AUC 0.60-0.70 when asked with a phrase but 0.75-0.84
when the direction is fitted on its own embeddings. The axis is partly there;
the text vector misses it. A 1.4 M-parameter student cannot match the teacher
everywhere, so where it spends its error budget is the whole game, and plain
`1 - cos` spends it isotropically - it has no idea which directions a sentence
will ever point at.

WHY COCO CAPTIONS AND NOT STATE PHRASES
-----------------------------------------
The obvious bank is the contrasts being measured - `an opened book`, `a glass
with tea`. **That would be fitting the training objective to the test set**, and
the number afterwards would mean nothing. COCO captions are a broad, generic
sample of the sentences a web-trained text encoder produces, none of them the
eval's phrases, and train2017's captions describe the same images the student is
already distilled on. val2017 stays untouched, which is what the generated sets
are cropped from.

NO TEMPLATES, AND THE PROJECTION LAST
---------------------------------------
A caption is a well-formed sentence, so it goes through the tokenizer as
written - `model/captions.py` has the long version of why wrapping it in "a
photo of a {}." embeds the wrapper's grammar. The basis is applied after
normalisation and the result is renormalised, which is the order
`teacher.encode_queries_spec` uses; a bank built in the unprojected space would
dot cleanly against 512-d targets and return noise.

THE BANK IS NOT A CLASSIFIER
------------------------------
Rows are near-duplicates of each other by construction - COCO has thousands of
sentences about a man on a surfboard - so the softmax over it is soft, and that
is intended. The term is a similarity-profile match, not a retrieval task; the
retrieval task is what `--infonce` already does against image targets.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))

import data
import teacher as teacher_mod
from spaces import resolve


def captions(split: str, n: int) -> list[str]:
    """`n` de-duplicated captions, evenly spread through the file.

    Evenly spread rather than the first n: COCO's annotation order is image
    order, so a prefix is a bank about whatever the first few thousand
    photographs happened to contain.
    """
    ann = data.DATA / "annotations" / f"captions_{split}.json"
    if not ann.exists():
        raise SystemExit(f"{ann} not found - run: uv run model/data.py fetch "
                         f"--split {split}")
    seen, texts = set(), []
    for a in json.loads(ann.read_text())["annotations"]:
        c = " ".join(a["caption"].split())
        key = c.lower().rstrip(".")
        if key and key not in seen:
            seen.add(key)
            texts.append(c)
    if n <= 0 or n >= len(texts):
        return texts
    step = len(texts) / n
    return [texts[int(i * step)] for i in range(n)]


@torch.no_grad()
def encode(model, tokenizer, texts: list[str], device, batch: int) -> np.ndarray:
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        f = model.encode_text(tokenizer(chunk).to(device)).float()
        out.append((f / f.norm(dim=-1, keepdim=True)).cpu().numpy())
        print(f"\rencode : {min(i + batch, len(texts))}/{len(texts)}",
              end="", flush=True)
    print()
    return np.concatenate(out).astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--teacher", required=True,
                    help="the run's `teacher` string, e.g. "
                         "emb_train2017_SO400M-pca512_s30000 - the bank is "
                         "built in whatever space that resolves to")
    ap.add_argument("--split", default="train2017",
                    help="captions to draw from; val2017 is held out because "
                         "the generated eval sets are cropped from it")
    ap.add_argument("-n", type=int, default=4096, help="rows (0 = every caption)")
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    spec, basis_path = resolve(args.teacher)
    device = teacher_mod.pick_device()
    print(f"teacher  : {args.teacher}")
    print(f"spec     : {spec}")
    print(f"basis    : {basis_path.name if basis_path else 'none (identity)'}")

    texts = captions(args.split, args.n)
    print(f"captions : {len(texts)} from {args.split}, no templates")

    model, tok = teacher_mod.load_spec(spec, device)
    v = encode(model, tok, texts, device, args.batch)
    if basis_path is not None:
        b = np.load(basis_path)
        v = (v - b["mu"]) @ b["w"]
        v = v / np.linalg.norm(v, axis=-1, keepdims=True)
    v = v.astype(np.float32)

    out = args.out or data.CACHE / f"textbank_{args.teacher}_{len(texts)}.npy"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, v)
    out.with_suffix(".json").write_text(json.dumps(
        {"teacher": args.teacher, "spec": spec,
         "basis": basis_path.name if basis_path else None,
         "split": args.split, "n": len(texts), "dim": int(v.shape[1]),
         "templates": None, "captions": texts}, indent=1) + "\n")
    print(f"wrote    : {out}  {v.shape}")
    return 0


raise SystemExit(main())
