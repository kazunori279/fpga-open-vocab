# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Re-encode a split's targets with SigLIP 2 SO400M, squeezed to 512 by a frozen map.

    uv run --script tools/teacher_swap.py --split train2017 --subset 30000
    uv run --script model/distill.py --split train2017 --subset 30000 \\
        --targets model/cache/emb_train2017_SO400M-pca512_s30000.npy \\
        --infonce 0.3 --epochs 20 --batch 512 --lr 6e-3 --name so400m-sieve

WHAT THIS IS FOR
----------------
Two probes settled the teacher question and left one thing unmeasured:

  probe_teacher.py   ViT-B/16 fails gate 2 - "a closed book" scores higher on
                     the OPEN image. Every model below SO400M fails it too.
                     SO400M passes. That is a property of the *teacher*.
  probe_project.py   a joint PCA 1152->512 keeps all three gates and widens the
                     gate-2 margin from +0.0166 to +0.0433, so the board's 512
                     stays.

Neither says whether a 512-wide, 128px student *inherits* gate 2 when distilled
from that teacher. Nothing but a training run does. This script produces the
targets for that run.

It is worth running now, and would not have been last week: with the loss fixed,
nce0.3 transmits 93% of its teacher's spread-front gap (+0.0648 of +0.0692,
tools/probe_noise.py). While the student was destroying the signal a better
teacher was pointless; now the teacher is the ceiling.

THE BASIS IS AN ARTIFACT, NOT AN IMPLEMENTATION DETAIL
------------------------------------------------------
The map is fitted here and written next to the targets as an .npz. It then has
to be applied, unchanged, to *every* vector that will ever be compared to a
student output - the host's query encodings included. A dot product between a
projected image vector and an unprojected text vector is not a weaker
comparison, it is a meaningless one, and it will not look like an error.

Bank, seed and size are pinned to probe_project.py's (val2017, 3000 images and
3000 captions, seed 0) so the basis fitted here is the one whose gates were
measured, not a new one that merely resembles it.

SUBSET ROWS, AND THE HOLE THEY LEAVE
------------------------------------
The output is a full (n_split, 512) array so it indexes exactly like the ViT-B/16
cache, but with --subset only the rows that run will read are filled; the rest
are zero. distill.py checks for that and refuses a mismatched pair, because a
zero target normalizes to NaN and would otherwise train for an hour and report a
plausible-looking loss. Same --subset and --holdout on both commands, or neither.

WHAT THE FIRST RUN PRODUCED, 2026-08-08
---------------------------------------
--subset 30000 --holdout 1000: 31000 images at 11.0 img/s = 53 min on MPS,
basis 1152 -> 512 keeping 98.3% of variance, target cone 0.5864 against
ViT-B/16's 0.7381 (centring on the joint mean removes the shared axis, so a
lower number here is the projection working, not a weaker teacher).

