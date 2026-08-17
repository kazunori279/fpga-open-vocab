# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""M4's gate: can a 1.4M-param student still separate the queries?

    uv run model/evaluate.py --split val2017

Reports **three rows** - teacher, student fp32, student int8 - because three
different failures look identical if you only measure the student:

  * the task is impossible (CLIP itself can't do it) -> the appliance idea is wrong
  * distillation failed (a bug, or too little data)  -> fixable
  * the student is genuinely too small               -> the real NO-GO

The teacher row is the ceiling everything else is read against. A student number
without it is uninterpretable.

**AUC, not accuracy.** AUC needs no threshold, and threshold calibration is a
deployment detail; reporting a tuned-threshold accuracy as the gate would
flatter the result. The per-query table matters more than any mean - the
appliance only has to work for the queries that matter, and an average over 80
COCO classes hides which ones survived.

**`--emit-thresholds` does not walk that back.** The gate is still AUC. But M9
has to answer yes or no on a live camera, and that needs a number from
somewhere; the alternative is a constant somebody typed. So the same int8 scores
the AUC column is computed from also yield, per query, the score at a chosen
false-positive rate on the negatives - a deployment artifact written to a
separate file, never mixed into the verdict. Read it with the AUC beside it: a
threshold on a query at AUC 0.6 separates nothing, and the file carries both so
`host/demo.py` can say so out loud.

**And it emits the background, not just the cutoff, because M9's first bench run
proved the cutoff alone is not usable.** With `"cup" "person" "book" "laptop"`
resident and a book on the table, the raw cosines ranked the book above the cup
in 11 of 12 frames - the perception was right - and the yes/no verdict still
said `cup`, because `book`'s threshold is 0.273 and `cup`'s is 0.266 and the
whole signal was 0.009 wide. A CLIP cosine carries a per-query offset that is a
property of the *sentence*, not of the picture, and here it was larger than the
picture. So each query also gets the mean and standard deviation of its own
negatives, and the device scores `z = (cos - mean_neg) / std_neg`. Offsets
cancel, the queries land on one scale, and `z_threshold` comes out near 1.28 for
all of them - which is just what the (1-fpr) quantile of a background is.

Emit it with `--geometry camera`. The thresholds are for a device looking
through an ArduCam, and the training framing is not what that device sees.

**WHICH TEACHER IS NOT A FLAG.** `model/distill.py` writes the teacher's name
into the checkpoint and `teacher_bundle()` derives the reference bank, the int8
calibration targets and the text encoder from it, so a student distilled from a
`tools/teacher_swap.py` target file is scored against *its own* teacher rather
than against unrelated text. Nothing about the incumbent path changed: a
ViT-B/16 student still reports teacher mean 0.952 and 94% retention.

WHAT THE SIX STUDENTS SCORE, 2026-08-09
---------------------------------------
  run                 images  teacher       t.mean   fp32    int8   retain
  train2017 (shipped)   118k  ViT-B/16       0.952  0.899   0.899     94%
  _sieve_infonce-0.3     30k  ViT-B/16       0.952  0.845   0.843     83%
  so400m-s30k            30k  SO400M a1      0.970  0.800   0.802     55%
  so400m-s30k-a05        30k  SO400M a0.5    0.983  0.835   0.836     73%
  so400m-full           118k  SO400M a1      0.970  0.856   0.859     80%
  so400m-full-a05       118k  SO400M a0.5    0.983  0.895   0.899     91%

**Data is worth a lot here**: same teacher, same recipe, 4x the images moved
ViT-B/16 0.845 -> 0.899 and SO400M 0.800 -> 0.856. Nearly the same slope twice,
which is what made "30k is where to expect a capacity-and-data shortfall" a
prediction rather than an excuse.

**And the centring strength was worth as much again.** alpha 0.5 against alpha 1
- the same 118287 images, the same recipe, only a weaker mu subtracted - is
0.856 -> 0.895 and 80% -> 91%. tools/probe_alpha.py picked alpha 0.5 off the
*teacher's* AUC and predicted it would cost nothing; at the student it turned out
to be the second-largest lever in the whole swap. so400m-full-a05 is within
0.004 of the shipped student's object AUC while separating open from closed
books at +5.88 sd where the shipped one is at -4.64 (tools/probe_noise.py).

The 30k alpha-0.5 row exists to keep those two claims apart, and it does: the
four SO400M cells make a clean 2x2 in which data buys +0.056/+0.060 and centring
buys +0.035/+0.039, independently. Neither lever is a substitute for the other,
and both were needed to get back to 0.899.

**int8 still costs nothing** anywhere in the table - it is 0.899 on both of the
two best students, the same finding M4 recorded. 4-bit weights are nearly free
too, but only with searched scales and 8-bit end layers; see tools/probe_int4.py.

The ViT-B/16 fp32 numbers here are ~0.01 below tools/probe_retention.py's for
the same checkpoints. That script encodes text with `ViT-B-16-quickgelu`;
model/teacher.py, and therefore this gate and the shipped weights, use
`ViT-B-16` against the same 'openai' tag. The SO400M numbers agree exactly
because both paths build that text the same way.
"""

import argparse
import json
import sys
from pathlib import Path

import data
import distill
import numpy as np
import quantize as quant_mod
import student as student_mod
import teacher as teacher_mod
import torch
from torch.utils.data import DataLoader

# Below this many positives (or negatives) an AUC is noise, so the query is
# dropped and the count of dropped queries is printed. Never silently.
MIN_POS = 10
AUC_CLEARS = 0.80
CONCEPT_FLOOR = 0.75
GO_RETENTION = 0.60
MARGINAL_RETENTION = 0.30


def teacher_bundle(tag: str, split: str, trained_on: str, device):
    """The three things this script needs from a teacher, picked by the checkpoint.

    This gate used to name one teacher in three places - the reference bank, the
    int8 calibration targets and the text encoder - which meant it could only
    score students distilled from CLIP ViT-B/16. A student trained on a
    tools/teacher_swap.py target file emits vectors in a different space, so the
    old path would have compared it to unrelated text and printed a confident,
    meaningless number. The pairing is not a flag because it is not a choice:
    model/distill.py writes the teacher's name into the checkpoint and the three
    pieces follow from it.

    Returns (emb, encode_text, calib_targets, label).

    THE BASIS IS THE STUDENT'S OWN, DELIBERATELY
    --------------------------------------------
    For a projected teacher the eval split's images are re-projected with the map
    that came with the *training* targets, not with the map teacher_swap.py fitted
    beside the eval split's own file. Both are fitted on the same bank (val2017,
    3000, seed 0) so they should agree to within float noise, but "should" is not
    a measurement: the student was trained to emit vectors in one specific space,
    and that is the space its ceiling has to be measured in.
    """
    basis_path = data.CACHE / f"{tag}.basis.npz"
    if not basis_path.exists():
        # The incumbent, unchanged: cached ViT-B/16 images, ViT-B/16 text.
        def encode_text(qnames):
            clip_model, _ = teacher_mod.load_clip(device)
            return teacher_mod.encode_queries(clip_model, qnames, device)
        return (teacher_mod.load_cache(split).astype(np.float32), encode_text,
                teacher_mod.load_cache(trained_on), teacher_mod.tag())

    side = json.loads((data.CACHE / f"{tag}.json").read_text())
    b = np.load(basis_path)
    mu, w = b["mu"], b["w"]

    def project(v):
        v = v / np.linalg.norm(v, axis=-1, keepdims=True)
        p = (v - mu) @ w
        return (p / np.linalg.norm(p, axis=-1, keepdims=True)).astype(np.float32)

    # The unprojected vectors are per-split and the sidecar records the training
    # split's filename, so the eval split's is that name with the split swapped -
    # one token, from a recorded string, rather than a reconstructed convention.
    # Target files written before teacher_swap.py kept the raw vectors have no
    # such string; for those the name comes off the tag, which is where the
    # convention started.
    raw_name = side.get("raw") or (
        f"emb_{side['split']}_{tag.split('_')[2].split('-', maxsplit=1)[0]}.raw.npy")
    raw = data.CACHE / raw_name.replace(side["split"], split)
    if not raw.exists():
        raise SystemExit(
            f"{raw} not found. Scoring a {side['teacher']} student needs that "
            f"teacher's {split} vectors:\n  uv run --script tools/teacher_swap.py "
            f"--split {split} --subset 0 --holdout 0")

    def encode_text(qnames):
        import open_clip
        name, pre = side["teacher"].split(":")
        model, _, _ = open_clip.create_model_and_transforms(name, pretrained=pre)
        model = model.to(device).eval()
        tok = open_clip.get_tokenizer(name)
        out = []
        with torch.no_grad():
            for q in qnames:
                # Ensembled in the teacher's own space, then projected - the
                # order host/demo.py uses, and the one probe_retention.py used.
                v = model.encode_text(
                    tok([t.format(q) for t in teacher_mod.TEMPLATES]).to(device)).float()
                v = (v / v.norm(dim=-1, keepdim=True)).mean(dim=0)
                out.append((v / v.norm()).cpu().numpy())
        del model
        return project(np.stack(out))

    return (project(np.load(raw).astype(np.float32)), encode_text,
            np.load(data.CACHE / f"{tag}.npy"),
            f"{side['teacher']}  +  {basis_path.name}")


def rankdata(a: np.ndarray) -> np.ndarray:
    """Ranks with ties averaged. Cheaper than a scipy dependency."""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=np.float64)
    ranks[order] = np.arange(1, len(a) + 1)
    sorted_a = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + j + 2) / 2
        i = j + 1
    return ranks


def auc(scores: np.ndarray, positive: np.ndarray) -> float:
    """Area under the ROC curve, via the Mann-Whitney U identity."""
    n_pos = int(positive.sum())
    n_neg = len(scores) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    r = rankdata(scores)
    return (r[positive].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


@torch.no_grad()
def embed_students(models: dict, split: str, names: list[str], indices: np.ndarray,
                   targets: np.ndarray, device, batch: int, transform) -> dict:
    """Run each student variant over the eval images once, in index order."""
    loader = DataLoader(
        distill.DistillSet(split, names, indices, targets, transform),
        batch_size=batch, shuffle=False, num_workers=6)
    out = {k: [] for k in models}
    for pixels, _ in loader:
        pixels = pixels.to(device)
        for key, model in models.items():
            e = model(pixels)
            out[key].append((e / e.norm(dim=-1, keepdim=True)).cpu().numpy())
    return {k: np.concatenate(v) for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val2017", help="split to evaluate on")
    ap.add_argument("--run", default=None, help="run dir under model/runs/ (default: --split)")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--calib-batches", type=int, default=8)
    ap.add_argument("--top", type=int, default=0,
                    help="show only the N best and worst queries (0 = all)")
    ap.add_argument("--geometry", choices=("crop", "camera"), default="crop",
                    help="crop = the training-time framing (resize short side, "
                         "center crop). camera = what the ArduCam's 128x128 mode "
                         "really delivers: the 4:3 field squashed into a square.")
    ap.add_argument("--emit-thresholds", type=Path, default=None,
                    help="write per-query match thresholds for M9's host/demo.py")
    ap.add_argument("--emit-embeddings", type=Path, default=None,
                    help="write the int8 eval embeddings for M12's host/demo.py, "
                         "so it can calibrate a query this table has no row for")
    ap.add_argument("--fpr", type=float, default=0.10,
                    help="false-positive rate the emitted thresholds are set at")
    args = ap.parse_args()

    run = Path(distill.RUNS) / (args.run or args.split)
    ckpt = torch.load(run / "student.pt", map_location="cpu", weights_only=False)
    device = teacher_mod.pick_device()

    names = data.image_list(args.split)
    queries = json.loads((data.CACHE / f"queries_{args.split}.json").read_text())
    teacher_emb, encode_text, calib_targets, teacher_label = teacher_bundle(
        ckpt["teacher"], args.split, ckpt["split"], device)

    # Score only on images the student never saw. When the student trained on a
    # different split there is no overlap, so the whole eval split is fair game -
    # which is the setup that gives the query counts any statistical weight.
    trained_on = ckpt["split"]
    if trained_on == args.split:
        eval_idx = np.array(sorted(ckpt["holdout"]))
        basis = f"held out from {trained_on}"
    else:
        eval_idx = np.arange(len(names))
        basis = f"all of {args.split}; student trained on {trained_on}"

    model = student_mod.Student()
    model.load_state_dict(ckpt["state_dict"])
    folded = quant_mod.fold_bn(model).to(device)
    qmodel = quant_mod.quantize(folded).to(device)

    # Calibrate int8 on training images, never on the eval set.
    hold = set(ckpt["holdout"]) if trained_on == args.split else set()
    calib_idx = np.array([i for i in range(len(data.image_list(trained_on))) if i not in hold])
    calib_loader = DataLoader(
        distill.DistillSet(trained_on, data.image_list(trained_on), calib_idx,
                           calib_targets,
                           distill.student_transform(False, False)),
        batch_size=args.batch, shuffle=False, num_workers=4)
    n_calib = quant_mod.calibrate(qmodel, calib_loader, device, args.calib_batches)

    print(f"student  : {run.name}, epoch {ckpt['epoch']}, holdout cos {ckpt['holdout_cosine']:+.4f}")
    print(f"teacher  : {teacher_label}")
    print(f"eval     : {len(eval_idx)} images ({basis})")
    print(f"int8     : calibrated on {n_calib} training images")
    print(f"criterion: prominent object, >= {queries['area_threshold']:.0%} of frame")
    # Calibration deliberately stays on the training framing whatever --geometry
    # says: the int8 scales in the exported blob were chosen that way, so varying
    # them too would confound the geometry question with a requantization.
    print(f"geometry : {args.geometry}"
          + ("  (4:3 field squashed to square, as the camera delivers it; "
             "int8 still calibrated on the training framing)"
             if args.geometry == "camera" else "  (training framing)"))
    print()

    eval_tf = (distill.camera_transform() if args.geometry == "camera"
               else distill.student_transform(False, False))
    emb = embed_students({"fp32": folded, "int8": qmodel},
                         args.split, names, eval_idx, teacher_emb, device,
                         args.batch, eval_tf)

    query_names = [q["name"] for q in queries["queries"]]
    text = encode_text(query_names)

    # Membership is defined over all images; restrict to the eval subset.
    pos_of = {q["name"]: set(q["pos"]) for q in queries["queries"]}
    excl_of = {q["name"]: set(q["excluded"]) for q in queries["queries"]}

    rows, dropped = [], []
    for qi, name in enumerate(query_names):
        keep = np.array([i for i in eval_idx if i not in excl_of[name]])
        if len(keep) == 0:
            dropped.append(name)
            continue
        positive = np.array([i in pos_of[name] for i in keep])
        n_pos, n_neg = int(positive.sum()), int((~positive).sum())
        if n_pos < MIN_POS or n_neg < MIN_POS:
            dropped.append(name)
            continue

        # Map absolute image indices onto rows of the student embedding arrays.
        where = {int(v): k for k, v in enumerate(eval_idx)}
        sel = np.array([where[int(i)] for i in keep])
        t = text[qi]
        int8_scores = emb["int8"][sel] @ t
        neg = int8_scores[~positive]
        # The background this query sits on. Absolute CLIP cosines carry a large
        # per-query offset that has nothing to do with the picture - "person"
        # runs ~0.01 hot against everything - and on the device that offset is
        # bigger than the signal, so the raw ranking across queries is not a
        # ranking. Standardising against each query's own negatives removes it.
        # std, not the score range: the range is set by the two extreme images
        # in the split and moves when the split does.
        mu, sd = float(neg.mean()), float(neg.std())
        thr = float(np.quantile(neg, 1.0 - args.fpr))
        rows.append({
            "name": name,
            "n_pos": n_pos,
            "n_neg": n_neg,
            "teacher": auc(teacher_emb[keep] @ t, positive),
            "fp32": auc(emb["fp32"][sel] @ t, positive),
            "int8": auc(int8_scores, positive),
            # M9's deployment numbers, not part of the gate. See --emit-thresholds.
            "threshold": thr,
            "mean_neg": mu,
            "std_neg": sd,
            # The same threshold in units of the background. This is what the
            # device compares against, and it is roughly 1.28 for every query at
            # --fpr 0.10, which is the point: one number, one meaning.
            "z_threshold": (thr - mu) / sd if sd > 0 else 0.0,
        })

    if not rows:
        print("RESULT : FAIL - no query had enough positives to score")
        return 1

    cos_fp32 = float((emb["fp32"] * teacher_emb[eval_idx]).sum(axis=1).mean())
    cos_int8 = float((emb["int8"] * teacher_emb[eval_idx]).sum(axis=1).mean())

    def summary(key: str) -> tuple[int, float]:
        vals = np.array([r[key] for r in rows])
        return int((vals >= AUC_CLEARS).sum()), float(vals.mean())

    n = len(rows)
    print(f"{'':<26}{'queries@AUC>=' + f'{AUC_CLEARS:.2f}':>18}{'mean AUC':>11}{'cos-to-teacher':>17}")
    for label, key, cos in ((f"teacher  {teacher_label.split(':')[0][:17]}", "teacher", 1.0),
                            ("student  fp32", "fp32", cos_fp32),
                            ("student  int8 (simulated)", "int8", cos_int8)):
        clears, mean = summary(key)
        print(f"{label:<26}{f'{clears} / {n}':>18}{mean:>11.3f}{cos:>17.3f}")

    print()
    print(f"{'query':<18}{'n_pos':>7}{'teacher':>10}{'fp32':>9}{'int8':>9}")
    ranked = sorted(rows, key=lambda r: -r["teacher"])
    shown = ranked if args.top <= 0 else ranked[: args.top] + ranked[-args.top :]
    for i, r in enumerate(shown):
        if args.top > 0 and i == args.top:
            print(f"{'...':<18}")
        print(f"{r['name']:<18}{r['n_pos']:>7}{r['teacher']:>10.3f}{r['fp32']:>9.3f}{r['int8']:>9.3f}")

    if dropped:
        print(f"\ndropped  : {len(dropped)} queries with < {MIN_POS} prominent positives "
              f"or negatives in this eval set")
        print(f"           {', '.join(dropped)}")

    if args.emit_embeddings:
        # M12. The table above can only calibrate a query it has a row for, and
        # a *contrast* query - normalize(e_pos - mean(e_neg)) - is a synthetic
        # vector that will never be in it. demo.py used to fall back to the
        # median of the other queries' numbers, which for a difference axis is
        # not an approximation of anything: a difference vector's cosines are
        # centred near zero with a much smaller spread than a raw prompt's, so
        # the median std made z far too small to ever cross a threshold.
        #
        # With the matrix in hand, demo.py computes mu and sd for any vector as
        # a 5000x512 matmul. It has to be THIS matrix - int8, this geometry -
        # and not the teacher cache next to it, because the board's cosines come
        # from the int8 student looking through the camera's squash, and the
        # per-query offset being cancelled is a property of that pipeline.
        #
        # STATED BECAUSE IT IS A REAL DIFFERENCE: the table's mean_neg/std_neg
        # are over each query's *negatives*, and demo.py's fallback will be over
        # every eval image. For a COCO class those nearly coincide (positives
        # are a few percent) and for a contrast axis there is no membership to
        # define negatives with, so all-images is the only population available.
        # Close, not identical - the table wins whenever it has a row.
        #
        # fp16 to match the teacher caches beside it. The precision lost is far
        # below the spread being measured, and model/cache/ is gitignored, so
        # this is a regenerable artifact like everything else in there.
        args.emit_embeddings.parent.mkdir(parents=True, exist_ok=True)
        np.save(args.emit_embeddings, emb["int8"].astype(np.float16))
        args.emit_embeddings.with_suffix(".json").write_text(json.dumps({
            "split": args.split,
            "run": run.name,
            # Beside `run` because a bank is only meaningful in one embedding
            # space, and both shipped spaces are 512-d - host/demo.py checks
            # `run` against its export.json rather than trusting the filename.
            "teacher": ckpt["teacher"],
            "geometry": args.geometry,
            "scored_on": "student int8 (simulated), " + basis,
            "rows": int(emb["int8"].shape[0]),
            "dim": int(emb["int8"].shape[1]),
        }, indent=2) + "\n")
        print(f"\nembeddings: {emb['int8'].shape[0]}x{emb['int8'].shape[1]} int8 "
              f"student embeddings written to {args.emit_embeddings}")

    if args.emit_thresholds:
        # A dropped query gets no threshold rather than a guessed one - there was
        # not enough data to score it, and a number here would look like there was.
        args.emit_thresholds.write_text(json.dumps({
            "split": args.split,
            "run": run.name,
            "teacher": ckpt["teacher"],
            "geometry": args.geometry,
            "fpr": args.fpr,
            "scored_on": "student int8 (simulated), " + basis,
            "templates": teacher_mod.TEMPLATES,
            "queries": {r["name"]: {"threshold": round(r["threshold"], 6),
                                    "mean_neg": round(r["mean_neg"], 6),
                                    "std_neg": round(r["std_neg"], 6),
                                    "z_threshold": round(r["z_threshold"], 4),
                                    "auc": round(r["int8"], 4),
                                    "n_pos": r["n_pos"], "n_neg": r["n_neg"]}
                        for r in rows},
        }, indent=2) + "\n")
        thr = np.array([r["threshold"] for r in rows])
        zt = np.array([r["z_threshold"] for r in rows])
        mu = np.array([r["mean_neg"] for r in rows])
        print(f"\nthresholds: {len(rows)} written to {args.emit_thresholds} at "
              f"{args.fpr:.0%} FPR, {thr.min():.3f}-{thr.max():.3f}, "
              f"median {np.median(thr):.3f}")
        # The spread that motivated standardising. If the raw range is wide and
        # the z range is narrow, the per-query offset was the thing in the way.
        print(f"            per-query background mean spans "
              f"{mu.min():.3f}-{mu.max():.3f} ({mu.max() - mu.min():.3f} wide); "
              f"in units of it the same thresholds span only "
              f"{zt.min():.2f}-{zt.max():.2f}")
        print(f"            {len(dropped)} queries have none - too little data to "
              f"place one, so demo.py falls back to the median and says so")

    teacher_clears = [r for r in rows if r["teacher"] >= AUC_CLEARS]
    _, teacher_mean = summary("teacher")
    retained = [r for r in teacher_clears if r["int8"] >= AUC_CLEARS]
    retention = len(retained) / len(teacher_clears) if teacher_clears else 0.0

    print()
    print(f"teacher mean AUC : {teacher_mean:.3f}  (floor {CONCEPT_FLOOR:.2f})")
    print(f"retention        : {len(retained)}/{len(teacher_clears)} = {retention:.0%} "
          f"of the queries the teacher clears")

    if teacher_mean < CONCEPT_FLOOR:
        verdict = ("NO-GO (concept) - CLIP itself cannot do these queries; "
                   "the student is not the problem")
        code = 1
    elif retention >= GO_RETENTION:
        verdict = "GO - the student retains enough of the teacher"
        code = 0
    elif retention >= MARGINAL_RETENTION:
        verdict = ("MARGINAL - works for the easier queries; read the per-query "
                   "table before committing")
        code = 0
    else:
        verdict = ("NO-GO - 1.5M params is not enough; fall back to Tier 2 or a "
                   "fixed classifier")
        code = 1

    print(f"\nRESULT : {verdict}")
    return code


if __name__ == "__main__":
    sys.exit(main())
