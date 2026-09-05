# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Is the empty scene off the line the two classes make? Issue #31.

    uv run --script tools/probe_offplane.py \
        --a bench/stills/20260825-empty-book/open \
        --b bench/stills/20260825-empty-book/closed \
        --empty bench/stills/20260825-empty-book/empty \
        --pos "an opened book" --neg "a closed book" \
        --third "an empty desk" --third "a bare table"

#31'S FIRST CHECKLIST ITEM, AND IT IS THE ONE THAT CAN CLOSE THE ISSUE CHEAPLY.
With two queries the board's centred space is EXACTLY one-dimensional - measured
to 3.55e-15 over 8 514 pairs - so the empty reference is a point on the same line
the class decision is made on, and presence and class trade against each other by
arithmetic. At k = 3 there is a plane and "nothing there" has a direction
available that is not the class decision. Whether the model puts it there is an
empirical question and this asks it. If the answer is no, no presence rule built
on the extra dimension can work and #31 closes without a single bench.

WHAT IS MEASURED. In the centred z space the two class references make a line.
The number is `off`, the empty reference's perpendicular distance from that line,
in units of the distance between the classes. `along` is where its foot lands on
the line, in the same units - the `pos` of tools/probe_third.py, and the reason
the current nearest-of-three rule works at all: 0.5 is the midpoint that
tools/probe_midpoint.py measured and rejected, and the archive's median is 0.88.

  off = 0    the empty scene is ON the class line and the third query bought
             nothing. At k = 2 this is not a finding, it is arithmetic, and the
             k=2 row is printed as a self-test - anything but 0.00 is a bug here.
  off large  there is a direction to build a presence rule on, and #31 lives.

`gain` is the practical consequence rather than the geometry: balanced accuracy
of nearest-of-three over held-out frames, at k = 2 against each k = 3 candidate.
Geometry that does not move this is geometry nobody can use.

ROUNDS ENROL, ROUNDS SCORE. No frame is ever scored against a reference its own
round helped build, because references and frames drawn from the same stillness
is the optimism that made a 42.3% look like a 90%. Two rounds is the floor and
the tool refuses below it, for the same reason bench/stills/shoot.sh takes a
round number at all.

`gain` leaves ONE ROUND out at a time and averages the folds; `g1` enrols on
round 1 alone and scores the rest, which is what the appliance actually does.
Both are printed because they came apart on 2026-08-27's set, where the AEC
settled at a different operating point in each round - the open book read mean
RGB 120, 66 and 84 across three rounds of the same scene. `g1` then measures how
unlucky round 1 was as much as it measures the geometry. When the two agree the
distinction does not matter; when they disagree, read `gain` for the geometry
and treat the gap as the cost of enrolling once.

