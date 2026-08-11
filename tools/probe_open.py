# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch"]
# ///
"""Can anything in this stack tell an opened book from a closed one?

    uv run --script tools/probe_open.py [--snaps DIR]

Runs on the PNGs host/cam.py already wrote, so it needs no board and no hands.
Three questions, in the order that makes a negative answer informative:

  1. Does the *teacher* (ViT-B/16, fp32, 224px) separate them? If not, nothing
     downstream can, and the fix is the prompts, not the hardware.
  2. Does a difference vector separate them better than ranking the two prompts
     independently? normalize(e_open - e_closed) cancels the "book" component
     that dominates both and is what the board is actually drowning in.
  3. Does the *student* (fp32, 128px) keep whatever the teacher had?

WHAT IT FOUND, 2026-08-07, and the reason M12 exists
----------------------------------------------------
Yes, no, and no. On the spread-front axis the teacher puts the open book at
+0.0401 and the closed one at -0.0291; the student puts them at +0.0065 and
+0.0052, a gap of 0.32 sd against the frame-to-frame spread that
probe_noise.py measures. On the opened-closed axis the student is
anti-correlated at -4.67 sd.

Both models were fed the *same* 128x128 board PNG (the teacher's preprocess
upscales it to 224), so the input information is identical and the loss is
distillation capacity. Not resolution, not the prompts, not int8, not the
link, not the fabric -- this reproduces the bench inversion with no board in
the room, which is what makes it a proof rather than a suspicion.

The margins this script prints have no scale -- its two book-free frames are a
span, not a standard deviation. Read them next to tools/probe_noise.py --run
NAME, which projects all 93 frames onto the same axes and is where the
2026-08-08 loss-fix comparison of the three students is recorded.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))

import numpy as np
import torch
from PIL import Image

import teacher as teacher_mod
import student as student_mod
import distill

# The two labelled frames from the 2026-08-07 bench run, plus a close-up and an
# empty frame for scale. host/cam.py names these <tag>-<block>-f<frame>-hi.png;
# the frame number is the marker demo.py --snap-every printed, so it is one
# behind the image (m9.c:770 dumps the *next* frame).
IMAGES = [
    ("OPEN  ", "m9-54-f0550-hi.png"),
    ("CLOSED", "m9-56-f0570-hi.png"),
    ("close ", "m9-57-f0580-hi.png"),
    ("empty ", "m9-58-f0590-hi.png"),
]
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


def table(title, cos, labels, prompts):
    print(f"\n=== {title} ===")
    print(f"{'':10}" + "".join(f"{p[:17]:>19}" for p in prompts))
    for i, lab in enumerate(labels):
        print(f"{lab:10}" + "".join(f"{cos[i, j]:>19.4f}" for j in range(len(prompts))))


def axes(title, img, qv, prompts):
    print(f"\n--- {title}: projection onto normalize(pos - neg) ---")
    idx = {p: i for i, p in enumerate(prompts)}
    for name, pos, neg in AXES:
        d = qv[idx[pos]] - qv[idx[neg]]
        d = d / np.linalg.norm(d)
        vals = img @ d
        cells = "  ".join(f"{lab.strip()} {v:+.4f}" for (lab, _), v in zip(IMAGES, vals))
        print(f"  {name:16} {cells}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snaps", type=Path, default=Path("/tmp/snaps"),
                    help="where host/cam.py wrote the PNGs (default /tmp/snaps, "
                         "which is cam.py's --out default plus a subdirectory)")
    # The whole point of the loss work is to change the third answer below, and
    # the only honest way to see whether it did is to put a new checkpoint
    # through the identical script that recorded the failure.
    ap.add_argument("--run", default="train2017",
                    help="student checkpoint under model/runs/ (default: the "
                         "shipped M9 student, whose numbers are in the docstring)")
    args = ap.parse_args()
    missing = [f for _, f in IMAGES if not (args.snaps / f).exists()]
    if missing:
        raise SystemExit(f"{args.snaps}: missing {', '.join(missing)}. These are "
                         f"the labelled frames from one specific bench run; point "
                         f"--snaps at that run's output, or edit IMAGES.")

    device = teacher_mod.pick_device()
    print(f"device   : {device}")

    model, preprocess = teacher_mod.load_clip(device)
    qv = teacher_mod.encode_queries(model, PROMPTS, device).astype(np.float32)
    print(f"teacher  : {teacher_mod.tag()}, {len(teacher_mod.TEMPLATES)} templates")

    pil = [Image.open(args.snaps / f).convert("RGB") for _, f in IMAGES]
    with torch.no_grad():
        batch = torch.stack([preprocess(p) for p in pil]).to(device)
        te = model.encode_image(batch)
        te = (te / te.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32)

    labels = [lab for lab, _ in IMAGES]
    table("TEACHER cosine", te @ qv.T, labels, PROMPTS)
    axes("TEACHER", te, qv, PROMPTS)

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
    print(f"\nstudent  : epoch {ckpt['epoch']}, holdout cos {ckpt['holdout_cosine']:+.4f}")
    # The diagnostic line: 0.710/0.700 on the two books against 0.855/0.883 on
    # the empty frames and 0.843 holdout. The student tracks the teacher on the
    # easy frames and comes apart on exactly the ones the question is about.
    print(f"teacher-student cosine per image: "
          + "  ".join(f"{lab.strip()} {float(a @ b):+.4f}"
                      for (lab, _), a, b in zip(IMAGES, te, se)))

    table("STUDENT cosine (fp32, 128px)", se @ qv.T, labels, PROMPTS)
    axes("STUDENT", se, qv, PROMPTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
