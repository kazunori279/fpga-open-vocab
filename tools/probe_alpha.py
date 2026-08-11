# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Which half of the teacher swap costs the object AUC - SO400M, or the centring?

    uv run --script tools/teacher_swap.py --split val2017 --subset 0 --holdout 0
    uv run --script tools/probe_alpha.py

THE QUESTION
------------
tools/probe_retention.py found the SO400M student gives up 0.055 of mean object
AUC (0.855 -> 0.800) to gain gate 2. That number is a *student*'s, so it blames
nothing in particular: the loss could be in SO400M's space, in the PCA squeeze
to 512, in the centring on the joint mean, or in the distillation.

This measures the teacher alone on the same val2017 images and the same 67
queries, so three of those four suspects can be separated before another hour of
training. If the projected teacher already scores what the student scored, the
remedy is the projection. If the projected teacher scores what ViT-B/16 scores,
the remedy is the distillation and the projection is exonerated.

WHY alpha
---------
The projection is p = normalize((v - alpha*mu) @ w), and alpha=1 - subtracting
the joint image+text mean in full - is what widened the gate-2 margin. It is
also the prime suspect here, because the shared cone axis it deletes is not pure
nuisance: images of the same category sit together partly *because* they share
that direction. alpha turns "delete it" into a dial, and this sweep says what
each setting costs.

The 1152 row is the ceiling: no squeeze, no centring, just SO400M. It is not
reachable by the board, which has 512 lanes, but it separates "SO400M's space is
different" from "our map damages it".

WHAT IT DOES NOT MEASURE
------------------------
Gate 2. A projection that keeps every point of AUC and loses the adjective
binding is not a win, so any alpha this favours still has to go back through
tools/probe_project.py before it is worth a training run.

Note also that the PCA bank is fitted on val2017 - the split scored here - so the
absolute SO400M numbers flatter the map slightly. The comparison *between*
alphas, which is the question, is unaffected: they share one basis.

WHAT IT FOUND, 2026-08-08: THE PROJECTION IS NOT THE CULPRIT
-------------------------------------------------------------
  space                        AUC>=0.80  mean AUC   gate-2 margin
  ViT-B/16 512 (incumbent)       63 / 67     0.952    fails
  SO400M 1152, unprojected       67 / 67     0.980    -
  SO400M pca512, alpha 0.00      67 / 67     0.981    +0.0183
  SO400M pca512, alpha 0.25      67 / 67     0.983    +0.0226
  SO400M pca512, alpha 0.50      67 / 67     0.983    +0.0283
  SO400M pca512, alpha 0.75      67 / 67     0.978    +0.0355
  SO400M pca512, alpha 1.00      65 / 67     0.970    +0.0305

Every projected SO400M space beats the incumbent teacher on object AUC, and
every one of them passes gate 2. Full centring costs 0.013 against the best
alpha, which is real and is a twelfth of what the student lost. So the swap did
not trade object accuracy for adjective binding at all - the teacher gained
both.

The 0.055 the student gave up is therefore *distillation*, and the arithmetic
says how much:

  teacher 0.952 -> student 0.855   ViT-B/16,  0.097 lost
  teacher 0.970 -> student 0.800   SO400M,    0.170 lost

The SO400M space is not worse, it is harder to fit: centring drops the target
cone from 0.7381 to 0.5864, which is the same as saying the targets are spread
over more of the sphere and less of the answer can be guessed from the mean.
A 128px student on 30000 images has to represent more to score the same. That
points at data, not at the map - hence the full 118287-image run - and it means
the alpha dial should be set for whatever else it buys, not to recover AUC.

alpha 0.5 is the pick: joint best AUC and 93% of alpha 1's gate-2 margin.

The ViT-B/16 row is the shipped configuration, quirks included - model/
teacher.py loads "ViT-B-16" against the 'openai' tag without -quickgelu, which
open_clip warns about. Left alone deliberately: the cache and the text vectors
come from that same model, so the row describes the teacher the incumbent
student actually had.

