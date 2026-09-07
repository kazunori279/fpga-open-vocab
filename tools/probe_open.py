# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Can anything in this stack tell an opened book from a closed one?

    uv run --script tools/probe_open.py [--run NAME] [--set TSV]

Runs on PNGs a bench run already wrote, so it needs no board and no hands.
Three questions, in the order that makes a negative answer informative:

  1. Does the *teacher* separate them? If not, nothing downstream can, and the
     fix is the prompts, not the hardware.
  2. Does a difference vector separate them better than ranking the two prompts
     independently? normalize(e_open - e_closed) cancels the "book" component
     that dominates both and is what the board is actually drowning in.
  3. Does the *student* (fp32, 128px) keep whatever the teacher had?

WHICH TEACHER IS NOT THIS FILE'S CHOICE
---------------------------------------
It is the checkpoint's. `--run` names a student, and that student's
export/export.json names the teacher it was distilled from and the basis it
emits into. The two shipped stacks do NOT share a 512-d space:

    train2017         ViT-B-16-quickgelu:openai      basis null
    so400m-full-a05   ViT-SO400M-14-SigLIP2:webli    PCA-512

Scoring a so400m student against ViT-B/16 text vectors would compare two
different spaces and print numbers the whole way, so the teacher, the query
vectors and the teacher-side image embeddings are all taken from the run under
test and projected through its own basis. This is the same path host/demo.py
takes to build the vectors it sends the board.

WHAT IT FOUND, 2026-08-07, ON A SET THAT NO LONGER EXISTS
---------------------------------------------------------
Yes, no, and no. On the spread-front axis the teacher put the open book at
+0.0401 and the closed one at -0.0291; the student put them at +0.0065 and
+0.0052, a gap of 0.32 sd against the frame-to-frame spread that
probe_noise.py measures. On the opened-closed axis the student was
anti-correlated at -4.67 sd.

That was `train2017` under ViT-B/16, on four frames named m9-5*-f0*-hi.png.
**Those four files are gone.** They lived in /tmp/snaps, which is the third
thing this repo has lost to /tmp, and five probes hardcoded them. The numbers
above are kept as history and are NOT a baseline the current set can be read
against: different images, different teacher, different space. To compare
checkpoints, run both through this script on the same --set. That is a
controlled comparison; the paragraph above is not.

WHAT REPLACED THE SET
---------------------
bench/labelled/book-20260908.tsv - fifteen frames from the three cue runs of
2026-09-08, six CLOSED, six OPEN, three EMPTY, across three camera arms. The
manifest points at the runs' own snapshots rather than at copies. Six per class
instead of one means the gap below has a spread under it, which is what the
2026-08-07 version explicitly could not offer:

    The margins this script prints have no scale -- its two book-free frames
    are a span, not a standard deviation.

The summary now divides by the pooled within-class spread of this set. No
constant is fitted and none is carried between runs: every number is a relation
among the frames the invocation was given.

WHAT IT FOUND, 2026-09-08: THE THIRD ANSWER CHANGED
----------------------------------------------------
Both checkpoints on the same fifteen frames, OPEN-CLOSED gap in pooled
within-class sd, and whether the worst case of each class overlaps:

    axis            train2017 student      so400m-full-a05 student
    opened-closed   +0.42  OVERLAP         +3.97  no overlap
    pages-cover     -4.05  OVERLAP         -2.54  OVERLAP
    spread-front    +6.63  no overlap      +5.23  no overlap

    their teachers  ViT-B/16               SO400M -> PCA-512
    opened-closed   +10.76                 +26.10
    spread-front    +14.39                 +31.60

**The opened-closed axis is no longer inverted.** That is the axis the bench's
two queries define, and on the old stack the student was anti-correlated on it;
it now separates with the worst OPEN above the worst CLOSED. The per-frame line
shows the six OPEN values interleaving across the three camera arms instead of
falling into three blocks, so the gap is frame-level and not an arm effect.

Two things not to read into it. pages-cover is still inverted in both, so the
student has not simply inherited the teacher. And the retention numbers went
the *other* way: holdout cosine +0.8430 -> +0.6718, teacher-student cosine
0.73/0.88/0.76 -> 0.37/0.57/0.43 on CLOSED/EMPTY/OPEN. The so400m student is a
markedly worse fit to its teacher and better at the question, because the
teacher it is failing to match is so much stronger. Retention is not the metric.

WHAT THIS DOES NOT EXPLAIN
--------------------------
The bench of the same morning, on these same scenes, got the opened book right
26/60 with the camera gain locked and 23/60 with it free
(bench/cue/m9_cue-20260908-0602 and -0610). So the distinction survives
distillation in fp32 and still does not arrive. Everything between this script
and that number is untested by it: int4, and the enrolled nearest-reference
rule, which does not score the difference axis this script projects onto. The
board's own query AUC for "an opened book" was 0.966 on the free run, which
points at the enrolment geometry rather than at the model.
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))

