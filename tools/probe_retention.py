# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""What does a student's object accuracy cost, when its teacher changed?

    uv run --script tools/probe_retention.py _sieve_infonce-0.3 so400m-s30k

tools/probe_inherit.py answers whether a student inherits its teacher's
adjective binding. This answers the other half: whether it gave up ordinary
object recognition to get there. Both are needed before a teacher swap is worth
a full run - a student that finally sees an open book and has stopped seeing
cups is not progress.

WHY NOT model/evaluate.py
-------------------------
evaluate.py is the project's GO/NO-GO gate and it is wired to one teacher in
three places: the reference bank, the int8 calibration targets, and the text
encoder. A student trained on SigLIP 2 SO400M + PCA-512 emits vectors in a
different space, so evaluate.py would score it against unrelated text and report
a confident, meaningless number. Rather than rework the gate to answer a sieve
question, this scores fp32 students only, on the same val2017 images, the same
queries and the same AUC function evaluate.py uses - each against text from its
*own* teacher, which is the only comparison that means anything.

WHAT IT DOES NOT REPORT
-----------------------
"Retention", evaluate.py's headline, is student AUC against the *teacher's* AUC
on the same query. That denominator needs the teacher's own val2017 embeddings,
which for SO400M is another 7 minutes of encoding per split and is not what this
question turns on. Absolute student AUC, compared student to student, is. Note
this means a teacher with a higher ceiling gets no credit for it here.

WHAT IT FOUND, 2026-08-08
-------------------------
Same 30000 images, 20 epochs, batch 512, --infonce 0.3; only the teacher differs.

  student              teacher                  AUC>=0.80   mean AUC
  _sieve_infonce-0.3   ViT-B/16                  51 / 67      0.855
  so400m-s30k          SO400M + joint PCA-512    37 / 67      0.800

-0.055 mean, better on only 14 of 67. Worst: handbag -0.311, umbrella -0.166,
surfboard -0.149, bench -0.145, book -0.140. So the swap that finally binds
"opened" to "book" (tools/probe_inherit.py) makes the plain "book" query worse.

This is not obviously undertraining. The same student fits the SO400M space
*better* by every instance measure - holdout top-1 0.375 vs 0.268, centred
cosine +0.4234 vs +0.4085 - while losing category AUC.

WHERE THE 0.055 ACTUALLY GOES, 2026-08-08
------------------------------------------
tools/probe_alpha.py scored the *teachers* on these same images and queries and
moved the blame off the space entirely:

  teacher 0.952 -> student 0.855   ViT-B/16,               0.097 lost
  teacher 0.970 -> student 0.800   SO400M pca512 alpha 1,  0.170 lost

The SO400M space is the better one at every centring strength; the student just
retains less of it. Centring drops the target cone 0.7381 -> 0.5864, so the
targets are spread over more of the sphere and less of each answer can be
guessed from the mean. That is a capacity-and-data shortfall, not a property of
the map, and 30000 images is where to expect it.

--rkd DOES NOT HELP, AND COSTS GATE 2
--------------------------------------
Preserving the teacher's within-batch similarity structure was the obvious
remedy - the whole complaint is that category clumping was lost. It fails:

  run             extra          holdout top-1   mean AUC   gate 2
  so400m-s30k     -                    0.375       0.800     PASS
  so400m-rkd10    --rkd 10.0           0.286       0.802     FAIL
  so400m-rkd100   --rkd 100.0          0.088         -         -

+0.003 of AUC, which is noise, for a quarter of the top-1 and the one property
the teacher swap was for: rkd10 ranks "an opened book" onto the CLOSED frame
again (-2.15 sd on opened-closed, tools/probe_inherit.py). rkd100 is a rout. The
similarity matrix is a weaker target than the vectors and it competes with them.

AND THE ANSWER, 2026-08-09: BOTH HALVES OF "CAPACITY AND DATA"
----------------------------------------------------------------
The section above guessed the 0.055 was "a capacity-and-data shortfall, not a
property of the map". A 2x2 over images and centring strength (the table in
tools/probe_alpha.py) says it was data *and* how hard the map centres, in
roughly equal parts and additively:

                        alpha 1.0      alpha 0.5
   30000 images           0.800          0.835
  118287 images           0.856          0.895

The -0.055 this script measured is the top-left cell against ViT-B/16's 0.855.
The bottom-right cell is 0.895 - within 0.004 of the shipped ViT-B/16 student's
0.899, on model/evaluate.py's own scale, at 91% retention against 94%. So the
swap costs no object accuracy once it is trained on the whole split with the
gentler centring; the 0.055 was the price of a 30000-image sieve run, not of
SO400M.

Two notes for anyone re-reading the sieve numbers above:
  - This script deliberately reports no retention denominator (see WHAT IT DOES
    NOT REPORT). The 55%/73%/80%/91% quoted in the probe_alpha.py table are
    model/evaluate.py's, which does encode the teacher on val2017. They rank the
    students the same way the absolute AUCs do.
  - The centring dial moves the bench axes too, but not additively and not in
    the same direction at both data sizes - so a sieve-scale alpha result would
    have mispredicted the full run on gate 3 while predicting the AUC fine.
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import open_clip

