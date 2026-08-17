# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "open_clip_torch"]
# ///
"""Teacher AUC on STATE, framed the way an alert actually asks it.

    uv run --script tools/probe_states2.py

The negative set is images that contain the object and are NOT in the state --
"there is a book, is it open?" -- not "is there a book at all". That is the
discrimination the appliance has to make, and it is much harder than the object
retrieval the M9 numbers came from.

train2017 (118k images), because "open book" appears three times in val2017 and
tools/probe_states.py runs out of labels there. Cached teacher embeddings, so
this costs a text encode and a matmul. Testing the TEACHER, so the student's
training split is not a leak here.

WHAT IT FOUND, 2026-08-07
-------------------------
Best of raw/diff: book 0.772, pouring 0.763, posture 0.821, glass 0.617,
door 0.562 -- against object controls at 0.879-0.986. So the teacher can do
state, weakly, and cannot do all state equally.

Three caveats, all of which cut against reading these as point estimates:
  * Caption labels are sparse and noisy, so these are FLOORS. An image whose
    caption does not mention the book being open still counts as a negative.
  * The difference axis is not a universal win -- it helps some rows and hurts
    others, which is the finding tools/probe_negatives.py went on to pin down.
  * Text angle does NOT predict AUC. Book sits at 13.5 degrees and scores 0.772;
    glass sits at 21.6 and scores 0.617. Geometry bounds the problem, it does
    not rank the prompts, so probe_states.py part A cannot replace this run.
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

# label, positive prompt, negative prompt, positive caption regex, object regex
TESTS = [
    ("book open/closed", "an opened book", "a closed book",
     r"\bopen(ed)? book\b|\bbook (is |lying )?open\b", r"\bbooks?\b"),
    ("glass full/empty", "a glass of water", "an empty glass",
     r"\bglass of (water|wine|juice|milk|beer)\b|\bfull glass\b",
     r"\b(glass|glasses)\b"),
    ("door open/closed", "an open door", "a closed door",
     r"\bopen door\b|\bdoor is open\b|\bdoors? (are |is )?open\b", r"\bdoors?\b"),
    ("pouring", "pouring water into a glass", "a glass on a table",
     r"\bpour(s|ing|ed)?\b", r"\b(glass|cup|bottle|pitcher|jug)\b"),
    ("person posture", "a person sitting down", "a person standing up",
     r"\b(sitting|seated|sits)\b", r"\b(person|man|woman|people|boy|girl)\b"),
    ("upside down", "an upside-down object", "an upright object",
     r"\bupside[- ]down\b", r"\b(box|boxes|bowl|cup|chair|bottle)\b"),
]
CONTROLS = [("a book", r"\bbooks?\b"), ("a laptop", r"\blaptops?\b"),
            ("a cat", r"\bcats?\b")]


def auc(scores: np.ndarray, pos: np.ndarray) -> float:
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    npos, nneg = int(pos.sum()), int((~pos).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
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
    rows = [(idx_of[fname[i]], t) for i, t in text.items()
            if fname.get(i) in idx_of]
    print(f"split    : {SPLIT}, {len(rows)} captioned images\n")

    hdr = (f"{'state':22} {'n_pos':>6} {'n_neg':>6} {'AUC raw':>9} "
           f"{'AUC diff':>9}   verdict")
    print(hdr)
    print("-" * len(hdr))
    for label, pp, np_, pos_re, obj_re in TESTS:
        pr, orx = re.compile(pos_re), re.compile(obj_re)
        keep, flags = [], []
        for i, t in rows:
            if not orx.search(t):        # object absent -> not the question
                continue
            keep.append(i)
            flags.append(bool(pr.search(t)))
        keep, flags = np.array(keep), np.array(flags)
        if flags.sum() < 20 or (~flags).sum() < 20:
            print(f"{label:22} {int(flags.sum()):>6} {int((~flags).sum()):>6} "
                  f"{'--':>9} {'--':>9}   too few to say anything")
            continue
        e = emb[keep]
        qv = teacher_mod.encode_queries(model, [pp, np_], device)
        d = qv[0] - qv[1]
        d = d / np.linalg.norm(d)
        a_raw, a_dif = auc(e @ qv[0], flags), auc(e @ d, flags)
        best = max(a_raw, a_dif)
        verdict = ("useless" if best < 0.65 else "weak" if best < 0.80 else
                   "usable" if best < 0.90 else "good")
        print(f"{label:22} {int(flags.sum()):>6} {int((~flags).sum()):>6} "
              f"{a_raw:>9.3f} {a_dif:>9.3f}   {verdict}")

    print()
    for obj, rx in CONTROLS:
        r = re.compile(rx)
        keep = np.array([i for i, _ in rows])
        flags = np.array([bool(r.search(t)) for _, t in rows])
        qv = teacher_mod.encode_queries(model, [obj], device)
        print(f"{('control ' + obj):22} {int(flags.sum()):>6} "
              f"{int((~flags).sum()):>6} {auc(emb[keep] @ qv[0], flags):>9.3f} "
              f"{'--':>9}   object identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