WHAT alpha 0.5 WAS ACTUALLY WORTH, AT THE STUDENT, 2026-08-09
--------------------------------------------------------------
The paragraph above picks alpha 0.5 for 0.013 of *teacher* AUC and calls the
dial a thing to set "for whatever else it buys, not to recover AUC". That was
the wrong size. Four students, same recipe, same 40/20 epochs, only images and
alpha differing:

  run                images  alpha   fp32 AUC   retention   op-cl    spr-fr
  so400m-s30k          30k    1.0      0.800       55%      +3.35    +3.40
  so400m-s30k-a05      30k    0.5      0.835       73%      +2.61    +4.94
  so400m-full         118k    1.0      0.856       80%      +1.32    +1.93
  so400m-full-a05     118k    0.5      0.895       91%      +5.88    +7.33

On AUC the two levers are clean and additive: alpha buys +0.035 at 30k and
+0.039 at 118k, data buys +0.056 at alpha 1 and +0.060 at alpha 0.5. Neither
substitutes for the other, and 0.5 x 118k lands at 0.895 against the shipped
ViT-B/16 student's 0.899 - the object AUC the swap was thought to have cost is
recovered in full.

So the 0.013 at the teacher predicted 0.035-0.039 at the student, three times
over. It is not a projection artifact that centring 0.5 measures better; it is
that a weaker mu subtraction leaves the target cone tighter (0.5864 -> more of
the answer guessable from the mean), and a 128px student on a finite budget
spends its capacity on what is left. The dial is a *distillation* knob that this
script could only see the shadow of.

On the bench axes the same 2x2 is not additive at all - alpha 0.5 costs
opened-closed at 30k (+3.35 -> +2.61) and pays it back sevenfold at 118k
(+1.32 -> +5.88). One book and one lighting behind each of those numbers, so the
honest reading is that the axes need the data and the AUC does not care which
lever supplies it.
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import open_clip

import probe_project as pp
import probe_teacher as pt
import data
import teacher as teacher_mod
from evaluate import auc

SPLIT = "val2017"
SNAPS = Path("/tmp/snaps")
ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]


def score_table(img, text, names, pos_of, excl_of, n):
    """AUC per query, skipping the ones evaluate.py's floor would discard."""
    s = img @ text.T
    out = {}
    for j, q in enumerate(names):
        pos = pos_of[q] & set(range(n))
        keep = np.array([i for i in range(n) if i in pos or i not in excl_of[q]])
        lab = np.array([i in pos for i in keep])
        if lab.sum() < 10 or (~lab).sum() < 10:
            continue
        out[q] = auc(s[keep, j], lab)
    return out


