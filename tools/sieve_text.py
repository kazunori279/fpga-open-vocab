#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Rank `--text` weights on the statistic the product is written in.

    uv run --script tools/sieve_text.py                    # train and rank
    uv run --script tools/sieve_text.py --score-only       # re-rank what exists
    uv run --script tools/sieve_text.py --confirm text-0.3 # spend the held-out draw

WHY NOT tools/sieve_loss.py
---------------------------
That sieve ranks on holdout top-1, retrieval of an image from its own teacher
bank, and it was the right metric for the defect it was aimed at - a student
hugging the cone axis cannot retrieve anything. It is the wrong metric here.
`--text` is not trying to make the student better at telling val2017 images
apart; it is trying to make the part it gets right be the part a sentence can
reach. A variant can improve cross-scene AUC and lose top-1, and on that table
it would rank last.

So this sieve reads `sep` out of `probe_bisect.py --json`: the pooled
cross-scene AUC, P(a random positive-state image outranks a random
negative-state image OF A DIFFERENT SCENE). That is the requirement - *is the
book open, whatever else changed* - and the argument for it over the paired
column is in `bench/stills/README.md`.

TEN CONTRASTS, NOT TWO, AND WHY THAT IS THE VARIANCE THAT MATTERED
------------------------------------------------------------------
The first run of this sieve had two contrasts, and `rkd-10` scored +0.120
against the baseline on `book` and -0.035 on `glass`. The sign changes with the
contrast. So "does this variant help" has no answer from two of them, however
many scenes each one has: within a contrast the AUC's standard error at ~25
scenes is 0.08-0.09 by Hanley-McNeil, but the contrast-to-contrast spread is
about 0.11, and only one of those two shrinks by shooting more scenes.

Ten contrasts put the SE of the mean paired difference near 0.11/sqrt(10) =
0.035, which is the first point at which an 0.05 effect is visible at all. It
also makes the question a better match for the product: an appliance is not
sold on books and glasses, it is sold on "is this thing in that state", and ten
household objects is a sample of that rather than two anecdotes.

The comparison is PAIRED. Every variant is scored on the same contrasts, so the
statistic is the mean of per-contrast differences and its SE, not the
difference of two means. A variant good at ovens and bad at beds is a real
finding; a table of unpaired means hides it inside the spread between objects.

THE SECOND DRAWS ARE STILL HELD OUT
-----------------------------------
`book-crop2/` and `glass-crop2/` are not in the selection list. They are the
one thing left that has never been used to choose anything, and `--confirm`
spends them on the single variant already chosen. If --confirm disagrees with
the selection set, that is the answer: the effect was the draw. Say so and keep
the baseline - do not go back for a third set and pick the best of three.

WHAT IS HELD FIXED
------------------
Everything the so400m sweep held fixed: 30000 images, 20 epochs, batch 512, lr
6e-3, `--infonce 0.3`, and the SO400M-pca512 targets those rows were distilled
against. `so400m-s30k` and `so400m-rkd10` are those rows, already trained, and
they are scored alongside as the incumbent and as what used to look like a
winner. A new row is only interesting against them - and note that on ten
contrasts `so400m-rkd10` is not above `so400m-s30k`, so the bar is the plain
baseline.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STILLS = ROOT / "bench" / "stills"

# The teacher these rows share, and the bank built in its space. A bank from
# another teacher would multiply cleanly against 512-d targets and mean nothing;
# model/distill.py checks the width, which catches the unprojected case only.
TARGETS = "model/cache/emb_train2017_SO400M-pca512_s30000.npy"
BANK = "model/cache/textbank_emb_train2017_SO400M-pca512_s30000_4096.npy"

