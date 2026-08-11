# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Is 4-bit worth it? Sweep the student's weight width against the real gate.

    uv run --script tools/probe_int4.py --run train2017
    uv run --script tools/probe_int4.py --run so400m-full

WHY THE QUESTION IS ABOUT WEIGHTS, AND WHY IT IS WORTH ASKING
--------------------------------------------------------------
The board's frame budget is dominated by moving bytes, not by arithmetic. From
rtl/build/gemm_top.res.csv and the M9 timing: WGT is 2.219 MB per frame and RUN
is 313 ms of the 851 ms total. Both are linear in the weight width, so halving
it is the single largest lever available without an RTL redesign - and unlike
everything else on the list it needs no new silicon, only a different blob.

There are two versions of the win and they are very different in cost:

  cheap   4-bit weights on the wire only. The host packs two weights per byte,
          the fabric unpacks into wbuf, the MAC is untouched. Buys ~67 ms.
  packed  two weights per multiplier, via w0 + (w1 << s). rtl/gemm_tile.v:466 is
          `wire signed [17:0] p_j = a_q * w_j;` - a 10x8 product in an 18x18
          hard multiplier. With a 10-bit activation, s >= 14, so the packed
          operand is exactly 18 bits: it fits, barely, and signs make it
          delicate. Buys ~223 ms.

Neither is worth scoping before this script says the accuracy is there, which is
what it measures. Note the packed ceiling: core 1 is busy 509 ms against core
0's 639 ms of wire, so a full 4-bit win floors the frame near 600 ms rather than
630 - the bottleneck moves rather than disappearing.

int2 was considered and dropped. Two-packing is all an 18x18 multiplier gives at
either width (three needs 22 bits), so RUN is identical for int4 and int2 and
only WGT differs - 1.110 vs 0.555 MB, about 34 ms of 851. The 4x route needs
NMAC 8 -> 32 and a 1024-bit accumulator, ~52 RAM blocks against the 21/24 the
design already uses. Blocked by RAM, for 4% of a frame.

WHAT IS SWEPT, AND THE ONE THING THAT WOULD HAVE FAKED THE ANSWER
------------------------------------------------------------------
Activations stay at 8 bits throughout. They are a different transfer (ACT), a
different resource, and mixing them in would make a two-variable result out of a
one-variable question.

Max-abs per-channel scaling is right at 8 bits and increasingly wrong below it:
the scale is set by the largest weight in the channel, and at 7 codes an outlier
costs most of the resolution the rest of the channel needed. An int4 row
produced by the int8-era rule measures the rule, not int4. So every sub-8 row is
run twice - `maxabs` and `mse`, the latter searching 36 clip ratios per channel -
and both columns are printed. If they differ, the scale rule was the finding.

conv0+head at 8 bits is included because it is nearly free: those two layers are
a small fraction of the weights, and the first and last layers of a small
network are the usual place low-bit PTQ breaks.

THE BAR
-------
Not "does AUC drop" - it will, slightly, and so does int8. The bar is the one
model/evaluate.py already applies: retention, the share of teacher-clearing
queries the student still clears. int8 costs nothing by that measure (0.899 ->
0.899 on the shipped student). A width that holds retention within a point or
two is a width the board can have; one that does not is not worth 200 ms.

WHAT IT FOUND, 2026-08-08: 4 BITS YES, 3 BITS NO, AND THE SCALE RULE IS HALF OF IT
-----------------------------------------------------------------------------------
The shipped student (train2017, 118k, ViT-B/16), 5000 val2017 images, 67 queries:

  weights          AUC>=0.80  mean AUC  retention  cos-fp32  bits/w  WGT MB
  fp32 (reference)   59 / 67     0.899       94%    1.0000      32   8.876
  int8               59 / 67     0.899       94%    0.9982    8.00   2.219
  int6 mse           59 / 67     0.899       94%    0.9964    6.00   1.664
  int5 mse           58 / 67     0.887       92%    0.9880    5.00   1.387
  int4 maxabs        47 / 67     0.834       75%    0.9491    4.00   1.109
  int4 mse           51 / 67     0.851       81%    0.9526    4.00   1.109
  int4 mse ends8     59 / 67     0.886       94%    0.9800    4.38   1.214
  int3 mse           24 / 67     0.766       38%    0.9034    3.00   0.832
  int3 mse ends8     37 / 67     0.819       59%    0.9283    3.47   0.962

**int4 with searched scales and 8-bit ends is free by the gate's own measure**:
59 of 67 queries and 94% retention, identical to int8, for 0.013 of mean AUC.
Down to 6 bits nothing moves at all.

**Most of what naive int4 loses is the quantizer, not the width.** maxabs 75% ->
mse 81% -> ends8 94%. Had this been run the obvious way it would have reported
int4 as a 19-point retention loss and closed the question wrongly.

**3 bits is not close.** Even with both fixes, 59% retention and 30 queries
lost. The cliff is between 4 and 3, which is where the per-channel distribution
stops having enough codes to keep its shoulders.

