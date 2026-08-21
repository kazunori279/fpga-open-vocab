# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Where in the chain is a pair's axis lost? Issue #24.

    uv run --script tools/probe_bisect.py \
        --a bench/stills/20260821-glass/tea \
        --b bench/stills/20260821-glass/empty \
        --pos "a glass with tea" --neg "an empty glass"

THE QUESTION, AND WHY IT IS NOT ANSWERABLE AT THE BENCH
--------------------------------------------------------
`a glass with tea` / `an empty glass` has been run four times and reads |sep|
0.699 / 0.680 / 0.674 / 0.591, inverted on two of them. #24 records why a fifth
run cannot say anything new: staging, a bad morning, colour and the phrasing
have each been controlled for, and what is left is the model. This measures the
same pair at each stage the model has, on the same pixels, so the stage that
loses it can be named instead of guessed at.

  teacher 1152   SigLIP 2 SO400M, unprojected. If it is low HERE, no board-side
                 work helps and the answer belongs on #23.
  pca 512        the frozen joint projection the student was distilled against.
                 If it drops here it is the cheapest fix in the project: refit
                 the basis on a bank that has fill-state contrasts in it. No
                 retraining.
  student        the 1.4 M-parameter distilled net, float32.
  student int4   what the board actually runs, through model/export.py's golden
                 integer pipeline. Not implemented here yet - it needs the
                 calibration loader, and #24's own order says to stop at the
                 first stage that loses the axis rather than run all four.

THE QUANTITY IS THE ONE tools/probe_ceiling.py ALREADY USES
------------------------------------------------------------
Margin = `cos(image, e_pos) - cos(image, e_neg)`, and AUC over it is
P(a random A-frame outranks a random B-frame). With two queries the board's
centred space is one-dimensional and that margin carries everything the board
could possibly use, so the number here is directly comparable to `|sep|` in the
bench table - the difference being that here the frames are stills and the
encoder is swappable.

Prompts are TEMPLATES-ensembled and the projection is applied after the
ensembling, because that is the order model/teacher.py:encode_queries_spec
defines and host/demo.py ships. Bare prompts would measure a path nothing runs.

TWO CONTROLS, AND THEY ARE THE POINT OF SHOOTING IN ROUNDS
-----------------------------------------------------------
A pooled AUC on 30-vs-30 stills is worth very little on its own, because
anything that drifts while the scenes are being swapped shows up as class
signal. So the stills are shot A/B/A/B/A/B and two more numbers are printed
beside the pooled one:

  within   the same margin AUC computed inside each round - round 1's A against
           round 1's B - then folded and averaged. Minutes apart rather than
           the whole session, so it asks whether the scenes separate when
           nothing has had time to move.
  drift    the largest folded AUC between two ROUNDS OF THE SAME CLASS. This is
           the null: it is the same object, unmoved, and any separation it
           shows is the room and the sensor. **A `within` that does not clearly
           beat `drift` is not evidence about the pair at all**, whatever the
           pooled figure says, and reading the pooled figure without it is how
           four benches turned into an argument about mornings.

WHAT IT FOUND, 2026-08-21   (66 glass stills, 22 book stills, so400m-full-a05)
------------------------------------------------------------------------------
  pair                       teacher 1152   pca 512   student fp32   |sep|
  an opened / a closed book     26.0 sd      24.1 sd      8.2 sd      1.000
  a glass with tea / empty       7.9 sd       5.4 sd      0.2 sd      0.533

**The axis is lost at the student, and only at the student.** Two candidate
causes die on the teacher row alone:

  RESOLUTION IS NOT IT. The teacher is fed the same 128x128 PNGs the student
  gets - upscaled to its own input size, which adds no information - and reads
  7.9 sd. "128x128 does not resolve the fill state" was on #23's list and is now
  off it.

  THE PROJECTION IS NOT IT, so neither is the cheap fix. #24 called refitting
  the 1152->512 basis on a bank with fill-state contrasts "the cheapest possible
  fix"; the basis passes 5.4 sd through and there is nothing there to recover.

The book control is what makes the student row readable. Same session, same
camera, same script, same stage: the student carries the book axis at 8.2 sd and
the glass axis at 0.2 sd, which is 0.8 sd BELOW that pair's own round-to-round
drift. So this is not a student that scores everything low - it is a student
that did not inherit this one axis.

TWO THINGS THIS DOES NOT SHOW, both worth more than the table above
--------------------------------------------------------------------
**It does not show the difference is gone from the student.** The held-out
oracle - the best fitted direction, scored on a round it was not fitted on -
reads 1.000 for the glass pair at every stage including the student. The
student's embedding does move between the two scenes. What it does not do is
move along the direction the text query points at: the class-mean difference has
cosine +0.031 with the teacher's, against +0.158 for the book pair. Both are
small, because the student's geometry is its own; the ratio is the signal.

