#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Can the teacher tell your two states apart? A NO-GO screen, before a bench.

    uv run --script tools/fit_check.py --cat person \
        --pos "an open hand" --neg "a fist" \
        --pos-edit "the hand is open, fingers spread flat" \
        --neg-edit "the hand is closed into a tight fist"

    uv run --script tools/fit_check.py --set bench/stills/20260822-synth-oven-crop

Generates a contrast set out of val2017 photographs, runs the unquantised
SigLIP 2 SO400M teacher over it, and prints GO or NO-GO. Costs a few minutes and
no hardware. `docs/fit.md` is the long version of when to run this and what to
do with the answer.

IT CAN ONLY SAY NO, AND THAT IS THE WHOLE DESIGN
--------------------------------------------------
The number this prints is a teacher's pooled cross-scene AUC on GENERATED
scenes, and generated scenes are dirty: the editor leaves seams, resolution
mismatches and objects it repainted for no reason, and every one of those is a
cue an encoder can win on without ever seeing the state. `bench/stills/README.md`
lists what the judges found on ten of these sets - twenty-one of twenty-nine
sheets carried some shortcut on the oven set alone.

That makes the reading one-sided:

  LOW is trustworthy.  A dirty set can only inflate. If the full teacher tower,
                       before any of this project's compression, cannot separate
                       your two states even with the artefacts helping, then
                       nothing downstream recovers it. Stop, and save a morning
                       of daylight.

  HIGH means nothing.  It could be the state or it could be the seam. You have
                       not learned that the appliance will work; you have only
                       failed to learn that it will not.

So there is no blind judging step here, and no `keep.txt`. Those exist to make a
set safe for RANKING CHECKPOINTS against each other, which is a two-sided
question and needs the artefacts screened out. A rejection gate does not: an
inflated number cannot cause a false NO-GO, only a false GO, and a false GO
costs exactly the bench you were going to run anyway.

WHY IT REPORTS ONLY THE TEACHER
---------------------------------
`probe_bisect.py` will happily run the student too, and the student's number on
a generated set does not predict the appliance in either direction - it reads
HIGHER here than on the one pair the board carries perfectly, which is the
ordering upside down. `bench/stills/20260822-synth-book-crop/README.md` is where
that was caught. Passing `--no-student` is therefore not a speed optimisation,
it is the correctness of the tool: a student column on this page would be read.

THE THRESHOLD, AND WHY IT IS A BAND AND NOT A CONSTANT
--------------------------------------------------------
0.75 is where the ten measured contrasts separate into ones worth benching and
ones that are not, and it is a reading of that table rather than a fitted
number. The teacher spans .579 (a toilet lid) to .933 (tea in a glass) across
them, and the two below .75 are the two where the student came back BELOW
chance - an axis pointing backwards, not a weak one. Do not sharpen this into a
decision constant. Between about .70 and .80 the honest answer is "the screen
did not settle it", which is what the tool prints.

WORDING FAILS BEFORE THIS SCREEN DOES
---------------------------------------
If one side of your contrast is an absence - "a closed hand", "an empty X", "no
person" - this tool is not the check you need. Three benches have died there
already, most explicitly a hand run where `"a closed hand"` fired on 0 of 90
frames. Screen 0 in `docs/fit.md` is free and eliminates more ideas than this
does. The warning below fires on the obvious cases; it is a nudge, not a filter.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Enough scenes that the standard error is smaller than the band. Hanley-McNeil
# on an AUC near 0.8 is about 0.07 at n=20 and 0.055 at n=30, against a band
# 0.05 wide - so 30 is the smallest n that can land inside it, and the set costs
# about four and a half minutes to generate either way.
DEFAULT_N = 30

# The stage to read. "pca 512" is the frozen projection the board's queries live
# in and tracks the 1152-d row closely (0.811 vs 0.825 mean over ten contrasts);
# the full tower is the more conservative NO-GO, because a distinction the 1152-d
# teacher does not have cannot appear at 512.
STAGE = "teacher 1152"

STOP, THIN = 0.75, 0.80

# An absence on either side. Deliberately crude and deliberately non-blocking:
# the failure it points at is real and common, but "an empty glass" is also a
# perfectly good NEGATIVE phrase when the positive is "a glass with tea", so a
# filter here would be wrong more often than the warning is.
ABSENCE = re.compile(r"\b(no|not|without|empty|closed|missing|absent|"
                     r"unoccupied|clear(?:ed)?)\b", re.IGNORECASE)


def warn_wording(pos: str, neg: str) -> None:
    hits = [s for s in (pos, neg) if ABSENCE.search(s)]
    if not hits:
        return
    print("\n  note: a side of this contrast names an absence -")
    for h in hits:
        print(f"          {h!r}")
    print("        Three benches have died on exactly that: the absence side")
    print("        ranks correctly BETWEEN the two scenes and never wins its")
    print("        own, and 'a closed hand' fired on 0 of 90 frames. If there")
    print("        is a positive noun for the state - a fist, a closed door -")
    print("        use it. See docs/fit.md, Screen 0.\n")


