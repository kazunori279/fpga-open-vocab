# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""M15 Stage 0: does the fixed-point requantize contract cost any accuracy?

M15 moves the requantize epilogue off the MCU and into the tile, so DRAIN can
return one byte per accumulator instead of four. `firmware/encoder.h`'s
fgx_requant() is `(float)(acc + bias) * mult`, and a float multiply plus an
IEEE round is not what a 24-LE datapath wants to be. The replacement is
`code = clamp(((acc + bias) * M + 2^(s-1)) >> s, 0, 255)` with
`M = round(mult * 2^s)` held in [2^17, 2^18).

model/export.py's docstring already argued the other way, and was right at the
time: "Requantization is float, deliberately. The RP2350 has a single-precision
FPU, and the alternative - a fixed-point multiplier and shift per channel -
buys nothing here". What changed is *where* the epilogue runs. On an FPU it
buys nothing; on the return lane it buys three quarters of the largest line in
the frame.

## What this measures, and what it does not

The contract change is not an approximation of the float path - it is a
different one, and slightly more accurate: `(acc + bias) * M` is an exact
integer product where `(float)(acc + bias) * mult` rounds twice, once fitting a
26-bit accumulator into a 24-bit mantissa and once on the product. So "how far
from the float path" is the wrong question to ask alone. The question is
whether retention moves.

Both pipelines are run through `export.run_int()`, the numpy integer reference,
so the only thing that differs between the two columns is the epilogue. The
absolute AUC here is *not* directly comparable to model/evaluate.py's, which
scores the PyTorch fake-quant model: this is the true integer pipeline, which
evaluate.py has never scored. The comparison that matters is column against
column, on the same images and the same queries.

Weights default to M14's shipped configuration (--wbits 4 --wsearch --ends8),
because that is the model the contract change actually lands on.

    uv run tools/probe_rq.py --run train2017 --images 600
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import data
import distill
import export as export_mod
import quantize as quant_mod
import student as student_mod
import teacher as teacher_mod
from evaluate import auc, teacher_bundle, AUC_CLEARS, MIN_POS

SPLIT = "val2017"
ENDS = ("conv0", "head")


