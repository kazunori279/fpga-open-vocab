# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "transformers", "sentencepiece"]
# ///
"""The same three gates as tools/probe_gemini.py, for any open_clip teacher.

    uv run --script tools/probe_teacher.py [MODEL:PRETRAINED ...] [--snaps DIR]

Defaults to the incumbent (ViT-B-16/openai) plus SigLIP 2 base, so one run
reproduces the known failure and tests the candidate against it.

WHY A SEPARATE SCRIPT FROM probe_open.py
----------------------------------------
probe_open.py answers "does *this* stack separate the two frames", teacher and
student together, and its verdict is written into its own docstring. This one
answers "would this model do as a *replacement* teacher", which is a different
question with a pass/fail shape - so it borrows probe_gemini.py's three gates
verbatim rather than probe_open.py's tables. Same four frames, same eight
prompts, same three axes as both, because a number that cannot be compared to
the CLIP baseline is not worth the download.

THE GATES, and what the incumbent scores on them
------------------------------------------------
  1. CONTROL - the empty-desk frame must rank the empty-desk prompt top. Guards
     against reading meaning into a miswired call.
  2. RANKING - "an opened book" picks OPEN *and* "a closed book" picks CLOSED.
     CLIP fails the second: it scores "a closed book" higher on the OPEN image
     (0.2898 vs 0.2743).
  3. AXIS - projected onto normalize(e_open - e_closed), OPEN must clear CLOSED
     by more than the two book-free frames span.

Plus the bag-of-words tell: how many of the eight prompts the OPEN image wins.
CLIP wins 7 of 8 - a model that ranks by "is a book present" and ignores the
adjective takes nearly all of them. gemini-embedding-2 takes 4 of 8 and passes
all three gates, which is what makes replacing the teacher worth the trouble;
it is also under Vertex terms that forbid distilling it, which is what makes an
openly-licensed model that scores the same the actual prize.

DIMENSION IS NOT A DETAIL
-------------------------
The board's query record and the student's head are 512 wide. CLIP ViT-B/16 is
512, SigLIP 2 base is 768, SO400M is 1152. A wider teacher means either a wider
student head and a wider per-frame transfer, or a projection of the joint space
down to 512 applied to *both* sides. So the width is printed with the verdict.

WHAT IT FOUND, 2026-08-08
-------------------------
Attribute binding arrives with scale, and it arrives late:

  model                       dim   gate 2   OPEN wins
  ViT-B-16-quickgelu/openai   512   FAIL     7 of 8
  ViT-B-16-SigLIP2            768   FAIL     6 of 8
  ViT-L-16-SigLIP2-256       1024   FAIL     6 of 8
  ViT-SO400M-14-SigLIP2      1152   PASS     4 of 8
  gemini-embedding-2          512   PASS     4 of 8   (API, not distillable)

Every model passes gate 1 and gate 3; gate 2 - "a closed book" having to pick
the CLOSED image - is the only one that discriminates, which is why a probe
that reported axis separation alone would have called SigLIP 2 base a success.
Its spread-front gap is 0.1349 against a 0.0344 noise span, cleaner than
anything CLIP produces, while it still ranks the closed-book prompt onto the
open book. Separation on a hand-built axis is not the same as binding.

SO400M reproduces gemini-embedding-2's 4-of-8 exactly, and it is Apache-2.0
from Google Research via timm, so unlike the API it can be distilled from. The
1152-wide output is the open cost: either the student head and the per-frame
transfer grow 2.25x, or the joint space is projected to 512 by a map applied to
image and text alike - untested, and the next thing to measure.

A caveat this table cannot escape: one open book and one closed book. It ranks
candidates and rules out the small ones; it does not measure how well the
winner binds in general.

THE BASELINE MUST BE LOADED CORRECTLY OR THE WHOLE TABLE IS PROPAGANDA
----------------------------------------------------------------------
First run of this script paired "ViT-B-16" with the openai weights, which
open_clip accepts with only a warning about a QuickGELU mismatch, and CLIP
scored 5 of 8. With "-quickgelu" it scores 7 of 8 - matching the figure
probe_open.py recorded independently. The bug flattered every candidate.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import open_clip
import torch
from PIL import Image

# "-quickgelu", not plain "ViT-B-16". The OpenAI weights were trained with
# QuickGELU and open_clip's plain ViT-B-16 config uses nn.GELU, so pairing them
# loads the weights into the wrong activation and quietly degrades the baseline
# - it only warns. Getting this wrong would stack the comparison against the
# incumbent, which is the one direction of error this script must not have.
DEFAULT_MODELS = ["ViT-B-16-quickgelu:openai", "ViT-B-16-SigLIP2:webli"]

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
CONTROL = "an empty wooden desk"
AXES = [
    ("opened-closed", "an opened book", "a closed book"),
    ("pages-cover", "a page of printed text", "a book cover"),
    ("spread-front", "two pages of an open book", "the front cover of a closed book"),
]


def pick_device():
    """MPS counts. The first run of this script tested cuda only and fell to
    CPU on an Apple Silicon machine, which does not change any number here -
    four images - but makes tools/probe_project.py's few-thousand-image bank
    the difference between minutes and an afternoon."""
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def gates(iv, tv, header):
    """Score the three gates on already-encoded, already-normalized vectors.

    Split out from run() so tools/probe_project.py can put the *same* judgement
    on vectors that have been through a 1152->512 projection. Two copies of this
    would drift, and the only reason the projection result means anything is
    that it is compared against the unprojected one by an identical rule.

    Row order of `iv` must be IMAGES; row order of `tv` must be PROMPTS+[CONTROL].
    """
    allp = PROMPTS + [CONTROL]
    cos = iv @ tv.T
    print(f"\n{'='*78}\n{header}")

    ci = len(allp) - 1
    best = int(np.argmax(cos[3]))
    g1 = best == ci
    print(f"  1 CONTROL : empty frame's top prompt is '{allp[best]}' "
          f"({cos[3, best]:+.4f}){'' if g1 else '  <- not the desk prompt'}"
          f"   {'PASS' if g1 else 'FAIL - read nothing below'}")

    g2 = True
    for p in ("an opened book", "a closed book"):
        j = allp.index(p)
        want = "OPEN" if "opened" in p else "CLOSED"
        got = "OPEN" if cos[0, j] > cos[1, j] else "CLOSED"
        g2 &= got == want
        print(f"  2 RANKING : '{p:16}' picks {got:6} "
              f"(OPEN {cos[0, j]:+.4f} / CLOSED {cos[1, j]:+.4f}) want {want:6} "
              f"  {'ok' if got == want else 'WRONG'}")

    g3 = True
    for axname, pos, neg in AXES:
        d = tv[allp.index(pos)] - tv[allp.index(neg)]
        d = d / np.linalg.norm(d)
        v = iv @ d
        gap, span = v[0] - v[1], abs(v[2] - v[3])
        ok = gap > span
        g3 &= ok
        print(f"  3 AXIS    : {axname:14} OPEN {v[0]:+.4f} CLOSED {v[1]:+.4f}"
              f"  gap {gap:+.4f} vs non-book span {span:.4f}   "
              f"{'clears' if ok else 'under noise'}")

    wins = int((cos[0, :len(PROMPTS)] > cos[1, :len(PROMPTS)]).sum())
    print(f"    bag-of-words tell : OPEN wins {wins} of {len(PROMPTS)} prompts "
          f"(CLIP 7/8, gemini-embedding-2 4/8)")
    print(f"    VERDICT : {'PASS all three gates' if (g1 and g2 and g3) else 'fails a gate'}")
    return g1 and g2 and g3


def encode(spec, snaps, device):
    """Load a model and return (image_vectors, text_vectors), both normalized."""
    name, pretrained = spec.split(":", 1)
    model, _, preprocess = open_clip.create_model_and_transforms(
        name, pretrained=pretrained, device=device)
    model.eval()
    tok = open_clip.get_tokenizer(name)

    # Bare prompts, no template ensemble. probe_gemini.py found that dressing
    # the text up ("task: search result | query: ...") inverted the closed-book
    # ranking there, so the comparison is kept to the plainest form on both
    # sides rather than giving one model a prompt-engineering advantage.
    allp = PROMPTS + [CONTROL]
    with torch.no_grad():
        img = torch.stack([preprocess(Image.open(snaps / n).convert("RGB"))
                           for _, n in IMAGES]).to(device)
        iv = model.encode_image(img).float()
        tv = model.encode_text(tok(allp).to(device)).float()
    iv = (iv / iv.norm(dim=-1, keepdim=True)).cpu().numpy()
    tv = (tv / tv.norm(dim=-1, keepdim=True)).cpu().numpy()
    return iv, tv


def run(spec, snaps, device):
    iv, tv = encode(spec, snaps, device)
    header = (f"{spec}   embed dim {iv.shape[1]}"
              + ("  (matches the board's 512-wide query record)" if iv.shape[1] == 512
                 else "  (board is 512 - this needs a wider head or a projection)"))
    return gates(iv, tv, header)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--snaps", type=Path, default=Path("/tmp/snaps"))
    args = ap.parse_args()

    device = pick_device()
    missing = [n for _, n in IMAGES if not (args.snaps / n).exists()]
    if missing:
        print(f"missing frames in {args.snaps}: {', '.join(missing)}")
        return 1
    print(f"frames : {args.snaps}   device : {device}")

    for spec in (args.models or DEFAULT_MODELS):
        try:
            run(spec, args.snaps, device)
        except Exception as e:  # noqa: BLE001  - a bad spec should not lose the
            # other results, and open_clip raises anything at all on one
            print(f"\n{'='*78}\n{spec}   FAILED TO RUN: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