def generate(out: Path, args) -> None:
    cmd = ["uv", "run", "--script", "tools/synth_pairs.py",
           "--out", str(out), "--min-side", str(args.min_side),
           "-n", str(args.n), "--pos", args.pos, "--neg", args.neg,
           "--pos-edit", args.pos_edit, "--neg-edit", args.neg_edit]
    if args.cat:
        cmd += ["--cat", args.cat]
    if args.find:
        cmd += ["--find", args.find]
    print(f"generating {args.n} pairs into {out} ...")
    r = subprocess.run(cmd, cwd=ROOT, check=False)
    if r.returncode or not (out / "pos").is_dir():
        raise SystemExit(f"synth_pairs.py failed ({r.returncode}); nothing to "
                         f"score. Its own output above says why.")


def measure(setdir: Path, pos: str, neg: str) -> dict:
    out = setdir / "fit_check.json"
    cmd = ["uv", "run", "--script", "tools/probe_bisect.py",
           "--a", str(setdir / "pos"), "--b", str(setdir / "neg"),
           "--pos", pos, "--neg", neg, "--paired", "--no-student",
           "--json", str(out)]
    keep = setdir / "keep.txt"
    if keep.exists():
        # Only if the set has already been through the blind screen for some
        # other purpose. This tool never creates one.
        cmd += ["--keep", str(keep)]
        print(f"  (using the existing {keep.name})")
    log = setdir / "fit_check.log"
    with log.open("w") as fh:
        subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                       check=False)
    if not out.exists():
        raise SystemExit(f"probe_bisect wrote nothing; see {log}")
    return json.loads(out.read_text())


def verdict(auc: float, n: int) -> tuple[str, list[str]]:
    if auc < STOP:
        return "NO-GO", [
            "The teacher does not carry this distinction, and it is the",
            "least compressed thing in the chain. Nothing downstream can",
            "recover what it never had, so a bench would measure staging.",
            "Re-word the contrast and run this again before shooting one."]
    if auc < THIN:
        return "UNSETTLED", [
            "Inside the band where this screen does not decide. The measured",
            "contrasts thin out here and the two that fell below it also came",
            "back below chance on the student. Treat a bench as a coin flip",
            "worth one morning, not three."]
    return "GO", [
        "The teacher carries it. That is the only thing this screen can say,",
        "and it is not a prediction about the board: a generated set inflates",
        "every score and the student's column on one has read HIGHER than on a",
        "pair the appliance handles perfectly. Bench it - three times, on three",
        "different days, re-staged from scratch, and believe the low end.",
        "docs/fit.md has why one bench is not a measurement."]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="the long version is in docs/fit.md",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", type=Path, default=None, metavar="DIR",
                    help="score an existing set instead of generating one; "
                         "reads its queries.txt")
    ap.add_argument("--pos", help="the phrase for the positive state")
    ap.add_argument("--neg", help="the phrase for the negative state")
    ap.add_argument("--pos-edit", help="the positive state, as an instruction "
                                       "to the image editor")
    ap.add_argument("--neg-edit", help="the negative state, likewise")
    ap.add_argument("--cat", default=None,
                    help="COCO category to draw source photographs from; "
                         "without it synth_pairs.py searches captions")
    ap.add_argument("--find", default=None,
                    help="caption regex, passed through to synth_pairs.py")
    ap.add_argument("-n", type=int, default=DEFAULT_N)
    ap.add_argument("--min-side", type=int, default=120,
                    help="smallest source box, in pixels, before cropping - "
                         "the guard against a set of unreadably small objects")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to put a generated set "
                         "(default: bench/stills/_fitcheck-<slug>)")
    args = ap.parse_args()

    if args.set:
        setdir = args.set if args.set.is_absolute() else ROOT / args.set
        q = (setdir / "queries.txt").read_text().splitlines()
        pos, neg = q[0], q[1]
    else:
        missing = [f"--{k.replace('_', '-')}" for k in
                   ("pos", "neg", "pos_edit", "neg_edit")
                   if not getattr(args, k)]
        if missing:
            ap.error("need " + ", ".join(missing) + " (or --set)")
        pos, neg = args.pos, args.neg
        slug = re.sub(r"[^a-z0-9]+", "-", pos.lower()).strip("-")[:40]
        setdir = args.out or ROOT / "bench" / "stills" / f"_fitcheck-{slug}"
        if not setdir.is_absolute():
            setdir = ROOT / setdir
        generate(setdir, args)

    print(f"\n  positive : {pos}")
    print(f"  negative : {neg}")
    print(f"  set      : {setdir.relative_to(ROOT)}")
    warn_wording(pos, neg)

    got = measure(setdir, pos, neg)
    auc = got["stages"][STAGE]["sep"]
    n = min(got["n"])
    luma = got.get("cues", {}).get("luma", {}).get("sep")

    print(f"\n{'=' * 72}")
    print(f"  teacher, pooled cross-scene AUC   {auc:.3f}   "
          f"(n = {n} per side, SO400M at 1152-d)")
    call, why = verdict(auc, n)
    print(f"\n  {call}")
    for line in why:
        print(f"    {line}")
    if luma is not None and luma >= 0.80:
        print("\n  and read that number with suspicion: mean frame luma alone")
        print(f"    separates this contrast at AUC {luma:.3f}. An encoder can")
        print("    score well here by reading the lamp. Re-shoot the contrast")
        print("    so the two states are equally bright before believing it.")
    print(f"{'=' * 72}")
    print(f"\n  full table in {(setdir / 'fit_check.log').relative_to(ROOT)}")
    print("  what to do next, either way: docs/fit.md")
    return 0 if call != "NO-GO" else 1


if __name__ == "__main__":
    sys.exit(main())