import data
import distill
import student as student_mod
import teacher as teacher_mod
from evaluate import auc
from probe_inherit import resolve

SPLIT = "val2017"


def text_vectors(spec, basis_path, names, device):
    """One vector per query, TEMPLATES-ensembled then projected.

    Ensembling happens in the teacher's own space and the projection is applied
    to the result, which is the order host/demo.py would use: it encodes and
    averages first, and the basis is a post-hoc map onto the board's 512.
    """
    name, pre = spec.split(":")
    model, _, _ = open_clip.create_model_and_transforms(name, pretrained=pre)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(name)
    out = []
    with torch.no_grad():
        for q in names:
            v = model.encode_text(
                tok([t.format(q) for t in teacher_mod.TEMPLATES]).to(device)).float()
            v = (v / v.norm(dim=-1, keepdim=True)).mean(dim=0)
            out.append((v / v.norm()).cpu().numpy())
    del model
    v = np.stack(out)
    if basis_path is None:
        return v
    b = np.load(basis_path)
    p = (v - b["mu"]) @ b["w"]
    return p / np.linalg.norm(p, axis=-1, keepdims=True)


def student_vectors(ckpt, names, device):
    net = student_mod.Student()
    net.load_state_dict(ckpt["state_dict"])
    net = net.to(device).eval()
    idx = np.arange(len(names))
    # camera_transform(), matching evaluate.py --geometry camera's default
    # framing question, is deliberately NOT used: this compares students to each
    # other, and the training framing is the one both were trained on.
    loader = DataLoader(
        distill.DistillSet(SPLIT, names, idx, np.zeros((len(names), 1), np.float16),
                           distill.student_transform(False, False)),
        batch_size=256, shuffle=False, num_workers=6)
    out = np.zeros((len(names), student_mod.EMBED_DIM), np.float32)
    at = 0
    with torch.no_grad():
        for pixels, _ in loader:
            e = net(pixels.to(device))
            e = e / e.norm(dim=-1, keepdim=True)
            out[at:at + len(e)] = e.cpu().numpy()
            at += len(e)
    return out


def main():
    runs = sys.argv[1:]
    if not runs:
        print("usage: probe_retention.py RUN [RUN ...]")
        return 1
    device = teacher_mod.pick_device()
    names = data.image_list(SPLIT)
    queries = json.loads((data.CACHE / f"queries_{SPLIT}.json").read_text())
    qnames = [q["name"] for q in queries["queries"]]
    pos_of = {q["name"]: set(q["pos"]) for q in queries["queries"]}
    excl_of = {q["name"]: set(q["excluded"]) for q in queries["queries"]}

    print(f"images   : {len(names)} ({SPLIT}, all of it - these students trained "
          f"on train2017)")
    print(f"queries  : {len(qnames)}   criterion: prominent object, "
          f">= {queries['area_threshold']:.0%} of frame\n")
    print(f"{'run':24}{'teacher':34}{'AUC>=0.80':>11}{'mean AUC':>10}")

    table = {}
    for r in runs:
        ckpt = torch.load(ROOT / "model/runs" / r / "student.pt",
                          map_location="cpu", weights_only=False)
        spec, basis = resolve(ckpt.get("teacher", ""), None, None)
        text = text_vectors(spec, basis, qnames, device)
        sv = student_vectors(ckpt, names, device)
        scores = sv @ text.T

        aucs = {}
        for j, q in enumerate(qnames):
            pos = pos_of[q] & set(range(len(names)))
            keep = np.array([i for i in range(len(names))
                             if i in pos or i not in excl_of[q]])
            lab = np.array([i in pos for i in keep])
            # evaluate.py's own floor: fewer than 10 either way and the AUC is
            # an artifact of which handful of images happened to be in the split.
            if lab.sum() < 10 or (~lab).sum() < 10:
                continue
            aucs[q] = auc(scores[keep, j], lab)
        table[r] = aucs
        tag = (basis.name.replace("emb_train2017_", "").replace(".basis.npz", "")
               if basis else spec)
        good = sum(v >= 0.80 for v in aucs.values())
        print(f"{r:24}{tag:34}{good:>6} /{len(aucs):3}"
              f"{np.mean(list(aucs.values())):>10.3f}")

    if len(runs) == 2:
        a, b = table[runs[0]], table[runs[1]]
        both = sorted(set(a) & set(b), key=lambda q: b[q] - a[q])
        d = np.array([b[q] - a[q] for q in both])
        print(f"\n{runs[1]} minus {runs[0]}, over {len(both)} shared queries: "
              f"mean {d.mean():+.3f}, better on {int((d > 0).sum())}")
        print("  worst 5 :  " + "  ".join(f"{q} {b[q]-a[q]:+.3f}" for q in both[:5]))
        print("  best 5  :  " + "  ".join(f"{q} {b[q]-a[q]:+.3f}" for q in both[-5:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