IT REPLICATES ON THE BEST STUDENT, WHICH IS THE ONE THAT WOULD SHIP
--------------------------------------------------------------------
so400m-full-a05 (118k, SigLIP 2 SO400M, alpha 0.5), whose teacher clears all 67:

  fp32               61 / 67     0.895       91%    1.0000      32
  int8               61 / 67     0.899       91%    0.9908    8.00
  int6 mse           62 / 67     0.896       93%    0.9785    6.00
  int5 mse           61 / 67     0.889       91%    0.9402    5.00
  int4 maxabs        50 / 67     0.845       75%    0.7870    4.00
  int4 mse           49 / 67     0.853       73%    0.7425    4.00
  int4 mse ends8     61 / 67     0.887       91%    0.8865    4.38
  int3 mse            8 / 67     0.670       12%    0.3864    3.00
  int3 mse ends8     37 / 67     0.796       55%    0.6255    3.47

Same verdict, arrived at from a different teacher and a 0.5866 -> 0.6448 wider
target cone: **int4 with searched scales and 8-bit ends is free, int3 is not.**
Two independent students agreeing is what makes this a property of the network
rather than of one checkpoint.

The two students do *not* agree on the scale search, though: it is worth +6
retention points on the shipped student (75% -> 81%) and -2 here (75% -> 73%),
which is inside the noise of a 67-query mean either way. So the claim above -
"most of what naive int4 loses is the quantizer" - should be read narrowly. The
lever that survives both checkpoints is the 8-bit ends, worth +13 and +18
points; the MSE clip is a wash outside conv0 and the head. It is kept because it
costs nothing at run time (the scale is folded once, offline) and because
reporting both columns is what showed the search was not the load-bearing part.

One difference worth noting rather than smoothing over: the embedding is more
fragile in the SO400M space. cos-fp32 at int4 ends8 is 0.886 here against 0.980
on the shipped student, and int3 collapses to 0.386 against 0.928. The centred
SO400M targets are spread over more of the sphere, so the same absolute
perturbation costs more angle. It does not show up in retention at 4 bits, and
it is the reason not to read the int3 rows as merely worse.

WHAT IT IS WORTH ON THE BOARD
-----------------------------
Against the recorded 851 ms frame (docs/architecture.md, "One frame, 851 ms"): wire 639 (326
ACT/WGT/DRAIN + 313 RUN), core 0's non-overlappable 111, stall 66, scaffolding
35; core 1 busy 509, hiding inside the wire.

ACT + WGT is 167 ms post-jumper and WGT is 2.219 of those 3.976 MB, so WGT is
about 93 ms. At 4.38 bits/weight it becomes 51.

  cheap   wire only, MAC untouched:  -42 ms  ->  ~809 ms   (5%)
  packed  two weights per multiplier: RUN 313 -> 157, plus the 42
                                      wire 639 -> 441      ->  ~655 ms  (23%)

The packed figure is a floor, not a subtraction: at 441 ms of wire, core 1's 509
no longer fits inside it, so **core 1 becomes the critical path** and the frame
lands near 509 + 111 + 35 rather than at 851 - 198. Anything past this is core
1 work, not link work.

One design note the table implies: the ends8 row needs a per-layer width, so
whatever packs the weights must do it per layer. That argues for packing on the
host, which also keeps `gb_weights()` on core 1 a memcpy rather than giving the
core that is about to become the bottleneck a new bit-shuffling job.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import data
import distill
import quantize as quant_mod
import student as student_mod
import teacher as teacher_mod
from evaluate import auc, embed_students, teacher_bundle, AUC_CLEARS, MIN_POS

SPLIT = "val2017"
# The recorded per-frame weight transfer at 8 bits, from the M9 timing. Every
# other width is this scaled - WGT is linear in the width and nothing else in
# the layout changes.
WGT_MB_INT8 = 2.219
ENDS = ("conv0", "head")

# (w_bits, search, keep8) - keep8 names layers pinned to 8 bits regardless.
CONFIGS = [
    (8, False, ()),
    (6, True, ()),
    (5, True, ()),
    (4, False, ()),
    (4, True, ()),
    (4, True, ENDS),
    (3, True, ()),
    (3, True, ENDS),
]


def label(w_bits, search, keep8):
    return (f"int{w_bits}"
            + ("" if w_bits == 8 else f" {'mse' if search else 'maxabs'}")
            + (" ends8" if keep8 else ""))


def pin_to_8(qmodel, names):
    """Put named layers back on the 8-bit grid after a low-bit build."""
    layers = {f"conv{i}": m for i, m in enumerate(qmodel.features)}
    layers["head"] = qmodel.head
    for n in names:
        m = layers[n]
        m.w_qmax = 127.0
        m.w_scale = quant_mod.pick_w_scale(m.layer.weight.detach(), 127.0, False)