**And the oracle is not evidence of a bound concept**, because mean frame luma
separates the glass pair at AUC 1.000 all by itself (108 against 133 - tea is
darker, and the capture logs' exposure ramps settle twenty counts apart). It
separates the book pair too. Any encoder will "hold the difference" between two
sets of images that differ in brightness. The oracle rules out "the student
threw the frames away"; it does not rule in "the student knows what tea is."

So the live question moves from capacity to distillation: the student has the
frames apart and puts them apart in the wrong direction. That is #24's fourth
row, and it is a milestone rather than a bench - but it is a different milestone
from "the model is too small", and nothing above supports that one.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision import transforms

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import distill
import open_clip
import probe_teacher as pt
import student as student_mod
import teacher as teacher_mod
from spaces import resolve


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(a random `pos` outranks a random `neg`), ties counted as half.

    Rank-based rather than the O(nm) double loop, and ties are half a win
    because a margin computed in float32 off 128x128 stills does produce exact
    ties and silently scoring them as losses would bias every figure the same
    direction.
    """
    both = np.concatenate([pos, neg])
    order = np.argsort(both, kind="mergesort")
    s = both[order]
    ranks = np.empty(len(both), dtype=np.float64)
    # Average the ranks inside each tied group, which is what makes a tie
    # contribute exactly 0.5.
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    n, m = len(pos), len(neg)
    return float((ranks[:n].sum() - n * (n + 1) / 2.0) / (n * m))


def folded(a: np.ndarray, b: np.ndarray) -> float:
    v = auc(a, b)
    return max(v, 1.0 - v)


def round_of(path: Path) -> str:
    """`r2-m9-05-f0013-hi.png` -> `r2`. Everything else lands in one bucket."""
    stem = path.name.split("-", 1)[0]
    return stem if stem.startswith("r") and stem[1:].isdigit() else "r?"


def load(dirname: Path) -> tuple[list[Path], list[Image.Image]]:
    files = sorted(dirname.glob("*.png"))
    if not files:
        raise SystemExit(f"{dirname}: no PNGs")
    return files, [Image.open(f).convert("RGB") for f in files]


def report(name: str, ma: np.ndarray, mb: np.ndarray,
           ra: list[str], rb: list[str]) -> None:
    """One stage's rows: the AUCs, and the effect sizes for when they saturate.

    AUC ALONE IS NOT ENOUGH HERE and the first run of this script proved it: the
    teacher read 1.000 for the class AND 0.983 for the drift null, which says
    only that both are past the top of the scale. An AUC cannot distinguish "the
    two classes are barely apart" from "they are ten noise-widths apart", and
    the whole question is which. So the same two comparisons are also printed as
    a distance, in units of the spread of frames that ought to be identical:

      d      class gap / sd - how far apart the two scenes are
      d(rnd) the largest gap between two ROUNDS OF ONE CLASS, over the same sd

    sd is pooled over frames within one class and one round, which is as close
    to a repeat measurement as this bench gets. d is only worth reading when it
    is well clear of d(rnd); where it is not, the axis is measuring the morning.
    """
    ra, rb = np.array(ra), np.array(rb)
    rounds = sorted(set(ra.tolist()) | set(rb.tolist()))
    sep = auc(ma, mb)

    within, cells, spreads, cell_means = [], [], [], {}
    for r in rounds:
        sa, sb = ma[ra == r], mb[rb == r]
        if not (len(sa) and len(sb)):
            continue
        within.append(folded(sa, sb))
        cells.append(f"{r} {within[-1]:.3f}")
        for tag, s in (("a", sa), ("b", sb)):
            cell_means[(tag, r)] = float(s.mean())
            if len(s) > 1:
                spreads.append(float(s.std(ddof=1)))

    drift = 0.0
    for m, rr in ((ma, ra), (mb, rb)):
        for i, x in enumerate(rounds):
            for y in rounds[i + 1:]:
                sx, sy = m[rr == x], m[rr == y]
                if len(sx) and len(sy):
                    drift = max(drift, folded(sx, sy))

    sd = float(np.sqrt(np.mean(np.square(spreads)))) if spreads else float("nan")
    gap = abs(float(ma.mean()) - float(mb.mean()))
    swing = max((abs(cell_means[(t, x)] - cell_means[(t, y)])
                 for t in ("a", "b")
                 for i, x in enumerate(rounds) for y in rounds[i + 1:]
                 if (t, x) in cell_means and (t, y) in cell_means), default=0.0)
    w = float(np.mean(within)) if within else float("nan")
    # One round means the null was not measured, which is a different thing
    # from a null of zero and has to read differently or it flatters the row.
    many = len(rounds) > 1
    print(f"  {name:<14} sep {sep:.3f}  |sep| {max(sep, 1-sep):.3f}  "
          f"within {w:.3f}  drift " + (f"{drift:.3f}" if many else "  n/a"))
    print(f"  {'':<14} gap {gap:.4f} = {gap / sd:5.1f} sd   round swing "
          + (f"{swing:.4f} = {swing / sd:5.1f} sd" if many
             else "not measured - one round")
          + f"   (sd {sd:.4f})")
    print(f"  {'':<14} per round: {'  '.join(cells)}")


def oracle(name: str, va: np.ndarray, vb: np.ndarray,
           ra: list[str], rb: list[str]) -> None:
    """The best the stage COULD do, if the query pointed exactly the right way.

    This is the row that separates the two ways a stage can lose an axis, and
    they need completely different work:

      the representation does not hold the difference   -> capacity, a milestone
      it holds it and the text vector does not point at it -> the query, or a
      rotation of the head, and both are cheap

    So the axis here is not a phrase at all: it is `normalize(mean(A)-mean(B))`,
    fitted on the embeddings. FITTED, i.e. an oracle, and it is held out by
    round - the direction is fitted on the other rounds and scored on the one
    left out - because an in-sample direction on 33 points in 512 dimensions
    separates anything at all, including two halves of one class.
    """
    ra, rb = np.array(ra), np.array(rb)
    rounds = sorted(set(ra.tolist()) & set(rb.tolist()))
    held, cells = [], []
    for r in rounds:
        fa, fb = va[ra != r], vb[rb != r]
        if not (len(fa) and len(fb)):
            continue
        d = fa.mean(0) - fb.mean(0)
        d /= np.linalg.norm(d)
        f = folded(va[ra == r] @ d, vb[rb == r] @ d)
        held.append(f)
        cells.append(f"{r} {f:.3f}")
    if not held:
        print(f"  {name:<14} held-out oracle AUC n/a - needs two or more rounds")
        return
    print(f"  {name:<14} held-out oracle AUC {np.mean(held):.3f}   "
          f"per round: {'  '.join(cells)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a", type=Path, required=True, help="stills of the positive scene")
    ap.add_argument("--b", type=Path, required=True, help="stills of the negative scene")
    ap.add_argument("--pos", required=True, help="the phrase for --a")
    ap.add_argument("--neg", required=True, help="the phrase for --b")
    ap.add_argument("--run", default="so400m-full-a05", help="student under model/runs/")
    ap.add_argument("--no-student", action="store_true",
                    help="teacher stages only; skips loading the checkpoint")
    args = ap.parse_args()

    ckpt = torch.load(ROOT / "model/runs" / args.run / "student.pt",
                      map_location="cpu", weights_only=False)
    spec, basis_path = resolve(ckpt.get("teacher", ""))
    name, pre = spec.split(":")
    device = pt.pick_device()

    fa, ia = load(args.a)
    fb, ib = load(args.b)
    ra, rb = [round_of(f) for f in fa], [round_of(f) for f in fb]
    print(f"pair      : '{args.pos}'  vs  '{args.neg}'")
    print(f"stills    : {len(fa)} from {args.a}, {len(fb)} from {args.b}")
    print(f"rounds    : {sorted(set(ra))} / {sorted(set(rb))}")
    print(f"teacher   : {spec}, {len(teacher_mod.TEMPLATES)} templates")
    print(f"basis     : {basis_path.name if basis_path else 'none'}")
    print(f"student   : {args.run}, epoch {ckpt['epoch']}")
    print(f"device    : {device}\n")

    basis = np.load(basis_path) if basis_path else None
    model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pre)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(name)

    names = [args.pos, args.neg]
    tv_full = teacher_mod.encode_queries_spec(model, tok, names, device, None)
    tv_pca = teacher_mod.encode_queries_spec(model, tok, names, device, basis)

    def encode_images(pils):
        out = []
        for i in range(0, len(pils), 16):
            batch = torch.stack([preprocess(p) for p in pils[i:i + 16]]).to(device)
            with torch.no_grad():
                out.append(model.encode_image(batch).float().cpu().numpy())
        v = np.concatenate(out)
        return v / np.linalg.norm(v, axis=-1, keepdims=True)

    va, vb = encode_images(ia), encode_images(ib)

    def margins(a, b, tv):
        """The board's margin for both scenes, as z and not as a raw cosine gap.

        firmware/m9.c scores `z = (cos - background) / std` per query and then
        centres, so with two queries the decision variable is z[A] - z[B]. The
        background is a per-query constant and cancels out of any ranking; the
        std does NOT - it reweights the two queries against each other, and a
        raw `cos_A - cos_B` therefore hands whichever query happens to swing
        more the casting vote. That is not the quantity the appliance uses and
        it is not what tools/probe_ceiling.py reads off a bench log.

        mean and std are pooled over both scenes' frames, which is symmetric
        between the two queries and is the closest stand-in for the board's
        first-30-frames background that a directory of stills allows.
        """
        z = []
        for q in (0, 1):
            both = np.concatenate([a @ tv[q], b @ tv[q]])
            s = both.std(ddof=1) or 1.0
            z.append((both - both.mean()) / s)
        m = z[0] - z[1]
        return m[:len(a)], m[len(a):]

    def project(v):
        p = (v - basis["mu"]) @ basis["w"]
        return p / np.linalg.norm(p, axis=-1, keepdims=True)

    stages = [("teacher 1152", va, vb, tv_full)]
    if basis is not None:
        stages.append(("pca 512", project(va), project(vb), tv_pca))

    if not args.no_student:
        net = student_mod.Student()
        net.load_state_dict(ckpt["state_dict"])
        net = net.to(device).eval()
        # Plain normalize, not camera_transform(): these PNGs came off the Mega
        # at 128x128 already, so simulating the crop would apply it twice. The
        # same note is in probe_inherit.py and in probe_open.py, where getting
        # it wrong once made a student look better than it was.
        tf = transforms.Compose(
            [transforms.ToTensor(),
             transforms.Normalize(distill.PIXEL_MEAN, distill.PIXEL_STD)])

        def student_embed(pils):
            with torch.no_grad():
                e = net(torch.stack([tf(p) for p in pils]).to(device))
            e = e / e.norm(dim=-1, keepdim=True)
            return e.cpu().numpy().astype(np.float32)

        # The student emits into the teacher's *projected* space by
        # construction, so it is scored against tv_pca and not a space of its
        # own.
        stages.append(("student fp32", student_embed(ia), student_embed(ib),
                       tv_pca))

    print(f"{'='*78}\nMARGIN AUC BY STAGE, on the phrases as the board sends "
          "them")
    for sname, a, b, tv in stages:
        report(sname, *margins(a, b, tv), ra, rb)

    print(f"\n{'='*78}\nAND WITH THE QUERY TAKEN OUT OF IT - a fitted axis, "
          "held out by round")
    for sname, a, b, _tv in stages:
        oracle(sname, a, b, ra, rb)
    print("  A stage that reads high here and low above holds the difference "
          "and is\n  merely not being asked for it. A stage low in both has "
          "lost it.")

    # THE TRIVIAL CUE, because the oracle above cannot tell a bound concept from
    # a brightness difference and these two scenes have one: tea is darker than
    # an empty glass, and the exposure ramps in the capture logs settle about
    # twenty counts apart. If plain mean luma already separates the classes then
    # "the representation holds the difference" is true and says less than it
    # sounds - it does NOT say the stage knows what tea is.
    def luma(pils):
        return np.array([np.asarray(p, dtype=np.float64).mean() for p in pils])

    la, lb = luma(ia), luma(ib)
    print(f"\n  trivial cue: frame mean luma AUC {folded(la, lb):.3f}  "
          f"({la.mean():.1f} vs {lb.mean():.1f})")

    if len(stages) > 2:
        # DOES THE STUDENT PUT THE DIFFERENCE WHERE ITS TEACHER PUT IT? The
        # student is trained to emit the projected teacher vector, so the two
        # live in one 512-d space and their class-mean differences can be
        # compared directly. A difference of means also cancels anything shared
        # by every frame, which matters here - see below.
        #
        # PER-FRAME cos(student, teacher) WAS TRIED FIRST AND IS NOT REPORTED,
        # because it measures the cone and not the scene. It reads 0.475 on
        # these stills, which looks alarming until the same number is taken on
        # bench/cue's frames - 0.428, on runs where the board scored 100% - and
        # against a constant vector, which scores 0.957 and 0.841 on the same
        # two sets. config.json says the same thing about the training split:
        # constant_cosine 0.643 against best_cosine 0.672, and cone_norm 0.447
        # where the teacher's is 0.645. Raw agreement with the teacher is not a
        # thing this student was ever going to have, and quoting it would have
        # been a scary number that predicts nothing.
        _, ta, tb, _ = stages[1]
        d_t = ta.mean(0) - tb.mean(0)
        for sname, a, b, _tv in stages[2:]:
            d_s = a.mean(0) - b.mean(0)
            axis = float(d_s @ d_t / (np.linalg.norm(d_s) * np.linalg.norm(d_t)))
            print(f"\n  class axis, {sname} against pca 512: cos {axis:+.3f}"
                  "   (1.0 = the same difference, 0.0 = an unrelated one)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