import distill
import numpy as np
import open_clip
import student as student_mod
import teacher as teacher_mod
import torch
from PIL import Image

DEFAULT_SET = ROOT / "bench/labelled/book-20260908.tsv"
PROMPTS = [
    "a book",
    "an opened book",
    "a closed book",
    "an open book",
    "a page of printed text",
    "a book cover",
    "two pages of an open book",
    "the front cover of a closed book",
]
# (name, positive prompt, negative prompt) -> project onto normalize(pos - neg)
AXES = [
    ("opened-closed", "an opened book", "a closed book"),
    ("pages-cover", "a page of printed text", "a book cover"),
    ("spread-front", "two pages of an open book", "the front cover of a closed book"),
]


def load_set(path: Path):
    """[(label, arm, Path)] from the manifest, '#' comments skipped."""
    rows = []
    with path.open() as fh:
        for line in csv.reader((r for r in fh if not r.startswith("#")),
                               delimiter="\t"):
            if not line:
                continue
            label, arm, rel = (c.strip() for c in line[:3])
            png = ROOT / rel
            if not png.exists():
                raise SystemExit(f"{path}: {rel} does not exist")
            rows.append((label, arm, png))
    if not rows:
        raise SystemExit(f"{path}: no rows")
    return rows


def load_export(run: str) -> dict:
    """The run's export.json, with the basis resolved to a loaded npz or None.

    Deliberately re-read here rather than imported from host/demo.py: this
    script must keep working when run from a checkout that has no board
    attached and no serial dependencies installed.
    """
    import json

    import spaces
    side = ROOT / "model/runs" / run / "export/export.json"
    if not side.exists():
        raise SystemExit(
            f"{side}: not found. The query space cannot be guessed - both "
            f"shipped teachers are 512-d, so the wrong one would score instead "
            f"of failing. Re-export:\n  uv run model/export.py --run {run} "
            f"--wbits 4 --wsearch --ends8")
    blob = json.loads(side.read_text())
    blob["basis_npz"] = None
    if blob.get("basis"):
        b = spaces.CACHE / blob["basis"]
        if not b.exists():
            raise SystemExit(f"{b}: not found, and {run} emits into the "
                             f"projected space it defines. Rebuild it with "
                             f"tools/teacher_swap.py")
        blob["basis_npz"] = dict(np.load(b))
    return blob


def project(v: np.ndarray, basis) -> np.ndarray:
    """The image-side twin of teacher.encode_queries_spec()'s tail."""
    if basis is None:
        return v
    p = (v - basis["mu"]) @ basis["w"]
    return (p / np.linalg.norm(p, axis=-1, keepdims=True)).astype(np.float32)


def table(title, cos, labels, prompts):
    print(f"\n=== {title} ===")
    print(f"{'':16}" + "".join(f"{p[:15]:>17}" for p in prompts))
    for i, lab in enumerate(labels):
        print(f"{lab:16}" + "".join(f"{cos[i, j]:>17.4f}" for j in range(len(prompts))))