# Weights an order of magnitude apart, because nothing yet says what scale this
# term wants: on a 2-epoch smoke run at w=1.0 the text term was ~4x the size of
# 1-cos and holdout raw cosine fell from 0.73 to 0.17, which is either "too
# heavy" or "undertrained". Three weights tell those apart. The stack with RKD
# was included because RKD 10 was, at the time, the only setting that had
# replicated (+0.10 on the book pair, both draws, both model families) and the
# two terms are not obviously redundant - RKD constrains image-image geometry,
# --text constrains image-text. That premise did not survive this script's own
# first ten-contrast run: RKD 10 is -0.022 +-0.023, and neither it nor any
# --text weight separates from the plain baseline. The variants are kept as the
# record of what was measured.
TEXT = ["--text-bank", BANK]
VARIANTS = [
    ("text-0.1", [*TEXT, "--text", "0.1"]),
    ("text-0.3", [*TEXT, "--text", "0.3"]),
    ("text-1.0", [*TEXT, "--text", "1.0"]),
    ("text-0.3+rkd-10", [*TEXT, "--text", "0.3", "--rkd", "10.0"]),
]
# Already trained, by tools/sieve_loss.py's settings. Not retrained here.
REFERENCE = ["so400m-s30k", "so400m-rkd10"]

# Ten household objects with a binary, visible, editable state. `tv` on/off and
# `dining table` set/cleared were considered and left out: the first is answered
# by mean luma, which probe_bisect.py prints as the trivial cue, and the second
# is a state of the scene rather than of the object, which is the opposite of
# what "whatever else changed" asks.
SETS = ["book", "glass", "laptop", "refrigerator", "oven",
        "toilet", "umbrella", "suitcase", "bowl", "bed"]
CONFIRM = ["book", "glass"]


def setdir(name: str, draw: int = 1) -> str:
    return f"20260822-synth-{name}-crop" + ("2" if draw == 2 else "")


def run_name(variant: str) -> str:
    return f"_text_{variant}"


def train(variant: str, flags: list[str], args) -> bool:
    run = run_name(variant)
    out = ROOT / "model" / "runs" / run
    if (out / "student.pt").exists() and not args.force:
        print(f"--- {variant}: already trained, skipping")
        return True
    cmd = ["uv", "run", "--script", "model/distill.py",
           "--split", "train2017", "--targets", TARGETS,
           "--subset", str(args.subset), "--epochs", str(args.epochs),
           "--batch", str(args.batch), "--lr", str(args.lr),
           "--infonce", str(args.infonce), "--name", run, *flags]
    log = ROOT / "model" / "runs" / f"{run}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(f"--- {variant} " + "-" * max(0, 58 - len(variant)))
    with log.open("w") as fh:
        p = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                           check=False)
    # distill.py returns 1 for "no better than the mean", which is a verdict on
    # the student and not a failure to produce one. Only a missing checkpoint is.
    ok = (out / "student.pt").exists()
    print(f"    {'trained' if ok else f'FAILED rc={p.returncode}, see {log}'}"
          f"   {(time.time() - t0) / 60:.1f} min")
    return ok


def score(setname: str, dirname: str, runs: list[str], tag: str) -> dict:
    """probe_bisect on one set, every run in one pass, and hand back `sep`.

    One pass rather than one per run: the runs then share the pixels, the
    encoder and the query vectors, so the column is a comparison and not two
    measurements that happen to be near each other.
    """
    d = STILLS / dirname
    pos, neg = (d / "queries.txt").read_text().splitlines()[:2]
    out = ROOT / "model" / "runs" / f"_text_score_{tag}_{setname}.json"
    cmd = ["uv", "run", "--script", "tools/probe_bisect.py",
           "--a", str(d / "pos"), "--b", str(d / "neg"),
           "--pos", pos, "--neg", neg, "--paired",
           "--keep", str(d / "keep.txt"),
           "--runs", ",".join(runs), "--json", str(out)]
    log = out.with_suffix(".log")
    with log.open("w") as fh:
        subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                       check=False)
    if not out.exists():
        raise SystemExit(f"{setname}/{dirname}: probe_bisect wrote nothing, "
                         f"see {log}")
    return json.loads(out.read_text())


