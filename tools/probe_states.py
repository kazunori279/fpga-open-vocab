# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "open_clip_torch"]
# ///
"""Is the teacher any good at STATE, as opposed to object identity?

    uv run --script tools/probe_states.py

The project's goal is alerts like "the book was opened", "water was poured into
the glass", "the box is upside down". Those are all state and relation queries,
and CLIP's known weakness is exactly there. Two measurements:

  A. Text geometry. How far apart are the two phrasings of a state, compared to
     two different objects? This needs no images and bounds everything: if the
     two prompts sit at cosine 0.95, the whole distinction lives in a sliver
     that image-side noise can swallow.

  B. Real AUC on COCO val2017, using the cached teacher image embeddings and
     captions as ground truth. Free -- the embeddings were computed at M5.
     Scored two ways: the raw prompt (what the board does now) and the
     difference axis normalize(e_pos - e_neg).

WHAT IT FOUND, 2026-08-07
-------------------------
Part A: state pairs sit at 9.1-32.1 degrees while the *object* controls sit at
25.8-31.1. "opened book" vs "closed book" is 13.5 degrees -- less than half the
separation of "a book" vs "a cup" at 30.8. There is room, but not much.

Part B is where val2017 runs out: COCO captions almost never say "empty glass"
or "closed door", so several rows come back with n_neg = 0 and the book row has
three positives. That is why tools/probe_states2.py exists and reframes the
question on train2017. Keep this script for part A, which needs no images and is
the cheapest sanity check there is on a new prompt pair.
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

SPLIT = "val2017"

STATE_PAIRS = [
    ("book open/closed", "an opened book", "a closed book"),
    ("glass full/empty", "a glass of water", "an empty glass"),
    ("box orientation", "an upside-down box", "an upright box"),
    ("door open/closed", "an open door", "a closed door"),
    ("light on/off", "a lamp that is switched on", "a lamp that is switched off"),
    ("person posture", "a person sitting down", "a person standing up"),
]
OBJECT_PAIRS = [
    ("book/cup", "a book", "a cup"),
    ("book/laptop", "a book", "a laptop"),
    ("cat/dog", "a cat", "a dog"),
    ("person/car", "a person", "a car"),
]

# (label, positive caption regex, negative caption regex). Ground truth is the
# human caption, so a hit means a person thought the state was worth writing
# down -- which is the same bar the alert would have to clear.
CAPTION_TESTS = [
    ("book open/closed", r"\bopen(ed)? book\b|\bbook (is |lying )?open\b",
     r"\bclosed book\b|\bstack of books\b|\bpile of books\b"),
    ("glass full/empty", r"\bglass of (water|wine|juice|milk)\b|\bfull glass\b",
     r"\bempty glass\b|\bempty wine glass\b"),
    ("door open/closed", r"\bopen door\b|\bdoor is open\b|\bdoor open\b",
     r"\bclosed door\b|\bdoor is closed\b|\bdoor closed\b"),
    ("person posture", r"\b(sitting|seated|sits)\b", r"\b(standing|stands)\b"),
]


def auc(scores: np.ndarray, positive: np.ndarray) -> float:
    """P(a random positive outranks a random negative). 0.5 is chance."""
    order = np.argsort(scores)
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    # average ties
    s = scores[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2
        i = j + 1
    npos, nneg = positive.sum(), (~positive).sum()
    if npos == 0 or nneg == 0:
        return float("nan")
    return float((ranks[positive].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def main():
    device = teacher_mod.pick_device()
    model, _ = teacher_mod.load_clip(device)

    print("=" * 78)
    print("A. TEXT GEOMETRY -- how much room does the distinction have at all?")
    print("=" * 78)
    print(f"{'pair':22} {'cos(a,b)':>10} {'angle':>8}   the two prompts")
    for title, pairs in (("STATE / RELATION", STATE_PAIRS),
                         ("OBJECT (control)", OBJECT_PAIRS)):
        print(f"\n-- {title} --")
        for name, a, b in pairs:
            v = teacher_mod.encode_queries(model, [a, b], device)
            c = float(v[0] @ v[1])
            deg = np.degrees(np.arccos(np.clip(c, -1, 1)))
            print(f"{name:22} {c:>10.4f} {deg:>7.1f} deg   {a!r} vs {b!r}")

    print()
    print("=" * 78)
    print(f"B. REAL AUC on COCO {SPLIT} -- cached teacher embeddings, captions as truth")
    print("=" * 78)

    names = data.image_list(SPLIT)
    idx_of = {n: i for i, n in enumerate(names)}
    emb = teacher_mod.load_cache(SPLIT).astype(np.float32)

    caps = json.loads((data.DATA / "annotations" /
                       f"captions_{SPLIT}.json").read_text())
    fname = {im["id"]: im["file_name"] for im in caps["images"]}
    text: dict[int, str] = {}
    for a in caps["annotations"]:
        text[a["image_id"]] = text.get(a["image_id"], "") + " " + a["caption"].lower()

    print(f"images   : {len(names)}, {len(text)} with captions\n")
    print(f"{'pair':22} {'n_pos':>6} {'n_neg':>6} {'AUC raw':>9} {'AUC diff':>9}   verdict")

    lookup = {name: (a, b) for name, a, b in STATE_PAIRS}
    for label, pos_re, neg_re in CAPTION_TESTS:
        a, b = lookup[label]
        pr, nr = re.compile(pos_re), re.compile(neg_re)
        rows, flags = [], []
        for iid, t in text.items():
            f = fname.get(iid)
            if f not in idx_of:
                continue
            p, n = bool(pr.search(t)), bool(nr.search(t))
            if p == n:            # neither, or contradictory captions
                continue
            rows.append(idx_of[f])
            flags.append(p)
        # Both classes, not just the row count. COCO captions say "a glass of
        # water" often and "an empty glass" almost never, so these rows arrive
        # with 19 positives and zero negatives -- and an AUC over one class is
        # nan, which slides through the verdict ladder below and comes out
        # "good" because every `nan < x` is False. Reject it here instead.
        npos, nneg = sum(flags), len(flags) - sum(flags)
        if npos < 5 or nneg < 5:
            print(f"{label:22} {npos:>6} {nneg:>6} "
                  f"{'--':>9} {'--':>9}   too few captions to say anything")
            continue
        rows = np.array(rows)
        flags = np.array(flags)
        e = emb[rows]
        qv = teacher_mod.encode_queries(model, [a, b], device)
        d = qv[0] - qv[1]
        d = d / np.linalg.norm(d)
        a_raw = auc(e @ qv[0], flags)
        a_dif = auc(e @ d, flags)
        best = max(a_raw, a_dif)
        verdict = ("useless" if best < 0.65 else
                   "weak" if best < 0.80 else
                   "usable" if best < 0.90 else "good")
        print(f"{label:22} {flags.sum():>6} {(~flags).sum():>6} "
              f"{a_raw:>9.3f} {a_dif:>9.3f}   {verdict}")

    # The control: the same machinery on an object query, so the numbers above
    # have something to be bad relative to.
    print()
    for obj, rx in (("a cat", r"\bcat\b"), ("a laptop", r"\blaptop\b"),
                    ("a book", r"\bbook\b")):
        r = re.compile(rx)
        rows = np.array([idx_of[fname[i]] for i, t in text.items()
                         if fname.get(i) in idx_of])
        flags = np.array([bool(r.search(text[i])) for i in text
                          if fname.get(i) in idx_of])
        qv = teacher_mod.encode_queries(model, [obj], device)
        print(f"{('control ' + obj):22} {flags.sum():>6} {(~flags).sum():>6} "
              f"{auc(emb[rows] @ qv[0], flags):>9.3f} {'--':>9}   object identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