def main():
    raw_path = data.CACHE / f"emb_{SPLIT}_SO400M.raw.npy"
    basis_path = data.CACHE / f"emb_{SPLIT}_SO400M.basis.npz"
    if not raw_path.exists():
        raise SystemExit(f"{raw_path} missing. Run:\n  uv run --script "
                         f"tools/teacher_swap.py --split {SPLIT} --subset 0 --holdout 0")

    device = teacher_mod.pick_device()
    names = data.image_list(SPLIT)
    queries = json.loads((data.CACHE / f"queries_{SPLIT}.json").read_text())
    qnames = [q["name"] for q in queries["queries"]]
    pos_of = {q["name"]: set(q["pos"]) for q in queries["queries"]}
    excl_of = {q["name"]: set(q["excluded"]) for q in queries["queries"]}
    print(f"images   : {len(names)} ({SPLIT})")
    print(f"queries  : {len(qnames)}   device {device}")

    # 1. The incumbent, from the cache the shipped student was trained on.
    clip, _ = teacher_mod.load_clip(device)
    vit_text = teacher_mod.encode_queries(clip, qnames, device).astype(np.float32)
    del clip
    vit_img = teacher_mod.load_cache(SPLIT).astype(np.float32)
    vit_img /= np.linalg.norm(vit_img, axis=-1, keepdims=True)
    ref = score_table(vit_img, vit_text, qnames, pos_of, excl_of, len(names))

    # 2. SO400M text, ensembled in its own space exactly as probe_retention.py
    #    does it, so the two scripts' numbers are comparable.
    name, pre = pp.SPEC.split(":")
    model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pre)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(name)
    tv = []
    with torch.no_grad():
        for q in qnames:
            v = model.encode_text(
                tok([t.format(q) for t in teacher_mod.TEMPLATES]).to(device)).float()
            v = (v / v.norm(dim=-1, keepdim=True)).mean(dim=0)
            tv.append((v / v.norm()).cpu().numpy())
    so_text = np.stack(tv).astype(np.float32)

    # The bench frames and the bare gate prompts, encoded once here so the gate
    # sweep below costs nothing: an alpha that buys AUC by giving gate 2 back is
    # not a candidate, and that is 13 encodes to find out.
    bench_img = bench_txt = None
    missing = [f for _, f in pt.IMAGES if not (SNAPS / f).exists()]
    if missing:
        print(f"\n(skipping the gate sweep: {SNAPS} is missing {missing[0]} etc.)")
    else:
        allp = pt.PROMPTS + [pt.CONTROL]
        with torch.no_grad():
            bench_txt = model.encode_text(tok(allp).to(device)).float().cpu().numpy()
            bench_img = model.encode_image(torch.stack([
                preprocess(Image.open(SNAPS / f).convert("RGB"))
                for _, f in pt.IMAGES]).to(device)).float().cpu().numpy()
        bench_txt /= np.linalg.norm(bench_txt, axis=-1, keepdims=True)
        bench_img /= np.linalg.norm(bench_img, axis=-1, keepdims=True)
    del model
    so_img = np.load(raw_path).astype(np.float32)
    so_img /= np.linalg.norm(so_img, axis=-1, keepdims=True)

    b = np.load(basis_path)
    mu, w = b["mu"], b["w"]

    print(f"\n{'space':34}{'AUC>=0.80':>11}{'mean AUC':>10}{'vs ViT-B/16':>13}")
    rows = [(f"ViT-B/16 512 (incumbent)", ref)]
    rows.append(("SO400M 1152, unprojected", score_table(
        so_img, so_text, qnames, pos_of, excl_of, len(names))))
    for a in ALPHAS:
        def proj(v):
            p = (v - a * mu) @ w
            return p / np.linalg.norm(p, axis=-1, keepdims=True)
        rows.append((f"SO400M pca512, alpha {a:.2f}", score_table(
            proj(so_img), proj(so_text), qnames, pos_of, excl_of, len(names))))

    for label, t in rows:
        shared = sorted(set(t) & set(ref))
        d = np.mean([t[q] - ref[q] for q in shared])
        good = sum(v >= 0.80 for v in t.values())
        print(f"{label:34}{good:>6} /{len(t):3}{np.mean(list(t.values())):>10.3f}"
              f"{d:>+13.3f}")

    # The student's 0.800 is the number all of this exists to explain, so print
    # the gap between the best teacher space and it rather than leaving the
    # subtraction to the reader.
    best = max(rows[2:], key=lambda r: np.mean(list(r[1].values())))
    print(f"\nbest projected teacher : {best[0]}  "
          f"{np.mean(list(best[1].values())):.3f}")
    print(f"so400m-s30k student    : 0.800 (tools/probe_retention.py)")

    if bench_img is None:
        return 0
    print(f"\n{'='*78}\nGATE 2 across the same alphas (bare prompts, "
          f"probe_teacher.py's rule)")
    allp = pt.PROMPTS + [pt.CONTROL]
    io, ic = pt.IMAGES[0][0], pt.IMAGES[1][0]
    print(f"{'alpha':>7}{'opened->OPEN':>16}{'closed->CLOSED':>17}{'margin':>10}")
    for a in ALPHAS:
        def proj(v):
            p = (v - a * mu) @ w
            return p / np.linalg.norm(p, axis=-1, keepdims=True)
        pi, ptx = proj(bench_img), proj(bench_txt)
        cos = pi @ ptx.T
        o, c = allp.index("an opened book"), allp.index("a closed book")
        # Gate 2 is two rankings, and the margin is the one that has to stay
        # positive for both: how much the right image wins its own prompt by.
        m = min(cos[0, o] - cos[1, o], cos[1, c] - cos[0, c])
        print(f"{a:>7.2f}{'ok' if cos[0,o] > cos[1,o] else 'FAIL':>16}"
              f"{'ok' if cos[1,c] > cos[0,c] else 'FAIL':>17}{m:>+10.4f}")
    print(f"({io.strip()} = {pt.IMAGES[0][1]}, {ic.strip()} = {pt.IMAGES[1][1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