def weight_bits(qmodel, keep8):
    """Mean bits per weight, which is what WGT and RUN both scale with."""
    layers = {f"conv{i}": m for i, m in enumerate(qmodel.features)}
    layers["head"] = qmodel.head
    tot = bits = 0
    for n, m in layers.items():
        k = m.layer.weight.numel()
        tot += k
        bits += k * (8 if n in keep8 else int(np.log2(m.w_qmax + 1)) + 1)
    return bits / tot


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="run dir under model/runs/")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--calib-batches", type=int, default=8)
    args = ap.parse_args()

    run = ROOT / "model/runs" / args.run
    ckpt = torch.load(run / "student.pt", map_location="cpu", weights_only=False)
    device = teacher_mod.pick_device()

    names = data.image_list(SPLIT)
    queries = json.loads((data.CACHE / f"queries_{SPLIT}.json").read_text())
    teacher_emb, encode_text, calib_targets, teacher_label = teacher_bundle(
        ckpt["teacher"], SPLIT, ckpt["split"], device)

    trained_on = ckpt["split"]
    if trained_on == SPLIT:
        eval_idx = np.array(sorted(ckpt["holdout"]))
    else:
        eval_idx = np.arange(len(names))

    model = student_mod.Student()
    model.load_state_dict(ckpt["state_dict"])
    folded = quant_mod.fold_bn(model).to(device)

    hold = set(ckpt["holdout"]) if trained_on == SPLIT else set()
    calib_idx = np.array([i for i in range(len(data.image_list(trained_on)))
                          if i not in hold])

    def calib_loader():
        from torch.utils.data import DataLoader
        return DataLoader(
            distill.DistillSet(trained_on, data.image_list(trained_on), calib_idx,
                               calib_targets, distill.student_transform(False, False)),
            batch_size=args.batch, shuffle=False, num_workers=4)

    print(f"student  : {args.run}, epoch {ckpt['epoch']}")
    print(f"teacher  : {teacher_label}")
    print(f"eval     : {len(eval_idx)} {SPLIT} images, geometry crop "
          f"(model/evaluate.py's default, so the int8 row is comparable to it)")
    print(f"activations held at 8 bits throughout; only the weights move\n")

    tf = distill.student_transform(False, False)
    query_names = [q["name"] for q in queries["queries"]]
    text = encode_text(query_names)
    pos_of = {q["name"]: set(q["pos"]) for q in queries["queries"]}
    excl_of = {q["name"]: set(q["excluded"]) for q in queries["queries"]}
    where = {int(v): k for k, v in enumerate(eval_idx)}

    # The query set is fixed, so the rows to score are computed once and every
    # width is judged on exactly the same ones. Recomputing them per width would
    # let a config quietly change its own denominator.
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
    fp32 = embed_students({"fp32": folded}, SPLIT, names, eval_idx,
                          teacher_emb, device, args.batch, tf)["fp32"]

    def report(name, emb, bits):
        a = {q: auc(emb[sel] @ t, pos) for q, sel, _, pos, t in scored}
        ret = sum(a[q] >= AUC_CLEARS for q in clears) / len(clears)
        good = sum(v >= AUC_CLEARS for v in a.values())
        cos32 = float((emb * fp32).sum(axis=1).mean())
        cost = "" if bits is None else f"{WGT_MB_INT8 * bits / 8:>9.3f}"
        print(f"{name:<20}{good:>5} /{len(a):3}{np.mean(list(a.values())):>10.3f}"
              f"{ret:>11.0%}{cos32:>11.4f}{bits if bits else 8:>9.2f}{cost}")
        return np.mean(list(a.values())), ret

    print(f"{'weights':<20}{'AUC>=0.80':>9}{'mean AUC':>10}{'retention':>11}"
          f"{'cos-fp32':>11}{'bits/w':>9}{'WGT MB':>9}")
    print(f"{'fp32 (reference)':<20}", end="")
    a0 = {q: auc(fp32[sel] @ t, pos) for q, sel, _, pos, t in scored}
    r0 = sum(a0[q] >= AUC_CLEARS for q in clears) / len(clears)
    print(f"{sum(v >= AUC_CLEARS for v in a0.values()):>5} /{len(a0):3}"
          f"{np.mean(list(a0.values())):>10.3f}{r0:>11.0%}{1.0:>11.4f}"
          f"{32:>9}{WGT_MB_INT8 * 4:>9.3f}")

    for w_bits, search, keep8 in CONFIGS:
        qm = quant_mod.quantize(folded, w_bits=w_bits, search=search).to(device)
        if keep8:
            pin_to_8(qm, keep8)
        quant_mod.calibrate(qm, calib_loader(), device, args.calib_batches)
        emb = embed_students({"q": qm}, SPLIT, names, eval_idx, teacher_emb,
                             device, args.batch, tf)["q"]
        report(label(w_bits, search, keep8), emb, weight_bits(qm, keep8))
        del qm

    print(f"\nteacher mean AUC {np.mean(list(t_auc.values())):.3f}, "
          f"{len(clears)} of {len(t_auc)} queries at >= {AUC_CLEARS:.2f} "
          f"(retention's denominator)")
    print(f"WGT MB is the recorded {WGT_MB_INT8} MB int8 transfer scaled by the "
          f"width; RUN's 313 ms scales the same way if the MAC packs two.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
