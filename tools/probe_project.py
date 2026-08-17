# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Does SigLIP 2 SO400M still bind the adjective after being squeezed to 512?

    uv run --script tools/probe_project.py [--bank 3000] [--snaps DIR]

THE PROBLEM THIS MEASURES
-------------------------
tools/probe_teacher.py found the teacher worth having: ViT-SO400M-14-SigLIP2 is
the smallest openly-licensed model tested that passes all three gates, matching
gemini-embedding-2's 4-of-8 exactly. Its output is 1152 wide. The board's query
record and the student's head are 512.

Widening the board is one answer and costs 2.25x on the per-frame transfer. The
other is to project the joint space to 512 and leave the board alone - and that
only works if the projection is applied to the *image and text sides alike*,
because a dot product between a projected image vector and an unprojected text
vector is meaningless. So the question is not "does 512 dims hold the
information", it is "does one shared map to 512 preserve the specific
comparison that gate 2 tests", whose whole margin on this teacher is 0.0166.

THREE MAPS, AND WHY THE CHEAP ONE IS TRIED FIRST
------------------------------------------------
  full     no projection, 1152 wide. The reference; must reproduce probe_teacher.
  random   a random orthonormal 1152->512. Needs no bank at all. By
           Johnson-Lindenstrauss it preserves inner products to about
           1/sqrt(512) ~ 0.044 relative, which is the same order as the margin
           it has to protect - so it is expected to be marginal, and it is here
           as the control that says how much of any PCA win is really PCA
           rather than "512 dims is simply enough".
  pca      the top 512 directions of a bank of real image and caption vectors,
           fitted on the two modalities *jointly* and centred on the joint mean.
           Fitting on images alone would discard the directions that carry the
           text side, which is exactly the axis the gates read.

Renormalization after projection is not optional: the map is not orthogonal for
PCA, so the outputs are not unit vectors and a raw dot product would silently
compare lengths as well as directions.

WHAT COUNTS AS A PASS
---------------------
Gate 2 surviving. Gates 1 and 3 did not discriminate between any teacher, so
their survival is necessary and says little. If PCA-512 passes gate 2 the board
stays 512 wide; if it does not, the honest answer is a wider head.

WHAT IT FOUND, 2026-08-08   (bank 3000+3000, val2017, seed 0)
-------------------------------------------------------------
  map                        gate 1   gate 2   gate 3   closed-book margin
  full 1152                  PASS     PASS     3 of 3   +0.0166
  random orthonormal 512     FAIL     pass     2 of 3   +0.0164
  joint PCA 512 (98.3% var)  PASS     PASS     3 of 3   +0.0433

**The board stays 512 wide.** PCA keeps every gate and the gate-2 margin comes
out 2.6x wider than in the full space - not because 512 dims beat 1152, but
because centring on the joint mean deletes the cone axis, which is shared by
every vector and so contributes to both sides of the comparison equally while
inflating the denominator. The same effect is why the printed cosines go
negative: these are centred residuals, and only their ordering is meaningful.
For the board that is free - m9.c's z score already subtracts a running
per-query background, so a constant offset was never being read.

The random control earns its place. It fails gate 1 outright - the empty frame
ranks "a book" top - and pages-cover falls under noise, while its gate-2 margin
is essentially the unprojected one. So a projection *can* hold the book
comparison and still wreck the model's basic sense of what is in the frame,
which is exactly the failure a gate-2-only test would have shipped.