The student distilled from it is the first to pass gate 2 (probe_inherit.py) and
gives up 0.055 of mean object AUC to do it (probe_retention.py).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import data as data_mod
import distill as distill_mod
import probe_project as pp
import probe_teacher as pt


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", default="train2017")
    ap.add_argument("--subset", type=int, default=0, metavar="N",
                    help="encode only the N training images distill.py --subset N "
                         "would use, plus the whole holdout (0 = the entire split)")
    ap.add_argument("--holdout", type=int, default=1000,
                    help="must match distill.py's --holdout")
    ap.add_argument("--bank", type=int, default=3000,
                    help="images and captions for the PCA fit (probe_project used 3000)")
    ap.add_argument("--dim", type=int, default=512)
    ap.add_argument("--batch", type=int, default=32)
    # Centring on the joint mean is what widened the gate-2 margin, and it is
    # also the prime suspect for the object-AUC loss probe_retention.py found:
    # the shared cone axis it deletes is not pure nuisance, some category
    # structure lies along it. alpha makes that a dial instead of a decision.
    ap.add_argument("--alpha", type=float, default=1.0, metavar="A",
                    help="centring strength: project (v - A*mu) @ w. "
                         "1.0 = full centring (what passed the gates), 0 = none")
    # The encode is the expensive step and the projection is not. Keeping the
    # unprojected vectors turns every future basis, dim and alpha question into
    # a matmul instead of another hour of SO400M.
    ap.add_argument("--from-raw", type=Path, default=None, metavar="NPY",
                    help="re-project a previous run's .raw.npy instead of encoding")
    args = ap.parse_args()

    name, pre = pp.SPEC.split(":")
    names = data_mod.image_list(args.split)
    train_idx, hold_idx = distill_mod.split_indices(len(names), args.holdout)
    if args.subset:
        train_idx = train_idx[:args.subset]
    need = np.sort(np.concatenate([train_idx, hold_idx]))
    print(f"teacher  : {pp.SPEC}")
    print(f"images   : {len(need)} of {len(names)} ({args.split}: "
          f"{len(train_idx)} train + {len(hold_idx)} holdout)")

    base = f"emb_{args.split}_SO400M" + (f"_s{args.subset}" if args.subset else "")
    raw_path = data_mod.CACHE / f"{base}.raw.npy"
    basis = data_mod.CACHE / f"{base}.basis.npz"

    if args.from_raw:
        # mu and w do not depend on alpha - alpha is applied at projection time -
        # so one fitted basis serves every centring strength. It has to be the
        # basis fitted beside *this* raw file, not the one --subset happens to
        # name, because --from-raw's whole point is re-using a bigger encode.
        raw_path = args.from_raw
        basis = Path(str(args.from_raw).replace(".raw.npy", ".basis.npz"))
        if not basis.exists():
            raise SystemExit(f"{basis}: no basis beside {args.from_raw.name}. The "
                             f"map has to be the one fitted with those vectors.")
        raw = np.load(raw_path).astype(np.float32)
        b = np.load(basis)
        mu, w = b["mu"], b["w"]
        var = float(b["var"]) if "var" in b.files else float("nan")
        # A raw file built for a smaller subset has zero rows where this run
        # needs vectors, and a zero row projects to NaN without complaining.
        blank = int((np.abs(raw[need]).sum(axis=1) == 0).sum())
        if blank:
            raise SystemExit(f"{raw_path.name}: {blank} of {len(need)} rows this "
                             f"run needs are all-zero - it was encoded for a "
                             f"smaller --subset.")
        print(f"re-using : {raw_path.name} + {basis.name}, no encoding")
    else:
        device = pt.pick_device()
        print(f"device   : {device}")
        import open_clip
        model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pre)
        model = model.to(device).eval()
        tok = open_clip.get_tokenizer(name)

        # 1. The map. Same bank, seed and size as the run that passed the gates.
        print(f"\nfitting the basis on {args.bank} val2017 images + {args.bank} captions")
        iv, tv = pp.build_bank(model, preprocess, tok, device, args.bank, "val2017", 0)
        mu, w, var = pp.fit_pca(np.concatenate([iv, tv]), args.dim)
        print(f"basis    : {iv.shape[1]} -> {args.dim}, {var * 100:.1f}% of variance")

        # 2. Encode, unprojected. The projection is a matmul and is applied after,
        #    so the expensive artifact on disk is the one that survives a change
        #    of mind about alpha or dim.
        raw = np.zeros((len(names), iv.shape[1]), dtype=np.float16)
        idir = data_mod.image_dir(args.split)
        t0 = time.time()
        with torch.no_grad():
            for i in range(0, len(need), args.batch):
                chunk = need[i:i + args.batch]
                batch = torch.stack([
                    preprocess(Image.open(idir / names[j]).convert("RGB"))
                    for j in chunk]).to(device)
                v = model.encode_image(batch).float()
                v = v / v.norm(dim=-1, keepdim=True)
                raw[chunk] = v.cpu().numpy().astype(np.float16)
                done = i + len(chunk)
                rate = done / max(time.time() - t0, 1e-9)
                print(f"\r  {done}/{len(need)}  {rate:.1f} img/s  "
                      f"eta {(len(need) - done) / max(rate, 1e-9) / 60:.0f} min",
                      end="", flush=True)
        print()
        np.save(raw_path, raw)
        np.savez(basis, mu=mu, w=w, var=var)
        print(f"wrote    : {raw_path.name} ({raw.nbytes / 2**20:.0f} MiB, unprojected)")
        raw = raw.astype(np.float32)

    dim = w.shape[1]
    if dim != args.dim:
        raise SystemExit(f"{basis.name} maps to {dim}, not --dim {args.dim}. "
                         f"Changing the width needs a re-fit, not a re-projection.")
    out = np.zeros((len(names), dim), dtype=np.float16)
    p = (raw[need] - args.alpha * mu) @ w
    out[need] = (p / np.linalg.norm(p, axis=-1, keepdims=True)).astype(np.float16)

    stem = (f"emb_{args.split}_SO400M-pca{dim}"
            + (f"-a{args.alpha:g}" if args.alpha != 1.0 else "")
            + (f"_s{args.subset}" if args.subset else ""))
    npy = data_mod.CACHE / f"{stem}.npy"
    np.save(npy, out)
    # probe_inherit.resolve() finds the spec and the map from the target stem, so
    # every projected variant needs its own pair of sidecars beside it. alpha is
    # folded into mu here rather than carried as a third field, so every consumer
    # keeps applying the one formula it already applies: (v - mu) @ w.
    vbasis = data_mod.CACHE / f"{stem}.basis.npz"
    np.savez(vbasis, mu=args.alpha * mu, w=w, var=var)
    (data_mod.CACHE / f"{stem}.json").write_text(json.dumps({
        "teacher": pp.SPEC, "split": args.split, "dim": dim,
        "alpha": args.alpha, "source_dim": int(raw.shape[1]),
        "variance_kept": None if var != var else var,
        "bank": args.bank, "bank_split": "val2017", "bank_seed": 0,
        "subset": args.subset, "holdout": args.holdout,
        "n_images": len(names), "n_encoded": len(need),
        "raw": raw_path.name,
    }, indent=2))

    filled = out[need].astype(np.float32)
    norms = np.linalg.norm(filled, axis=1)
    cone = float(np.linalg.norm((filled / norms[:, None]).mean(axis=0)))
    print(f"\nwrote    : {npy.name} ({out.nbytes / 2**20:.1f} MiB)")
    print(f"           {vbasis.name}  <- apply this to host queries too")
    print(f"norms    : min {norms.min():.4f} max {norms.max():.4f}")
    print(f"cone     : {cone:.4f}  (ViT-B/16's is 0.7381; centring should lower it)")
    ok = bool(np.all(np.abs(norms - 1.0) < 1e-2))
    print("\nRESULT : " + ("PASS" if ok else "FAIL - projected targets are not unit-norm"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
