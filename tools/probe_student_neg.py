# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch"]
# ///
"""Does the negative-prompt gain survive distillation?

    uv run --script tools/probe_student_neg.py

tools/probe_negatives.py showed every state test improving on the TEACHER when a
negative is supplied. The teacher is not what runs on the board. This repeats
the same table with the student's own embeddings, which is the number that
decides whether the UX change is worth building.

Only the images each test actually needs are embedded, and negatives are capped
so "person posture" (54k rows) does not dominate the runtime. AUC is stable at
a few thousand negatives; the cap is seeded so the number is reproducible.

CAVEAT, stated because it cuts the wrong way: the student TRAINED on train2017,
so these are training images and the number is optimistic. It is still the right
split -- val2017 has three "open book" captions -- and a distinction that fails
on training data will not appear on the bench.

WHAT IT FOUND, 2026-08-07 -- the measurement M12 is built on
-------------------------------------------------------------
Means: raw 0.610, vs-state 0.609, vs-object 0.590, vs-empty 0.601,
vs-all 0.646. The gain survives, smaller than the teacher's (+0.036 against
+0.050), and the shape is the same: vs-all wins, vs-state does not.

Per-row it is not uniform, which is why host/demo.py must NOT silently append
"nothing" to every query. vs-empty helps book (+0.111, 0.614 -> 0.726) and glass
(+0.061) and hurts pouring (-0.117) and posture (-0.124).

And the honest ceiling: the best student column lands at 0.58-0.75. That is a
usable ranking and it is not a reliable alert. Getting past it needs a
change/event formulation, not a better prompt.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))

import data
import distill
import numpy as np
import student as student_mod
import teacher as teacher_mod
import torch
from torch.utils.data import DataLoader

SPLIT = "train2017"
EMPTY = "an empty scene with nothing in it"
NEG_CAP = 3000
TESTS = [
    ("book open/closed", "an opened book", "a closed book", "a book",
     r"\bopen(ed)? book\b|\bbook (is |lying )?open\b", r"\bbooks?\b"),
    ("pouring", "pouring water into a glass", "a glass on a table", "a glass",
     r"\bpour(s|ing|ed)?\b", r"\b(glass|cup|bottle|pitcher|jug)\b"),
    ("person posture", "a person sitting down", "a person standing up", "a person",
     r"\b(sitting|seated|sits)\b", r"\b(person|man|woman|people|boy|girl)\b"),
    ("glass full/empty", "a glass of water", "an empty glass", "a glass",
     r"\bglass of (water|wine|juice|milk|beer)\b|\bfull glass\b",
     r"\b(glass|glasses)\b"),
    ("door open/closed", "an open door", "a closed door", "a door",
     r"\bopen door\b|\bdoor is open\b|\bdoors? (are |is )?open\b", r"\bdoors?\b"),
]
COLS = ["raw", "vs-state", "vs-object", "vs-empty", "vs-all"]


def auc(scores, pos):
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    npos, nneg = int(pos.sum()), int((~pos).sum())
    return float((ranks[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def main():
    device = teacher_mod.pick_device()
    names = data.image_list(SPLIT)
    idx_of = {n: i for i, n in enumerate(names)}
    caps = json.loads((data.DATA / "annotations" /
                       f"captions_{SPLIT}.json").read_text())
    fname = {im["id"]: im["file_name"] for im in caps["images"]}
    text = {}
    for a in caps["annotations"]:
        text[a["image_id"]] = text.get(a["image_id"], "") + " " + a["caption"].lower()
    rows = [(idx_of[fname[i]], t) for i, t in text.items() if fname.get(i) in idx_of]

    rng = np.random.default_rng(0)
    sets = {}
    for label, _, _, _, pos_re, obj_re in TESTS:
        pr, orx = re.compile(pos_re), re.compile(obj_re)
        pos = [i for i, t in rows if orx.search(t) and pr.search(t)]
        neg = [i for i, t in rows if orx.search(t) and not pr.search(t)]
        if len(neg) > NEG_CAP:
            neg = list(rng.choice(neg, NEG_CAP, replace=False))
        sets[label] = (np.array(pos), np.array(neg))

    need = sorted({int(i) for p, n in sets.values() for i in (*p, *n)})
    print(f"embedding {len(need)} images with the student ...")

    ckpt = torch.load(ROOT / "model/runs/train2017/student.pt", map_location="cpu",
                      weights_only=False)
    net = student_mod.Student()
    net.load_state_dict(ckpt["state_dict"])
    net = net.to(device).eval()
    # The training/eval framing, not camera_transform(): these are COCO JPEGs,
    # and the question is what the student's embedding contains, not what the
    # Mega's squash does to it.
    tf = distill.student_transform(False, False)
    idx = np.array(need)
    ds = distill.DistillSet(SPLIT, names, idx, np.zeros((len(names), 1), np.float32), tf)
    out = []
    with torch.no_grad():
        for px, _ in DataLoader(ds, batch_size=256, num_workers=8, shuffle=False):
            e = net(px.to(device))
            out.append((e / e.norm(dim=-1, keepdim=True)).cpu().numpy())
    se = np.concatenate(out).astype(np.float32)
    at = {v: k for k, v in enumerate(need)}
    print(f"student  : epoch {ckpt['epoch']}, holdout cos {ckpt['holdout_cosine']:+.4f}\n")

    model, _ = teacher_mod.load_clip(device)
    hdr = f"{'state':22} {'n_pos':>6} " + "".join(f"{c:>10}" for c in COLS) + "   best"
    print(hdr)
    print("-" * len(hdr))
    for label, pp, ns, ob, _, _ in TESTS:
        p, n = sets[label]
        e = se[[at[int(i)] for i in (*p, *n)]]
        flags = np.array([True] * len(p) + [False] * len(n))
        v = teacher_mod.encode_queries(model, [pp, ns, ob, EMPTY], device)
        negs = {"vs-state": v[1], "vs-object": v[2], "vs-empty": v[3],
                "vs-all": v[1:4].mean(axis=0)}
        got = {"raw": auc(e @ v[0], flags)}
        for k in COLS[1:]:
            d = v[0] - negs[k]
            got[k] = auc(e @ (d / np.linalg.norm(d)), flags)
        best = max(got, key=got.get)
        print(f"{label:22} {len(p):>6} " + "".join(f"{got[c]:>10.3f}" for c in COLS)
              + f"   {best} {got[best]:.3f} ({got[best]-got['raw']:+.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