THIS IS NOT A BENCH and cannot produce one. No enrolment guard, no hysteresis, no
LED, no int4, and a directory of stills holds the staging still on purpose. A
number here that reads well still has to be benched. What it CAN do is say that a
direction does not exist, which no bench can see.
"""
import argparse
import itertools
import json
import re
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision import transforms

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))

import distill
import open_clip
import probe_teacher as pt
import student as student_mod
import teacher as teacher_mod
from spaces import resolve


def round_of(path: Path) -> str:
    m = re.match(r"r(\d+)-", path.name)
    return m.group(1) if m else "?"


def load(dirname: Path):
    files = sorted(p for p in dirname.glob("*.png"))
    if not files:
        raise SystemExit(f"no PNGs in {dirname}")
    return files, [Image.open(p).convert("RGB") for p in files]


def centred(cos_by_scene):
    """The board's decision vector: z per query, then centred across queries.

    firmware/m9.c scores z = (cos - background) / std per query and subtracts the
    mean across queries before deciding. The background is a per-query constant
    and cancels; the std does NOT, because it reweights the queries against each
    other, so a raw cosine gap hands the casting vote to whichever query happens
    to swing more.

    THE BACKGROUND IS ONE CONSTANT PER QUERY FOR THE WHOLE SET, pooled over every
    scene's frames. Standardising each scene against its own mean is the bug the
    first draft of this tool shipped with: it drives every scene's centre to the
    same point and every `gain` in the table to chance, because the between-scene
    difference is exactly what it divides out. Pooling is also symmetric between
    the queries, and is the nearest stand-in for the board's first-thirty-frames
    background that a directory of stills allows.

    Takes {scene: [cos per query]}, returns {scene: frames x queries}.
    """
    scenes = list(cos_by_scene)
    nq = len(cos_by_scene[scenes[0]])
    mu, sd = [], []
    for q in range(nq):
        pooled = np.concatenate([cos_by_scene[k][q] for k in scenes])
        mu.append(pooled.mean())
        sd.append(pooled.std(ddof=1) or 1.0)
    out = {}
    for k in scenes:
        z = np.stack([(cos_by_scene[k][q] - mu[q]) / sd[q] for q in range(nq)])
        out[k] = (z - z.mean(axis=0, keepdims=True)).T    # frames x queries
    return out


def geometry(cA, cB, cE):
    """(along, off) for the empty reference, in units of the class separation."""
    ab = cB - cA
    n = float(np.linalg.norm(ab))
    if n == 0:
        return float("nan"), float("nan")
    u = ab / n
    d = cE - cA
    along = float(d @ u) / n
    off = float(np.linalg.norm(d - (d @ u) * u)) / n
    return along, off


def balanced(frames, labels, refs):
    """Balanced accuracy of nearest-reference, and the per-scene recalls.

    The recalls are printed beside the mean because the mean hides its own
    shape: three scenes at 67% each and two perfect scenes beside one that is
    never recognised both average 66.7, and only one of those is a rule worth
    building on.
    """
    names = list(refs)
    pred = [names[int(np.argmin([np.linalg.norm(f - refs[k]) for k in names]))]
            for f in frames]
    per = {}
    for lab in names:
        got = [p == lab for p, t in zip(pred, labels, strict=True) if t == lab]
        if got:
            per[lab] = 100.0 * sum(got) / len(got)
    mean = sum(per.values()) / len(per) if per else float("nan")
    return mean, per


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a", type=Path, required=True, help="stills of class A")
    ap.add_argument("--b", type=Path, required=True, help="stills of class B")
    ap.add_argument("--empty", type=Path, required=True,
                    help="stills of the empty scene. NOT a class - it is the "
                         "thing the extra dimension is supposed to be for")
    ap.add_argument("--pos", required=True, help="the phrase for --a")
    ap.add_argument("--neg", required=True, help="the phrase for --b")
    ap.add_argument("--third", action="append", default=[], metavar="PHRASE",
                    help="a candidate third query, repeatable. Each is scored "
                         "on its own against the same pixels and the same two "
                         "class phrases, so the rows are comparable")
    ap.add_argument("--run", default="so400m-full-a05")
    ap.add_argument("--no-student", action="store_true",
                    help="teacher stages only; skips loading the checkpoint")
    ap.add_argument("--json", type=Path, default=None, metavar="PATH")
    args = ap.parse_args()

    ck = ROOT / "model/runs" / args.run / "student.pt"
    if not ck.exists():
        raise SystemExit(f"no {ck}")
    ckpt = torch.load(ck, map_location="cpu", weights_only=False)
    spec, basis_path = resolve(ckpt.get("teacher", ""))
    name, pre = spec.split(":")
    device = pt.pick_device()

    scenes = {"A": args.a, "B": args.b, "empty": args.empty}
    files, pils = {}, {}
    for k, d in scenes.items():
        files[k], pils[k] = load(d)
    rounds = {k: [round_of(f) for f in files[k]] for k in scenes}
    allr = sorted({r for rs in rounds.values() for r in rs})
    if len(allr) < 2:
        raise SystemExit(
            f"rounds present: {allr}. Two is the floor - references and frames "
            f"out of one stillness measure the stillness. Shoot another round "
            f"with bench/stills/shoot.sh before reading anything off this set.")

    print(f"classes   : '{args.pos}' (A)  vs  '{args.neg}' (B)")
    print("stills    : " + ", ".join(f"{len(files[k])} {k}" for k in scenes))
    print(f"rounds    : {allr}  - `gain` leaves one out, `g1` enrols on "
          f"round {allr[0]} only")
    print(f"teacher   : {spec}")
    print(f"thirds    : {len(args.third)} candidate" + ("s" if len(args.third) != 1 else ""))
    print(f"device    : {device}\n")

    basis = np.load(basis_path) if basis_path else None
    model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pre)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(name)

    def encode_images(ps):
        out = []
        for i in range(0, len(ps), 16):
            batch = torch.stack([preprocess(p) for p in ps[i:i + 16]]).to(device)
            with torch.no_grad():
                out.append(model.encode_image(batch).float().cpu().numpy())
        v = np.concatenate(out)
        return v / np.linalg.norm(v, axis=-1, keepdims=True)

    def project(v):
        p = (v - basis["mu"]) @ basis["w"]
        return p / np.linalg.norm(p, axis=-1, keepdims=True)

    vis = {k: encode_images(pils[k]) for k in scenes}
    stages = [("teacher 1152", vis, None)]
    if basis is not None:
        stages.append(("pca 512", {k: project(v) for k, v in vis.items()}, basis))

    if not args.no_student:
        # Plain normalize, not camera_transform(): these PNGs came off the Mega
        # at 128x128 already, so simulating the crop would apply it twice.
        tf = transforms.Compose(
            [transforms.ToTensor(),
             transforms.Normalize(distill.PIXEL_MEAN, distill.PIXEL_STD)])
        net = student_mod.Student()
        net.load_state_dict(ckpt["state_dict"])
        net = net.to(device).eval()

        def student_embed(ps):
            with torch.no_grad():
                e = net(torch.stack([tf(p) for p in ps]).to(device))
            e = e / e.norm(dim=-1, keepdim=True)
            return e.cpu().numpy().astype(np.float32)

        # The student emits into the teacher's PROJECTED space by construction,
        # so it is scored against the projected query vectors and not its own.
        stages.append((args.run.replace("so400m-", ""),
                       {k: student_embed(pils[k]) for k in scenes}, basis))

    querysets = [("k=2  (self-test)", [args.pos, args.neg])]
    querysets += [(f"k=3  + '{t}'", [args.pos, args.neg, t]) for t in args.third]

    out = {"pos": args.pos, "neg": args.neg, "thirds": args.third,
           "n": {k: len(files[k]) for k in scenes}, "rows": []}

    print(f"{'='*78}\nWHERE THE EMPTY REFERENCE SITS, in units of the class "
          f"separation\n")
    print(f"{'stage':<14} {'queries':<34} {'along':>6} {'off':>6} {'gain':>6}"
          f" {'g1':>6}   {'A':>5} {'B':>5} {'empty':>5}")
    for sname, emb, bs in stages:
        for qname, phrases in querysets:
            tv = teacher_mod.encode_queries_spec(model, tok, phrases, device, bs)
            per = centred({k: [emb[k] @ tv[q] for q in range(len(phrases))]
                           for k in scenes})
            def rows_in(k, keep, _per=per):
                return _per[k][[i for i, r in enumerate(rounds[k]) if keep(r)]]

            # Geometry from references pooled over EVERY round. It is a property
            # of where three points sit, not a prediction about unseen frames,
            # so it wants the best estimate of each point rather than a split -
            # and pooling is what stops one unlucky round from setting it. The
            # k=2 self-test is unaffected: a line is a line however it is fitted.
            refs_all = {k: per[k].mean(axis=0) for k in scenes}
            along, off = geometry(refs_all["A"], refs_all["B"],
                                  refs_all["empty"])

            # Accuracy, leaving one ROUND out at a time. `gain` is the mean over
            # the folds and `g1` is the appliance's own split - enrol once at the
            # start, run afterwards. They come apart when one round is unlike the
            # others, which on 2026-08-27's set was the whole story: the AEC
            # settled at a different operating point in each round, so g1 says
            # what an unlucky enrolment costs and gain says what the geometry is
            # worth once that is averaged out. Neither is the other's error bar.
            folds, g1, r1 = [], float("nan"), {}
            for test in allr:
                enrol = {k: rows_in(k, lambda r, t=test: r != t) for k in scenes}
                held = {k: rows_in(k, lambda r, t=test: r == t) for k in scenes}
                if any(len(v) == 0 for v in enrol.values()) or \
                   any(len(v) == 0 for v in held.values()):
                    raise SystemExit(f"a scene is missing round {test}; "
                                     f"re-shoot rather than pad")
                refs = {k: v.mean(axis=0) for k, v in enrol.items()}
                frames = np.concatenate([held[k] for k in scenes])
                labels = list(itertools.chain.from_iterable(
                    [k] * len(held[k]) for k in scenes))
                folds.append(balanced(frames, labels, refs))
            enrol1 = {k: rows_in(k, lambda r: r == allr[0]) for k in scenes}
            held1 = {k: rows_in(k, lambda r: r != allr[0]) for k in scenes}
            refs1 = {k: v.mean(axis=0) for k, v in enrol1.items()}
            g1, r1 = balanced(
                np.concatenate([held1[k] for k in scenes]),
                list(itertools.chain.from_iterable(
                    [k] * len(held1[k]) for k in scenes)), refs1)
            gain = sum(f[0] for f in folds) / len(folds)
            recall = {k: sum(f[1].get(k, float("nan")) for f in folds)
                      / len(folds) for k in scenes}
            print(f"{sname:<14} {qname:<34} {along:>6.2f} {off:>6.2f} "
                  f"{gain:>5.1f}% {g1:>5.1f}%   "
                  + " ".join(f"{recall.get(k, float('nan')):>4.0f}%"
                             for k in ("A", "B", "empty")))
            out["rows"].append({"stage": sname, "queries": phrases,
                                "along": along, "off": off, "gain": gain,
                                "g1": g1, "recall": recall, "recall_g1": r1})
        print()

    print("`off` 0.00 on every k=2 row is the arithmetic self-test, not a "
          "result: with two\nqueries the centred space is a line and the three "
          "references are on it by construction.\nA k=3 row that is also near "
          "zero says the model does not put the empty scene off the\nclass "
          "axis for that phrase, and no rule can recover what is not there.")

    if args.json:
        args.json.write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
