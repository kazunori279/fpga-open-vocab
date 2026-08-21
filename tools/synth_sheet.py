#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Blind A|B sheets for screening a `synth_pairs.py` set before measuring it.

    uv run --script tools/synth_sheet.py bench/stills/20260822-synth-book-crop
    # -> /tmp/judge/<set>/<stem>.png, one per scene, plus key.json

Hand the directory of sheets to a judge that has not seen any encoder output,
tell it the two states, and ask it *which side holds the positive one*. Keep a
pair only when the judge names the side correctly, the object is present and
large in both, and the scene is the same. Write the survivors to `keep.txt` in
the set and read the set with `probe_bisect.py --keep`.

WHICH SIDE THE POSITIVE GOES ON, AND WHY IT IS NOT THE OBVIOUS ANSWER
----------------------------------------------------------------------
Positive on the left every time is a free answer, and a judge that has noticed
grades nothing. The first four sets flipped the side with the index instead,
which is not enough: a judge on the second glass set finished its thirty sheets
and volunteered, unprompted, that the sides looked perfectly alternating. It
had not used that - its 14 A's against the key's 15 say it scored the pixels -
but a screen that CAN be answered without looking will eventually be answered
without looking, and the failure is silent: `side` goes to 100% and the filter
keeps passing everything.

So the side is now a hash of the stem. Still deterministic - `hashlib`, not
`hash()`, which is salted per process - so the same set produces the same
sheets and the same key on every machine and a verdict archived beside the
pixels can be re-checked. But it has no run of alternations to spot, and the
positive lands left or right in whatever ratio the filenames happen to give.

The sets shot before this change were keyed by parity. Their `key.json` is
archived in the set and remains the record of what their judges were graded
against; re-running this script over one of them produces a different key, and
the verdicts would have to be re-collected to match.

WHY ASK "WHICH SIDE", RATHER THAN "IS THIS PAIR ANY GOOD"
----------------------------------------------------------
Because the second question has no wrong answer and this one does. A judge told
which side is the positive will confirm it; a judge asked to *find* it fails
loudly on the pairs where the generator changed nothing, swapped the room, or
deleted the object - which on the first four sets was a third to a half of
them. The key is the ground truth, so the screen reports an accuracy rather
than an opinion.

WHY THIS IS NOT CIRCULAR, AND WHERE IT IS BIASED
-------------------------------------------------
The judge sees pixels only, never a margin, and the sheets are built before
anything is encoded. Selecting on whether the *stimulus* realises the contrast
is a validity filter; selecting after seeing the scores would be fitting, and
this ordering is the whole reason the two are not the same thing.

It is still biased in one direction: it keeps the pairs a vision model finds
legible, which is the population a web-trained teacher is best at. So the
teacher's reading on a screened set is inflated by construction. That is the
harmless direction - a pair that fails a screened set will not be rescued by a
camera - but it is not a number to quote.

NOT AN APPLIANCE MEASUREMENT. See `synth_pairs.py` for what a set of twelve
different scenes can and cannot say about a student that is asked about one.
"""

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

CELL = 320   # 128 upscaled nearest; big enough to read a spine, honest about
             # the pixels the encoder actually gets


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: synth_sheet.py SET_DIR [SET_DIR ...]")
    for arg in sys.argv[1:]:
        d = Path(arg)
        pos = sorted((d / "pos").glob("*.png"))
        neg = sorted((d / "neg").glob("*.png"))
        if not pos or len(pos) != len(neg):
            sys.exit(f"{d}: {len(pos)} pos and {len(neg)} neg - not a paired set")
        out = Path("/tmp/judge") / d.name
        out.mkdir(parents=True, exist_ok=True)
        key = {}
        for a, b in zip(pos, neg, strict=True):
            if a.stem != b.stem:
                sys.exit(f"{d}: {a.stem} has no negative twin")
            on_left = hashlib.sha256(a.stem.encode()).digest()[0] % 2 == 0
            left, right = (a, b) if on_left else (b, a)
            key[a.stem] = "A" if on_left else "B"
            im = Image.new("RGB", (2 * CELL + 8, CELL + 18), "white")
            dr = ImageDraw.Draw(im)
            im.paste(Image.open(left).resize((CELL, CELL), Image.NEAREST), (0, 18))
            im.paste(Image.open(right).resize((CELL, CELL), Image.NEAREST), (CELL + 8, 18))
            dr.text((4, 4), "A", fill="black")
            dr.text((CELL + 12, 4), "B", fill="black")
            im.save(out / f"{a.stem}.png")
        (out / "key.json").write_text(json.dumps(key, indent=1) + "\n")
        qf = d / "queries.txt"
        q = qf.read_text().splitlines() if qf.exists() else ["?", "?"]
        print(f"{d.name}: {len(key)} sheets -> {out}")
        print(f"  positive '{q[0]}'   negative '{q[1]}'")
        print(f"  archive {out}/key.json and the judges' verdicts in {d}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
