# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Is the student's open-vs-closed margin bigger than its frame-to-frame noise?

    uv run --script tools/probe_noise.py [--snaps DIR]

tools/probe_open.py prints a margin. A margin means nothing without a scale, so
this runs every snap from the same session through the student and projects onto
the same axes: the two book frames then get compared against the spread of ~90
frames that are mostly an empty wall. A margin inside that spread is not a
signal, it is the noise floor with a label on it.

WHAT IT FOUND, 2026-08-07
-------------------------
93 frames. The open-closed gap is 0.32 sd on spread-front -- inside the noise --
and on the opened-closed axis the open book scores -4.67 sd, the *minimum of all
93 frames*. The distinction is not merely absent; the student ranks it backwards
with conviction, which is what the bench saw as red-for-closed.

AND WHAT THE LOSS FIX DID TO IT, 2026-08-08
-------------------------------------------
Same 93 frames, same axes, three students:

                      opened-closed          spread-front
  train2017 (1-cos)   -4.67 sd  inverted     +0.32 sd  noise
  nce1.0              -2.79 sd  inverted     +0.21 sd  noise
  nce0.3              +1.48 sd               +4.11 sd

nce0.3 is the first student to get the sign right on both, and its spread-front
gap (+0.0648) is within 7% of the teacher's own (+0.0692). Two things stop that
from being a win yet:

  - The separation is one-sided. OPEN sits at -0.67 sd, i.e. unremarkable; it is
    CLOSED at -4.78 sd doing all the work. The student has learned that this
    closed book is odd, which is not the same as binding "opened" to "book" --
    the teacher, by contrast, moves *both* frames (+0.0401 / -0.0291).
  - n = 1 open book and 1 closed book, from one session, one lighting. A 4 sd
    gap on a single pair is a reason to retest on the bench, not a result.

Raw prompt ranking is still wrong at every prompt (CLOSED beats OPEN even on
"an opened book", 0.2837 vs 0.2474). Only the difference axis works, which is
what M12 built difference axes for.

Note nce1.0 beats nce0.3 on every *instance-retrieval* number (top-1 0.369 vs
0.320) and loses on this one. Retrieval rewards telling images apart; this asks
the student to keep the teacher's axis directions, and w=1.0 over-disperses the
cone to 0.2501 against the teacher's 0.7381. The gentler weight is the one to
carry forward.

EVERY STUDENT, RE-SCORED AFTER THE TEACHER FIX, 2026-08-08
-----------------------------------------------------------
  student           teacher            opened-closed   spread-front
  train2017         ViT-B/16              -4.64 sd       -0.22 sd
  nce0.3            ViT-B/16              +0.19          +2.73
  so400m-s30k       SO400M a1,   30k      +3.35          +3.40
  so400m-s30k-a05   SO400M a0.5, 30k      +2.61          +4.94
  so400m-full       SO400M a1,  118k      +1.32          +1.93
  so400m-full-a05   SO400M a0.5, 118k     +5.88          +7.33

The ViT-B/16 rows differ from the table above them because resolve() loads
ViT-B-16-quickgelu and the old path loaded ViT-B-16; nce0.3's spread-front moves
+4.11 -> +2.73 on that alone. The sign and the ordering survive, the magnitudes
do not, which is the size of the quickgelu quirk on this axis.

so400m-full-a05 is the first student to be far outside the noise on both axes,
and it does not pay for it: model/evaluate.py has it at 0.895 mean object AUC
and 91% retention against the shipped student's 0.899 and 94%. Weakening the
centring from alpha 1 to alpha 0.5 is what did it - same teacher, same data,
same recipe, +1.32 -> +5.88 here and 0.856 -> 0.895 there.

WHICH TEACHER'S TEXT, AND A WRONG ANSWER THIS PRINTED FOR AN HOUR
------------------------------------------------------------------
This script used to encode the prompts with teacher.load_clip() - ViT-B/16,
unconditionally. That is right for every student in the table above and silently
wrong for one distilled from tools/teacher_swap.py: those emit vectors in an
SO400M-PCA space, which is also 512 wide, so the dot product succeeds and the
numbers mean nothing. It scored so400m-full-a05 at -2.39 sd on opened-closed
while tools/probe_inherit.py, which resolves the teacher, scored the same
checkpoint at +5.68. Nothing raised an error; only the disagreement did.

