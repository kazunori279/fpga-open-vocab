#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""How often does a contrast appear in the distillation set's captions?

    uv run --script tools/probe_captions.py
    uv run --script tools/probe_captions.py "book/open(ed)?/closed|shut"

Each argument is `OBJECT/POSITIVE/NEGATIVE`, three regexes, matched
case-insensitively against `captions_train2017.json`. An image counts for a side
if any one of its five captions matches the object *and* that side's state. With
no arguments the built-in list is the pairs this project has actually benched.

WHAT THIS IS FOR
----------------
Cheap input to the envelope map in
[#23](https://github.com/kazunori279/fpga-open-vocab/issues/23): before staging a
pair and shooting stills for it, it costs two seconds to ask whether the
distillation set has ever seen the contrast.

WHAT IT IS NOT, AND THIS MATTERS MORE THAN WHAT IT IS
-----------------------------------------------------
**The student never sees a caption.** `model/distill.py` regresses the student's
vector onto the *teacher's image embedding*; the targets are
`emb_train2017_...npy` and the text tower is not in the loop. So this counts a
proxy - what a caption writer thought worth mentioning - and not the training
signal.

The proxy is a bad one in a specific, asymmetric way: a state gets written down
only when it is salient. A closed book appears in an enormous number of COCO
images (every bookshelf) and is described as *closed* five times in the whole
split. So a low count here is evidence of nothing on its own.

**And the one time it was checked against an outcome, it pointed the wrong way.**
The student inherits the book axis (8.2 sd) off 5 captioned negatives and loses
the glass one (0.2 sd) off 288 - 58x more. That is
[#28](https://github.com/kazunori279/fpga-open-vocab/issues/28)'s first ruled-out
candidate, and it is the reason this file exists as a screening aid rather than a
predictor. Never skip a pair on a low count alone.
"""

import argparse
import json
import re
import sys
from pathlib import Path

CAPTIONS = Path("model/data/annotations/captions_train2017.json")

# The pairs this project has benched or bisected, so the default output is
# directly comparable to a result that exists.
DEFAULT = [
    "book/open(ed)?/closed|shut",
    "(glass|cup|mug|bottle|bowl|jar|pitcher|vase|can)/full|filled/empty",
    "laptop/open(ed)?/closed|shut",
    "(cube|box|block)/red/blue",
    "(bag|suitcase|backpack)/big|large/small|little",
    "(person|man|woman|people)/standing/sitting|seated",
]


def load(path: Path) -> dict[int, list[str]]:
    """image_id -> its captions, lowercased."""
    if not path.exists():
        sys.exit(f"no captions at {path} - run from the repo root, and see "
                 f"docs/building.md for the COCO download")
    by_image: dict[int, list[str]] = {}
    for a in json.load(path.open())["annotations"]:
        by_image.setdefault(a["image_id"], []).append(a["caption"].lower())
    return by_image


def count(by_image: dict[int, list[str]], obj: re.Pattern, state: re.Pattern) -> int:
    """Images with at least one caption naming both the object and the state.

    Both in the *same* caption, deliberately. "A book on a table" and "a closed
    laptop" are two captions of one image and do not make it an image of a
    closed book.
    """
    return sum(any(obj.search(c) and state.search(c) for c in caps)
               for caps in by_image.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pairs", nargs="*", default=None, metavar="OBJ/POS/NEG")
    ap.add_argument("--captions", type=Path, default=CAPTIONS)
    args = ap.parse_args()

    by_image = load(args.captions)
    n = len(by_image)
    print(f"captions  : {args.captions}")
    print(f"images    : {n}\n")
    print(f"  {'object':34s} {'positive':>10s} {'negative':>10s}   {'ratio':>6s}")

    for spec in (args.pairs or DEFAULT):
        parts = spec.split("/")
        if len(parts) != 3:
            sys.exit(f"want OBJECT/POSITIVE/NEGATIVE, got {spec!r}")
        obj, pos, neg = (re.compile(p, re.IGNORECASE) for p in parts)
        cp, cn = count(by_image, obj, pos), count(by_image, obj, neg)
        # The ratio is the interesting column: a contrast the set only ever
        # shows one way round is a different situation from a rare one.
        ratio = f"{cp / cn:6.1f}" if cn else "   inf"
        print(f"  {parts[0][:34]:34s} {cp:6d} {100*cp/n:5.2f}% "
              f"{cn:6d} {100*cn/n:5.2f}%   {ratio}")

    print("\n  Counts are captions, not pixels, and the student is trained on "
          "pixels.\n  Read the docstring before using a low count as a reason "
          "not to shoot a pair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
