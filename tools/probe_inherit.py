# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""Does the student inherit its teacher's gates, or only its teacher's cone?

    uv run --script tools/probe_inherit.py --run nce0.3
    uv run --script tools/probe_inherit.py --run so400m-s30k

THE QUESTION
------------
tools/probe_teacher.py judges *teachers* by three gates and found the incumbent
fails gate 2: CLIP ViT-B/16 scores "a closed book" higher on the OPEN image.
SigLIP 2 SO400M passes. tools/probe_project.py showed a joint PCA to 512 keeps
that pass.

Both of those are properties of a 400M-parameter teacher. This board runs a
few-million-parameter student on 128x128 frames. Whether gate 2 survives
distillation is a separate fact, and the only one that decides anything.

So this puts the *student* through probe_teacher.gates() - the identical
function, not a reimplementation - against text vectors from its own teacher.
A student distilled from SO400M must be compared to SO400M-encoded, PCA-
projected prompts; comparing it to ViT-B/16 prompts would be a dot product
between two unrelated spaces and would fail for reasons that have nothing to do
with the question.

WHICH TEACHER, AND WHY IT IS NOT A FLAG BY DEFAULT
--------------------------------------------------
model/distill.py writes the teacher's name into the checkpoint, so the pairing
travels with the weights. For a swapped run that name is the stem of the target
file, and tools/teacher_swap.py wrote a sidecar .json (the open_clip spec) and
a .basis.npz (the frozen projection) beside it. All three are found from the
one string. --teacher and --basis override, for a checkpoint predating this.

WHAT IT PRINTS
--------------
  gates    the three gates, teacher then student, same rule for both. Gate 2 is
           the only one that has ever discriminated.
  sd       the two difference axes over all 93 bench frames, which is the only
           real noise floor this project has. A margin without it is a number
           without a scale - see tools/probe_noise.py.

BARE PROMPTS HERE, ENSEMBLED PROMPTS IN probe_noise.py, AND THE GAP BETWEEN THEM
--------------------------------------------------------------------------------
This file inherits probe_teacher.py's bare prompts so every model is judged the
way the teacher table judged them. tools/probe_noise.py and host/demo.py use
teacher.TEMPLATES, a 7-way ensemble, which is what the board actually ships.

On nce0.3 the choice matters, and it matters on exactly one axis:

                  ensembled (deployed)   bare (comparable)
  spread-front         +4.11 sd              +3.62 sd
  opened-closed        +1.48 sd              -2.25 sd   inverted

So spread-front is a property of the student and opened-closed was a property of
the prompt ensemble. Any claim resting on opened-closed needs both numbers next
to it. Read the deployed figure for "will the board do it" and this one for
"is it the model".

nce0.3 also wins 0 of 8 prompts for OPEN, against CLIP's 7 of 8. It has not
fixed its teacher's bag-of-words bias so much as reversed it: it now prefers the
CLOSED frame for every prompt, "an opened book" included.

WHAT IT FOUND, 2026-08-08: GATE 2 SURVIVES DISTILLATION
--------------------------------------------------------
Two students, 30000 images, 20 epochs, batch 512, lr 6e-3, --infonce 0.3. The
only difference is the teacher:

  student              teacher                gate 2   opened-closed  spread-front
  _sieve_infonce-0.3   ViT-B/16               FAIL      +0.56 sd       +0.32 sd
  so400m-s30k          SO400M + joint PCA-512 PASS      +3.80 sd       +2.71 sd

The SO400M student is the first to rank both prompts correctly - "an opened
book" picks OPEN *and* "a closed book" picks CLOSED. Its teacher passes gate 2
and the control's teacher does not, and nothing else differs, so the property
came down the distillation.

It also arrives for the right reason. nce0.3's apparent spread-front win was
CLOSED sitting at -5.14 sd with OPEN unremarkable - the student had learned that
one book was odd. Here OPEN is at +3.42 sd and CLOSED near the mean, which is
the shape the teacher has.

The printed VERDICT still says "fails a gate": gate 3 compares the margin to the
span of two book-free frames, and for this student that two-point span (0.0295-
0.0590) is wider than the 93-frame sd. Two points are a poor scale estimate in
both directions. The sd block below the gates is the number to read.

The cost is real and is in tools/probe_retention.py: object AUC 0.855 -> 0.800.

AND WHAT THE SHIPPED STUDENT DID, 2026-08-10: GATE 2 DID NOT REPRODUCE
------------------------------------------------------------------------
so400m-full-a05 is the student M18 flashes - same teacher as so400m-s30k, four
times the data, alpha 0.5. It does *not* inherit the gate-2 pass:

  'an opened book'  OPEN -0.1466 / CLOSED -0.1457   WRONG by 0.0009
  'a closed book'   OPEN -0.1330 / CLOSED -0.0981   ok

0.0009 is 0.09 sd against the 93-frame noise floor below, so this is a tie the
gate has to break one way and breaks against us. It wins 1 of 8 prompts, worse
than s30k. Read the gate as "no bare-prompt claim", not as a regression to
ViT-B/16's -4.64: the sign is not the same kind of wrong.

The axes go the other way, and they go a long way:

  student           opened-closed   spread-front
  so400m-s30k          +3.80 sd        +2.71 sd
  so400m-full-a05      +5.68           +7.14