It now uses probe_inherit.resolve(), the same three-strings-from-the-checkpoint
rule model/evaluate.py's teacher_bundle() uses.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import distill
import numpy as np
import student as student_mod
import teacher as teacher_mod
import torch
from PIL import Image
from probe_inherit import resolve
from torchvision import transforms

PROMPTS = ["an opened book", "a closed book",
           "two pages of an open book", "the front cover of a closed book",
           "a book"]
AXES = [("opened-closed", 0, 1), ("spread-front", 2, 3)]
MARKED = {"m9-54-f0550-hi.png": "OPEN", "m9-56-f0570-hi.png": "CLOSED"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snaps", type=Path, default=Path("/tmp/snaps"),
                    help="where host/cam.py wrote the PNGs (default /tmp/snaps)")
    # The 93-frame sd is the only real noise floor this project has, so a new
    # checkpoint has to be scored against it by the same code that condemned the
    # shipped one -- probe_open.py's two book-free frames are a span, not a sd.
    ap.add_argument("--run", default="train2017",
                    help="student checkpoint under model/runs/ (default: the "
                         "shipped M9 student, whose numbers are in the docstring)")
    ap.add_argument("--teacher", default=None, help="open_clip MODEL:PRETRAINED override")
    ap.add_argument("--basis", type=Path, default=None, help=".npz with mu,w override")
    args = ap.parse_args()

    device = teacher_mod.pick_device()
    ckpt = torch.load(ROOT / "model/runs" / args.run / "student.pt",
                      map_location="cpu", weights_only=False)
    spec, basis_path = resolve(ckpt.get("teacher", ""), args.teacher, args.basis)

    # TEMPLATES-ensembled, unlike probe_inherit.py's bare prompts: this is the
    # deployed form, what host/demo.py sends. The two disagree on some students
    # and probe_inherit.py's docstring says why - read both.
    import open_clip
    name, pre = spec.split(":")
    model, _, _ = open_clip.create_model_and_transforms(name, pretrained=pre)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(name)
    out = []
    with torch.no_grad():
        for q in PROMPTS:
            v = model.encode_text(
                tok([t.format(q) for t in teacher_mod.TEMPLATES]).to(device)).float()
            v = (v / v.norm(dim=-1, keepdim=True)).mean(dim=0)
            out.append((v / v.norm()).cpu().numpy())
    del model
    qv = np.stack(out).astype(np.float32)
    if basis_path:
        b = np.load(basis_path)
        p = (qv - b["mu"]) @ b["w"]
        qv = (p / np.linalg.norm(p, axis=-1, keepdims=True)).astype(np.float32)
    print(f"teacher  : {spec}"
          + (f"  +  {basis_path.name}" if basis_path else "  (already 512)"))

    net = student_mod.Student()
    net.load_state_dict(ckpt["state_dict"])
    net = net.to(device).eval()
    # Plain normalize, not camera_transform() -- see probe_open.py's note.
    tf = transforms.Compose([transforms.ToTensor(),
                             transforms.Normalize(distill.PIXEL_MEAN,
                                                  distill.PIXEL_STD)])

    files = sorted(p for p in args.snaps.glob("*-hi.png"))
    if not files:
        raise SystemExit(f"{args.snaps}: no *-hi.png. Point --snaps at a "
                         f"host/cam.py output directory.")
    pil = [Image.open(p).convert("RGB") for p in files]
    with torch.no_grad():
        e = net(torch.stack([tf(p) for p in pil]).to(device))
        e = (e / e.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32)
    print(f"frames   : {len(files)}   student {args.run}, epoch {ckpt['epoch']}")

    for name, pi, ni in AXES:
        d = qv[pi] - qv[ni]
        d = d / np.linalg.norm(d)
        v = e @ d
        lo, hi = np.percentile(v, [5, 95])
        print(f"\n{name}: mean {v.mean():+.4f}  sd {v.std():.4f}  "
              f"5-95% [{lo:+.4f}, {hi:+.4f}]  range [{v.min():+.4f}, {v.max():+.4f}]")
        marks = {}
        for f, val in zip(files, v, strict=False):
            if f.name in MARKED:
                marks[MARKED[f.name]] = val
                print(f"    {MARKED[f.name]:7} {val:+.4f}   "
                      f"(z vs all frames {(val - v.mean()) / v.std():+.2f})")
        if len(marks) == 2:
            gap = marks["OPEN"] - marks["CLOSED"]
            print(f"    OPEN-CLOSED gap {gap:+.4f} = {gap / v.std():+.2f} sd "
                  f"-- wanted positive and well outside the spread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