Two caveats. The PCA basis is now part of the artifact: whatever fits it must be
frozen and applied to the student's targets and to every query the host encodes,
or the two sides land in different spaces. And this is still one open book and
one closed book - it says the projection is not the bottleneck, not that the
teacher binds adjectives in general.
"""
import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import open_clip
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_teacher as pt

ROOT = Path(__file__).resolve().parent.parent
SPEC = "ViT-SO400M-14-SigLIP2:webli"
OUT = 512


def build_bank(model, preprocess, tok, device, n, split, seed):
    """Encode n images and n captions with the same model, for the PCA fit.

    COCO val2017 and its captions, because they are already on disk from the
    existing pipeline and because the bank only has to span the kind of everyday
    scene the board looks at - it is fitting directions, not learning anything.
    """
    sys.path.insert(0, str(ROOT / "model"))
    import data as data_mod

    names = data_mod.image_list(split)
    caps = json.loads((ROOT / "model" / "cache" /
                       f"captions_{split}.json").read_text())
    caps = caps["captions"] if isinstance(caps, dict) else caps

    rng = random.Random(seed)
    names = rng.sample(names, min(n, len(names)))
    caps = rng.sample(caps, min(n, len(caps)))
    idir = data_mod.image_dir(split)

    iv = []
    with torch.no_grad():
        for i in range(0, len(names), 32):
            batch = torch.stack([
                preprocess(Image.open(idir / f).convert("RGB"))
                for f in names[i:i + 32]]).to(device)
            v = model.encode_image(batch).float()
            iv.append((v / v.norm(dim=-1, keepdim=True)).cpu())
            print(f"\r  images {min(i+32, len(names))}/{len(names)}", end="", flush=True)
        print()
        tv = []
        for i in range(0, len(caps), 64):
            v = model.encode_text(tok(caps[i:i + 64]).to(device)).float()
            tv.append((v / v.norm(dim=-1, keepdim=True)).cpu())
            print(f"\r  captions {min(i+64, len(caps))}/{len(caps)}", end="", flush=True)
        print()
    return torch.cat(iv).numpy(), torch.cat(tv).numpy()


def fit_pca(bank, out):
    """Joint mean + top-`out` right singular vectors of the pooled bank."""
    mu = bank.mean(axis=0)
    # full_matrices=False keeps this a (n x 1152) decomposition rather than
    # materializing an n x n left factor for a few-thousand-row bank.
    _, s, vt = np.linalg.svd(bank - mu, full_matrices=False)
    var = float((s[:out] ** 2).sum() / (s ** 2).sum())
    return mu, vt[:out].T, var


def project(v, mu, w):
    p = (v - mu) @ w
    return p / np.linalg.norm(p, axis=-1, keepdims=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", type=int, default=3000,
                    help="images and captions each, for the PCA fit")
    ap.add_argument("--split", default="val2017")
    ap.add_argument("--snaps", type=Path, default=Path("/tmp/snaps"))
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    device = pt.pick_device()
    print(f"model  : {SPEC}\ndevice : {device}\nbank   : {args.bank} images "
          f"+ {args.bank} captions from {args.split}")

    iv, tv = pt.encode(SPEC, args.snaps, device)
    pt.gates(iv, tv, f"full {iv.shape[1]}-d  (reference - must match probe_teacher)")

    # A random *orthonormal* map, not a random gaussian one: an orthonormal
    # basis preserves norms exactly on the subspace it keeps, which is the
    # fairest version of "just throw dimensions away" and so the strictest
    # control on whether PCA is doing real work.
    g = np.random.default_rng(args.seed).standard_normal((iv.shape[1], OUT))
    q, _ = np.linalg.qr(g)
    zero = np.zeros(iv.shape[1], dtype=np.float32)
    pt.gates(project(iv, zero, q), project(tv, zero, q),
             f"random orthonormal {iv.shape[1]}->{OUT}  (control)")

    name, pretrained = SPEC.split(":", 1)
    model, _, preprocess = open_clip.create_model_and_transforms(
        name, pretrained=pretrained, device=device)
    model.eval()
    tok = open_clip.get_tokenizer(name)
    bi, bt = build_bank(model, preprocess, tok, device, args.bank,
                        args.split, args.seed)
    mu, w, var = fit_pca(np.concatenate([bi, bt]), OUT)
    print(f"  pca  : fitted on {len(bi)+len(bt)} vectors, "
          f"{var:.1%} of joint variance retained in {OUT} dims")
    pt.gates(project(iv, mu, w), project(tv, mu, w),
             f"joint PCA {iv.shape[1]}->{OUT}  ({var:.1%} variance)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
