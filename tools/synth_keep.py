#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Turn two blind judges' verdicts into the `keep.txt` probe_bisect.py reads.

    uv run --script tools/synth_keep.py bench/stills/20260822-synth-book-crop2

Reads `key.json` (which side the generator put the positive state on, written
by tools/synth_sheet.py) and every `judge-*.json` in the set, and writes
`keep.txt`.

THE CRITERION, IN CODE RATHER THAN IN A README
------------------------------------------------
A pair is kept when EVERY judge, independently and without seeing the key or
any encoder output, got all three of these right:

  side          named the side the key says holds the positive state. This is
                the only one with a wrong answer, which is what makes it a
                measurement rather than an opinion.
  object_both   the object is large enough to read the state on both sides.
  same_scene    the two frames are the same scene, so the pair is an edit and
                not two photographs.

Unanimity rather than a majority because two judges is not a panel: with two,
a majority rule is just "either judge will do", which admits every pair one of
them fumbled. The cost is a smaller set, and the set exists to be trusted.

WHY THE DROPS STAY IN THE FILE
--------------------------------
The dropped stems are written into the header as comments, each with the
criterion it failed, and the PNGs stay in the set. A filter that deletes what
it rejects cannot be argued with later, and this one has already been wrong
once - the first wide book set was read as "the generator ignored the edit"
when the judges had in fact seen the edit on 11 of 12 and the real fault was
the object being too small.

The order matters more than the criterion: this runs on the stimulus, before
anything is encoded. Screening on margins afterwards would be fitting.
"""
import argparse
import json
from pathlib import Path


def verdicts(path: Path) -> dict[str, dict]:
    rows = json.loads(path.read_text())
    return {Path(r["file"]).stem: r for r in rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", type=Path, help="the set directory")
    args = ap.parse_args()

    key = json.loads((args.set / "key.json").read_text())
    judges = sorted(args.set.glob("judge-*.json"))
    if len(judges) < 2:
        raise SystemExit(f"{args.set}: found {len(judges)} judge-*.json, need 2 "
                         "or more - one judge is an opinion")
    seen = [verdicts(j) for j in judges]

    keep, dropped = [], []
    for stem in sorted(key):
        failed = set()
        for v in seen:
            r = v.get(stem)
            if r is None:
                failed.add("missing")
                continue
            if r.get("side") != key[stem]:
                failed.add("side")
            if not r.get("object_both"):
                failed.add("object")
            if not r.get("same_scene"):
                failed.add("scene")
        if failed:
            dropped.append((stem, "+".join(sorted(failed))))
        else:
            keep.append(stem)

    head = [(f"# {len(keep)} of {len(key)} pairs, kept by {len(judges)} blind "
             "judges that agreed on which"),
            ("# side held the positive state and that saw the object, large, in "
             "the same"),
            "# scene on both sides.  See README.md."]
    head.append("# Dropped, with the criterion each failed:" if dropped
                else "# Nothing dropped.")
    head += [f"#   {stem}  {why}" for stem, why in dropped]
    (args.set / "keep.txt").write_text("\n".join(head + keep) + "\n")
    print(f"{args.set.name}: keep {len(keep)}/{len(key)} -> {args.set / 'keep.txt'}")


main()