Both frames move (OPEN +3.33 sd, CLOSED -2.35 sd), which is the teacher's shape
and not the one-frame-is-odd artifact probe_noise.py caught on nce0.3.

WHICH NUMBER DECIDES. The board does not send bare prompts. host/demo.py
ensembles seven templates and M12's contrast query sends
normalize(e_pos - mean(e_neg)) - a difference axis, which is the row above and
not the gate. On the deployed path probe_noise.py has this student at +5.88 and
+7.33 sd, the best of any student on both axes at once. So the swap is still
carried by evidence; the evidence is just the axis and not the gate, and saying
otherwise would be quoting the measurement that did not happen.
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

import open_clip

import probe_teacher as pt
import student as student_mod
import distill

CACHE = ROOT / "model" / "cache"
# The two axes tools/probe_noise.py scores, by their index into pt.PROMPTS.
SD_AXES = [("opened-closed", "an opened book", "a closed book"),
           ("spread-front", "two pages of an open book",
            "the front cover of a closed book")]

# resolve() started here and moved to model/spaces.py when model/export.py and
# host/demo.py needed the same lookup - see that module for why one copy matters
# more than usual. Re-exported so --teacher / --basis below read unchanged.
from spaces import resolve  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="student under model/runs/")
    ap.add_argument("--snaps", type=Path, default=Path("/tmp/snaps"))
    ap.add_argument("--teacher", default=None, help="open_clip MODEL:PRETRAINED override")
    ap.add_argument("--basis", type=Path, default=None, help=".npz with mu,w override")
    args = ap.parse_args()

    ckpt = torch.load(ROOT / "model/runs" / args.run / "student.pt",
                      map_location="cpu", weights_only=False)
    spec, basis_path = resolve(ckpt.get("teacher", ""), args.teacher, args.basis)
    name, pre = spec.split(":")
    device = pt.pick_device()
    print(f"student  : {args.run}, epoch {ckpt['epoch']}, "
          f"teacher tag '{ckpt.get('teacher', '?')}'")
    print(f"teacher  : {spec}")
    print(f"basis    : {basis_path.name if basis_path else 'none (teacher is already 512)'}")
    print(f"device   : {device}")

    mu = w = None
    if basis_path:
        b = np.load(basis_path)
        mu, w = b["mu"], b["w"]

    def proj(v):
        v = v / np.linalg.norm(v, axis=-1, keepdims=True)
        if w is None:
            return v
        p = (v - mu) @ w
        return p / np.linalg.norm(p, axis=-1, keepdims=True)

    model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pre)
    model = model.to(device).eval()
    tok = open_clip.get_tokenizer(name)

    allp = pt.PROMPTS + [pt.CONTROL]
    with torch.no_grad():
        tv = model.encode_text(tok(allp).to(device)).float().cpu().numpy()
    tv = proj(tv)

    missing = [f for _, f in pt.IMAGES if not (args.snaps / f).exists()]
    if missing:
        raise SystemExit(f"{args.snaps}: missing {', '.join(missing)}")
    pil = [Image.open(args.snaps / f).convert("RGB") for _, f in pt.IMAGES]
    with torch.no_grad():
        iv = model.encode_image(
            torch.stack([preprocess(p) for p in pil]).to(device)).float().cpu().numpy()
    iv = proj(iv)
    del model
    pt.gates(iv, tv, f"TEACHER  {spec}"
                     + (f"  +  {basis_path.name}" if basis_path else ""))

    net = student_mod.Student()
    net.load_state_dict(ckpt["state_dict"])
    net = net.to(device).eval()
    # Plain normalize, not camera_transform() -- these PNGs already came out of
    # the Mega at 128x128, so simulating the crop would apply it twice. See the
    # note in probe_open.py, where doing it wrong made a student look better.
    tf = transforms.Compose([transforms.ToTensor(),
                             transforms.Normalize(distill.PIXEL_MEAN, distill.PIXEL_STD)])

    def student_embed(images):
        with torch.no_grad():
            e = net(torch.stack([tf(p) for p in images]).to(device))
        e = e / e.norm(dim=-1, keepdim=True)
        return e.cpu().numpy().astype(np.float32)

    # The student's outputs live in the teacher's *projected* space already --
    # that is what it was trained to emit -- so they are compared to tv as-is.
    sv = student_embed(pil)
    pt.gates(sv, tv, f"STUDENT  {args.run}")

    files = sorted(p for p in args.snaps.glob("*-hi.png"))
    ev = student_embed([Image.open(p).convert("RGB") for p in files])
    marked = {"m9-54-f0550-hi.png": 0, "m9-56-f0570-hi.png": 1}
    print(f"\n{'='*78}\nSTUDENT axes over all {len(files)} bench frames "
          f"(the only real noise floor)")
    for axname, pos, neg in SD_AXES:
        d = tv[allp.index(pos)] - tv[allp.index(neg)]
        d = d / np.linalg.norm(d)
        v, hits = ev @ d, {}
        for f, val in zip(files, v):
            if f.name in marked:
                hits[marked[f.name]] = val
        gap = hits[0] - hits[1]
        print(f"  {axname:14} OPEN {hits[0]:+.4f} ({(hits[0]-v.mean())/v.std():+.2f} sd)"
              f"   CLOSED {hits[1]:+.4f} ({(hits[1]-v.mean())/v.std():+.2f} sd)"
              f"   gap {gap:+.4f} = {gap / v.std():+.2f} sd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
