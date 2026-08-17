# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "open_clip_torch"]
# ///
"""Would letting the user supply a NEGATIVE prompt fix state detection?

    uv run --script tools/probe_negatives.py

Scoring `img . normalize(e_pos - e_neg)` instead of `img . e_pos` is the same
thing as taking the logit difference in a two-class softmax, up to a positive
scale -- so it does not matter which way it is described, and it costs the board
nothing: a query is a 512-d vector, whoever computed it.

The question is which negative. Five, on the same COCO harness:

  raw        e_pos                      what the board does today
  vs-state   - "a closed book"          the contrasting state
  vs-object  - "a book"                 the SAME object, state unsaid. Subtracts
                                        the noun and leaves the adjective, which
                                        is the part the alert cares about
  vs-empty   - "an empty scene..."      "nothing", i.e. an explicit background
  vs-all     - mean of the three

Labels come from captions, so these are floors, not point estimates.

WHAT IT FOUND, 2026-08-07, and it is not the obvious answer
------------------------------------------------------------
Means over the five tests: raw 0.675, vs-state 0.642, vs-object 0.648,
vs-empty 0.700, vs-all 0.725.

So the intuitive choice -- name the opposite state -- is the WORST of the five,
worse than supplying no negative at all, and on the glass row it inverts the
ranking outright (0.617 -> 0.371). Averaging several negatives is the only
strategy that helps everywhere. This is why host/demo.py takes a *list* after
the slash and documents the three-term form, rather than asking for "the
opposite".

tools/probe_student_neg.py repeats this on the student, which is the model that
actually runs, and is the number that decided M12.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))

import data
import numpy as np
import teacher as teacher_mod

SPLIT = "train2017"
EMPTY = "an empty scene with nothing in it"

# label, positive, contrasting state, the bare object, positive regex, object regex
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


def auc(scores: np.ndarray, pos: np.ndarray) -> float:
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    npos, nneg = int(pos.sum()), int((~pos).sum())
    return float((ranks[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def main():
    device = teacher_mod.pick_device()
    model, _ = teacher_mod.load_clip(device)

    names = data.image_list(SPLIT)
    idx_of = {n: i for i, n in enumerate(names)}
    emb = teacher_mod.load_cache(SPLIT).astype(np.float32)
    caps = json.loads((data.DATA / "annotations" /
                       f"captions_{SPLIT}.json").read_text())
    fname = {im["id"]: im["file_name"] for im in caps["images"]}
    text: dict[int, str] = {}
    for a in caps["annotations"]:
        text[a["image_id"]] = text.get(a["image_id"], "") + " " + a["caption"].lower()
    rows = [(idx_of[fname[i]], t) for i, t in text.items() if fname.get(i) in idx_of]

    cols = ["raw", "vs-state", "vs-object", "vs-empty", "vs-all"]
    hdr = f"{'state':22} {'n_pos':>6} " + "".join(f"{c:>10}" for c in cols) + "   best"
    print(f"split    : {SPLIT}, {len(rows)} captioned images")
    print(f"negative for 'nothing': {EMPTY!r}\n")
    print(hdr)
    print("-" * len(hdr))

    for label, pp, ns, ob, pos_re, obj_re in TESTS:
        pr, orx = re.compile(pos_re), re.compile(obj_re)
        keep, flags = [], []
        for i, t in rows:
            if not orx.search(t):
                continue
            keep.append(i)
            flags.append(bool(pr.search(t)))
        keep, flags = np.array(keep), np.array(flags)
        if flags.sum() < 20 or (~flags).sum() < 20:
            continue
        e = emb[keep]
        v = teacher_mod.encode_queries(model, [pp, ns, ob, EMPTY], device)
        pos, negs = v[0], {"vs-state": v[1], "vs-object": v[2], "vs-empty": v[3]}
        negs["vs-all"] = v[1:4].mean(axis=0)

        got = {"raw": auc(e @ pos, flags)}
        for k in cols[1:]:
            d = pos - negs[k]
            got[k] = auc(e @ (d / np.linalg.norm(d)), flags)
        best = max(got, key=got.get)
        print(f"{label:22} {int(flags.sum()):>6} "
              + "".join(f"{got[c]:>10.3f}" for c in cols)
              + f"   {best} {got[best]:.3f} ({got[best]-got['raw']:+.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