def table(title: str, results: dict, labels: list[str], runs: list[str],
          sets: list[str], base: str) -> None:
    """Per-contrast AUCs, then the paired difference against the baseline.

    The mean row is what to read, and the +-  beside it is the SE of the mean
    of the PER-CONTRAST differences. A variant whose lead is inside one of
    those has not beaten the baseline; it has beaten this draw of ten objects.
    """
    print(f"\n{'=' * 78}\n{title}")
    head = "".join(f"{s[:6]:>8}" for s in sets)
    print(f"{'run':20}{head}{'mean':>8}{'vs base':>18}")
    rows, per = [], {}
    for label, r in zip(labels, runs, strict=True):
        per[r] = [results[s]["stages"].get(label, {}).get("sep") for s in sets]
        have = [v for v in per[r] if v is not None]
        rows.append((r, sum(have) / len(have) if have else float("nan")))

    for r, mean in sorted(rows, key=lambda x: -x[1]):
        d = [a - b for a, b in zip(per[r], per[base], strict=True)
             if a is not None and b is not None]
        if r == base or len(d) < 2:
            gain = "  (baseline)" if r == base else ""
        else:
            m = sum(d) / len(d)
            sd = (sum((x - m) ** 2 for x in d) / (len(d) - 1)) ** 0.5
            gain = f"{m:>+9.3f} +-{sd / len(d) ** 0.5:.3f}"
        print(f"{r:20}"
              + "".join(f"{v:>8.3f}" if v is not None else f"{'-':>8}"
                        for v in per[r])
              + f"{mean:>8.3f}{gain:>18}")

    for stage in ("teacher 1152", "pca 512"):
        got = [results[s]["stages"].get(stage, {}).get("sep") for s in sets]
        have = [v for v in got if v is not None]
        print(f"{stage:20}"
              + "".join(f"{v:>8.3f}" if v is not None else f"{'-':>8}"
                        for v in got)
              + f"{sum(have) / len(have):>8.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--subset", type=int, default=30000)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=6e-3)
    ap.add_argument("--infonce", type=float, default=0.3,
                    help="held at the so400m sweep's value so the new rows are "
                         "comparable to the ones already measured")
    ap.add_argument("--force", action="store_true",
                    help="retrain variants that already have a checkpoint")
    ap.add_argument("--score-only", action="store_true",
                    help="skip training and re-rank the checkpoints on disk")
    ap.add_argument("--confirm", default=None, metavar="VARIANT",
                    help="score ONE chosen variant on the held-out second draw. "
                         "Read the docstring before using it twice")
    args = ap.parse_args()

    if args.confirm:
        run = args.confirm if args.confirm.startswith("so400m") \
            else run_name(args.confirm)
        runs = [*REFERENCE, run] if run not in REFERENCE else list(REFERENCE)
        results = {s: score(s, setdir(s, 2), runs, "confirm") for s in CONFIRM}
        # probe_bisect labels student rows by what the names DON'T share, so
        # read the labels back out of the file rather than guessing them.
        labels = [k for k in results[CONFIRM[0]]["stages"]
                  if k not in ("teacher 1152", "pca 512")]
        table(f"HELD-OUT SECOND DRAWS - {run}, scored once", results, labels,
              runs, CONFIRM, REFERENCE[0])
        print("\nThese draws are spent. A variant that wins here and on the "
              "selection set has\nreplicated; one that wins on only one of "
              "them has not, and the honest\nconclusion is the baseline. Two "
              "contrasts cannot carry a mean - read the two\ncolumns, not the "
              "average of them.")
        return 0

    if not args.score_only:
        for name, flags in VARIANTS:
            if not train(name, flags, args):
                return 1

    runs = [*REFERENCE, *(run_name(n) for n, _ in VARIANTS)]
    missing = [r for r in runs
               if not (ROOT / "model" / "runs" / r / "student.pt").exists()]
    if missing:
        raise SystemExit("no checkpoint for: " + ", ".join(missing))
    # A contrast with no keep.txt has not been screened, and an unscreened set
    # is a third to a half invalid pairs. Refuse it rather than average it in.
    unscreened = [s for s in SETS
                  if not (STILLS / setdir(s) / "keep.txt").exists()]
    if unscreened:
        raise SystemExit("no keep.txt (not blind-screened): "
                         + ", ".join(unscreened))
    results = {s: score(s, setdir(s), runs, "select") for s in SETS}
    labels = [k for k in results[SETS[0]]["stages"]
              if k not in ("teacher 1152", "pca 512")]
    table("SELECTION SET - rank here, then confirm ONE on the held-out draws",
          results, labels, runs, SETS, REFERENCE[0])
    print("\nPick one row, then: uv run --script tools/sieve_text.py --confirm "
          "<variant>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