def pin_to_8(qmodel, names):
    """probe_int4.py's pin_to_8, verbatim - the same two lines, search=False."""
    layers = {f"conv{i}": m for i, m in enumerate(qmodel.features)}
    layers["head"] = qmodel.head
    for n in names:
        m = layers[n]
        m.w_qmax = 127.0
        m.w_scale = quant_mod.pick_w_scale(m.layer.weight.detach(), 127.0, False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="run dir under model/runs/")
    ap.add_argument("--images", type=int, default=600,
                    help="eval images; run_int is a numpy reference, not fast")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--calib-batches", type=int, default=8)
    ap.add_argument("--wbits", type=int, default=4, choices=(4, 8))
    ap.add_argument("--no-wsearch", action="store_true")
    ap.add_argument("--no-ends8", action="store_true")
    args = ap.parse_args()

    run = ROOT / "model/runs" / args.run
    ckpt = torch.load(run / "student.pt", map_location="cpu", weights_only=False)
    device = torch.device("cpu")        # run_int is numpy; keep everything here

    names = data.image_list(SPLIT)
    queries = json.loads((data.CACHE / f"queries_{SPLIT}.json").read_text())
    teacher_emb, encode_text, calib_targets, teacher_label = teacher_bundle(
        ckpt["teacher"], SPLIT, ckpt["split"], device)

    trained_on = ckpt["split"]
    all_idx = (np.array(sorted(ckpt["holdout"])) if trained_on == SPLIT
               else np.arange(len(names)))
    # A contiguous prefix, not a random sample: the subset has to be the same
    # one every time this is re-run, and a seed is one more thing to get wrong.
    eval_idx = all_idx[:args.images]

    model = student_mod.Student()
    model.load_state_dict(ckpt["state_dict"])
    folded = quant_mod.fold_bn(model).to(device)
    qmodel = quant_mod.quantize(folded, w_bits=args.wbits,
                                search=not args.no_wsearch).to(device)
    if not args.no_ends8:
        pin_to_8(qmodel, ENDS)

    hold = set(ckpt["holdout"]) if trained_on == SPLIT else set()
    calib_idx = np.array([i for i in range(len(data.image_list(trained_on)))
                          if i not in hold])
    calib_loader = DataLoader(
        distill.DistillSet(trained_on, data.image_list(trained_on), calib_idx,
                           calib_targets, distill.student_transform(False, False)),
        batch_size=args.batch, shuffle=False, num_workers=4)
    torch.manual_seed(0)                # export.py seeds the same way, same reason
    n_calib = quant_mod.calibrate(qmodel, calib_loader, device, args.calib_batches)

    layers, head_in_scale = export_mod.build_layers(qmodel)
    in_scale = float(qmodel.features[0].in_scale)

    cfg = (f"int{args.wbits}"
           + ("" if args.no_wsearch else " mse") + ("" if args.no_ends8 else " ends8"))
    print(f"student  : {args.run}, epoch {ckpt['epoch']}")
    print(f"teacher  : {teacher_label}")
    print(f"weights  : {cfg}   (M14's shipped configuration)")
    print(f"eval     : {len(eval_idx)} of {len(all_idx)} {SPLIT} images, geometry crop")
    print(f"int8 act : calibrated on {n_calib} training images")
    print(f"pipeline : export.run_int(), the numpy integer reference - so the "
          f"absolute AUC\n           is not model/evaluate.py's number; the "
          f"column-to-column gap is\n")

    # The (M, s) table the fabric would hold, and what it costs in precision
    # before a single image is run. A layer whose M fell below 2^17 would mean
    # the pick failed and nothing downstream is worth reading.
    print(f"{'L':<4}{'cout':>6}{'mult min':>12}{'mult max':>12}"
          f"{'s':>10}{'M min':>9}{'worst rel err':>15}")
    n_code = len(layers) - 2            # conv7 and the head keep the float mult
    for i, layer in enumerate(layers):
        M, s = export_mod.rq_pick(layer.mult)
        rel = np.abs(M / np.exp2(s.astype(np.float64)) - layer.mult) / layer.mult
        tag = "" if i < n_code else "   float out, keeps mult"
        print(f"{i:<4}{len(layer.mult):>6}{layer.mult.min():>12.3e}"
              f"{layer.mult.max():>12.3e}{f'{s.min()}..{s.max()}':>10}"
              f"{M.min():>9}{rel.max():>15.2e}{tag}")
    print()

    # Input codes. Deliberately taken through the same transform and the same
    # in_scale export.py uses, so this starts where the C starts.
    loader = DataLoader(
        distill.DistillSet(SPLIT, names, eval_idx, teacher_emb,
                           distill.student_transform(False, False)),
        batch_size=args.batch, shuffle=False, num_workers=6)
    px = np.concatenate([p.numpy() for p, _ in loader])
    codes = np.clip(np.rint(px / in_scale), -127, 127).astype(np.int8)

    e_float = np.empty((len(codes), 512), dtype=np.float64)
    e_fixed = np.empty_like(e_float)
    t0 = time.time()
    for i in range(len(codes)):
        # Both flags explicit. run_int()'s default is the shipped contract, and
        # a column here that silently followed it would stop being a control.
        e_float[i] = export_mod.run_int(codes[i], layers, head_in_scale, fixed=False)
        e_fixed[i] = export_mod.run_int(codes[i], layers, head_in_scale, fixed=True)
        if i % 50 == 0:
            el = time.time() - t0
            print(f"\r  {i + 1}/{len(codes)}  {el:.0f}s elapsed, "
                  f"{el / (i + 1) * (len(codes) - i - 1):.0f}s left", end="", flush=True)
    print(f"\r  {len(codes)}/{len(codes)}  {time.time() - t0:.0f}s" + " " * 24)

    nf = e_float / np.linalg.norm(e_float, axis=1, keepdims=True)
    nx = e_fixed / np.linalg.norm(e_fixed, axis=1, keepdims=True)
    per_img = (nf * nx).sum(axis=1)

    query_names = [q["name"] for q in queries["queries"]]
    text = encode_text(query_names)
    pos_of = {q["name"]: set(q["pos"]) for q in queries["queries"]}
    excl_of = {q["name"]: set(q["excluded"]) for q in queries["queries"]}
    where = {int(v): k for k, v in enumerate(eval_idx)}

    scored = []
    for qi, q in enumerate(query_names):
        keep = np.array([i for i in eval_idx if i not in excl_of[q]])
        if len(keep) == 0:
            continue
        positive = np.array([i in pos_of[q] for i in keep])
        if int(positive.sum()) < MIN_POS or int((~positive).sum()) < MIN_POS:
            continue
        sel = np.array([where[int(i)] for i in keep])
        scored.append((q, sel, keep, positive, text[qi]))

    t_auc = {q: auc(teacher_emb[keep] @ t, pos) for q, _, keep, pos, t in scored}
    clears = [q for q, v in t_auc.items() if v >= AUC_CLEARS]

    def report(name, emb):
        a = {q: auc(emb[sel] @ t, pos) for q, sel, _, pos, t in scored}
        ret = sum(a[q] >= AUC_CLEARS for q in clears) / len(clears) if clears else 0.0
        good = sum(v >= AUC_CLEARS for v in a.values())
        print(f"{name:<28}{f'{good} / {len(a)}':>12}{np.mean(list(a.values())):>11.4f}"
              f"{ret:>12.0%}")
        return a

    print(f"\n{'epilogue':<28}{'AUC>=0.80':>12}{'mean AUC':>11}{'retention':>12}")
    a_f = report("float  (acc+b)*mult", nf)
    a_x = report("fixed  ((acc+b)*M+r)>>s", nx)

    moved = {q: a_x[q] - a_f[q] for q in a_f if abs(a_x[q] - a_f[q]) > 1e-9}
    print(f"\nqueries whose AUC moved at all : {len(moved)} of {len(a_f)}")
    if moved:
        worst = sorted(moved.items(), key=lambda kv: -abs(kv[1]))[:8]
        print("  " + "  ".join(f"{q} {d:+.5f}" for q, d in worst))
    print(f"largest single-query change    : "
          f"{max((abs(v) for v in moved.values()), default=0.0):.5f}")
    print(f"embedding cosine, float vs fixed:"
          f" min {per_img.min():.7f}  mean {per_img.mean():.7f}")
    print(f"teacher mean AUC {np.mean(list(t_auc.values())):.3f}, {len(clears)} of "
          f"{len(t_auc)} queries at >= {AUC_CLEARS:.2f} (retention's denominator)")

    # The gate. Retention is the bar model/evaluate.py applies and the one M14
    # was signed off against, so it is the one that decides here too.
    if not clears:
        print("\nRESULT : FAIL - no query cleared the teacher bar; --images too small")
        return 1
    r_f = sum(a_f[q] >= AUC_CLEARS for q in clears) / len(clears)
    r_x = sum(a_x[q] >= AUC_CLEARS for q in clears) / len(clears)
    ok = r_x >= r_f and np.mean(list(a_x.values())) >= np.mean(list(a_f.values())) - 0.002
    print("\nRESULT : " + ("GO - the fixed-point contract does not cost retention"
                           if ok else "NO-GO - the contract change moved accuracy"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