def summarise(title, img, qv, prompts, labels):
    """Per-class mean on each difference axis, and the gap over the spread.

    The divisor is this set's own pooled within-class spread. It is not a
    constant, it is not carried between invocations, and it is the reason six
    frames a class were collected instead of one: with n=1 the gap has no
    scale and a big-looking number can be one frame of camera noise.
    """
    print(f"\n--- {title}: projection onto normalize(pos - neg) ---")
    idx = {p: i for i, p in enumerate(prompts)}
    classes = sorted(set(labels))
    for name, pos, neg in AXES:
        d = qv[idx[pos]] - qv[idx[neg]]
        d = d / np.linalg.norm(d)
        vals = img @ d
        per = {c: vals[[i for i, l in enumerate(labels) if l == c]]
               for c in classes}
        cells = "  ".join(f"{c} {per[c].mean():+.4f}+-{per[c].std(ddof=1):.4f}"
                          for c in classes)
        print(f"  {name:16} {cells}")
        # Every frame, in manifest order, so a reader can check that the spread
        # above is frame-to-frame and not the camera arm sorting itself into
        # the classes. Manifest order is run by run, five frames a run
        # (C O C O E), so an arm effect would show up as three blocks.
        print(f"  {'':16} per frame: "
              + "  ".join(f"{l[0]}{v:+.3f}" for l, v in zip(labels, vals,
                                                            strict=True)))
        if "OPEN" in per and "CLOSED" in per:
            o, c = per["OPEN"], per["CLOSED"]
            # Pooled within-class spread of the two book classes only: EMPTY is
            # a different scene and folding it in would flatter the separation.
            pooled = np.sqrt(((len(o) - 1) * o.var(ddof=1)
                              + (len(c) - 1) * c.var(ddof=1))
                             / (len(o) + len(c) - 2))
            gap = (o.mean() - c.mean()) / pooled if pooled > 0 else float("nan")
            worst_o, worst_c = o.min(), c.max()
            print(f"  {'':16} OPEN-CLOSED gap {gap:+.2f} pooled sd, "
                  f"and the two worst cases "
                  f"{'do NOT overlap' if worst_o > worst_c else 'OVERLAP'} "
                  f"(worst OPEN {worst_o:+.4f} vs worst CLOSED {worst_c:+.4f})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", dest="tsv", type=Path, default=DEFAULT_SET,
                    help="labelled manifest: label<TAB>arm<TAB>path-from-root "
                         f"(default {DEFAULT_SET.relative_to(ROOT)})")
    # The whole point of the loss work is to change the third answer above, and
    # the only honest way to see whether it did is to put a new checkpoint
    # through the identical script on the identical images.
    ap.add_argument("--run", default="so400m-full-a05",
                    help="student checkpoint under model/runs/ (default: the "
                         "shipped student. The 2026-08-07 numbers in the "
                         "docstring are train2017's, on images that are gone)")
    args = ap.parse_args()

    rows = load_set(args.tsv)
    labels = [f"{lab} {arm[:9]}" for lab, arm, _ in rows]
    classes = [lab for lab, _, _ in rows]
    export = load_export(args.run)
    basis = export["basis_npz"]

    device = teacher_mod.pick_device()
    print(f"device   : {device}")
    print(f"set      : {args.tsv.relative_to(ROOT)}, {len(rows)} frames - "
          + ", ".join(f"{classes.count(c)} {c}" for c in sorted(set(classes))))
    print(f"run      : {args.run}")
    print(f"teacher  : {export['spec']}, {len(teacher_mod.TEMPLATES)} templates")
    print("space    : " + (f"projected by {export['basis']}" if basis is not None
                           else "the teacher's own, no projection")
          + f" -> {export['embed_dim']}-d")

    name, pretrained = export["spec"].split(":")
    model, _, preprocess = open_clip.create_model_and_transforms(
        name, pretrained=pretrained)
    model = model.to(device).eval()
    tokenizer = open_clip.get_tokenizer(name)
    qv = teacher_mod.encode_queries_spec(model, tokenizer, PROMPTS, device,
                                         basis=basis).astype(np.float32)

    pil = [Image.open(p).convert("RGB") for _, _, p in rows]
    with torch.no_grad():
        batch = torch.stack([preprocess(p) for p in pil]).to(device)
        te = model.encode_image(batch).float()
        te = (te / te.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32)
    # Projected with the same formula as the queries, or the teacher rows would
    # be scored in a space the student never saw.
    te = project(te, basis)

    table("TEACHER cosine", te @ qv.T, labels, PROMPTS)
    summarise("TEACHER", te, qv, PROMPTS, classes)

    ckpt = torch.load(ROOT / "model/runs" / args.run / "student.pt",
                      map_location="cpu", weights_only=False)
    net = student_mod.Student()
    net.load_state_dict(ckpt["state_dict"])
    net = net.to(device).eval()
    # NOT camera_transform(): that one takes a COCO source and *simulates* the
    # Mega's 4:3 crop and squash. These PNGs already came out of the Mega at
    # 128x128, so it would crop and squash a second time. Just normalize.
    #
    # This is not a nitpick. The first run of this script used camera_transform
    # and made the student look BETTER than it is; with the double-processing
    # removed the student's numbers got worse and started reproducing the bench
    # exactly, which is how the result became trustworthy.
    from torchvision import transforms
    tf = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(distill.PIXEL_MEAN, distill.PIXEL_STD),
    ])
    with torch.no_grad():
        sb = torch.stack([tf(p) for p in pil]).to(device)
        se = net(sb)
        se = (se / se.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32)
    print(f"\nstudent  : epoch {ckpt['epoch']}, "
          f"holdout cos {ckpt['holdout_cosine']:+.4f}")
    # The diagnostic line: on 2026-08-07 this read 0.710/0.700 on the two books
    # against 0.855/0.883 on the empty frames and 0.843 holdout - the student
    # tracking the teacher on the easy frames and coming apart on exactly the
    # ones the question is about. Per class now, since there are six of each.
    ts = np.einsum("ij,ij->i", te, se)
    print("teacher-student cosine: "
          + "  ".join(f"{c} {ts[[i for i, l in enumerate(classes) if l == c]].mean():+.4f}"
                      for c in sorted(set(classes))))

    table("STUDENT cosine (fp32, 128px)", se @ qv.T, labels, PROMPTS)
    summarise("STUDENT", se, qv, PROMPTS, classes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
