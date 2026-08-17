# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Rank the anti-collapse loss variants on a subset, before burning a full run.

    uv run --script tools/sieve_loss.py [--subset 30000] [--epochs 20] [--batch 512]

WHY A SIEVE
-----------
model/distill.py on the whole of train2017 is 54 minutes a candidate, measured -
the machine sits at 1450 img/s on MPS and neither a bigger batch nor fp16 moves
it, so there is no cheaper full run to be had. There are five things worth
trying. Three of them are wrong and the point is to find out which for four
minutes each rather than an hour each.

That only works if the variants separate early, and they do: on a 2-epoch
val2017 smoke run the baseline sat at chance for holdout top-1 (0.001) while
--infonce 1.0 was already at 0.011. The defect being fixed is visible almost
immediately, because it is a defect of geometry rather than of accuracy.

BATCH 512, NOT 128
------------------
The same benchmark found MPS delivers identical throughput at batch 128, 256 and
512 - it is bandwidth-saturated, not launch-bound. So a 4x larger batch is free
in wall-clock, and InfoNCE's whole signal is the number of in-batch negatives:
127 becomes 511. The learning rate goes up by sqrt(4) = 2 to match, which is the
conservative scaling for AdamW; linear scaling on 1160 total steps would be
asking for a divergent first epoch.

WHAT THE SIEVE READS
--------------------
peak holdout top-1, and the cone norm against the teacher's. Not raw cosine -
raw cosine is what said 0.843 while the student was failing, and every repulsive
term will *lower* it by construction, since spreading the outputs out costs
agreement on average. A candidate winning on raw cosine here would be a
candidate that had done nothing.

The subset result ranks candidates. It does not predict the full-run number, and
a variant that needs more data to pay off - RKD plausibly does, since it is
fitting a similarity matrix rather than a classification - could be underrated
here. Take the top two into the full run, not the top one.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The four repulsion settings worth an hour of the sieve, plus the incumbent.
# infonce at two weights because the smoke run's raw cosine fell to 0.4619 at
# w=1.0, which is either "undertrained at 2 epochs" or "the term is too heavy" -
# 0.3 is what tells the two apart. rkd on its own and stacked, because it and
# InfoNCE attack the cone from different sides and the stack is not obviously
# redundant.
VARIANTS = [
    ("baseline", []),
    ("infonce-1.0", ["--infonce", "1.0"]),
    ("infonce-0.3", ["--infonce", "0.3"]),
    ("rkd-10", ["--rkd", "10.0"]),
    ("infonce-0.3+rkd-10", ["--infonce", "0.3", "--rkd", "10.0"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", type=int, default=30000)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=6e-3, help="sqrt-scaled for batch 512")
    ap.add_argument("--split", default="train2017")
    ap.add_argument("--only", default=None, help="run one variant by name")
    args = ap.parse_args()

    todo = [v for v in VARIANTS if args.only in (None, v[0])]
    print(f"{len(todo)} variants x {args.subset} images x {args.epochs} epochs "
          f"@ batch {args.batch}, lr {args.lr}\n")

    rows = []
    for name, flags in todo:
        run = f"_sieve_{name}"
        cmd = ["uv", "run", "--script", "model/distill.py",
               "--split", args.split, "--subset", str(args.subset),
               "--epochs", str(args.epochs), "--batch", str(args.batch),
               "--lr", str(args.lr), "--name", run, *flags]
        t0 = time.time()
        print(f"--- {name} " + "-" * (60 - len(name)))
        log = ROOT / "model" / "runs" / f"{run}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as fh:
            p = subprocess.run(cmd, cwd=ROOT, stdout=fh,
                               stderr=subprocess.STDOUT, check=False)
        text = log.read_text()
        # The per-epoch lines are the useful trace; the step heartbeat is
        # carriage-return spam and would bury it.
        for line in text.splitlines():
            # The epoch summaries only - "epoch N : step .../..." is the
            # carriage-return heartbeat and there are hundreds of them.
            if re.match(r"epoch\s+\d+ : (?!step )", line.strip()):
                print("  " + line.strip())
        cfg = ROOT / "model" / "runs" / run / "config.json"
        if p.returncode not in (0, 1) or not cfg.exists():
            print(f"  FAILED (rc={p.returncode}), see {log}")
            continue
        c = json.loads(cfg.read_text())
        c["variant"], c["minutes"] = name, (time.time() - t0) / 60
        rows.append(c)
        print(f"  peak top1 {c['peak_top1']:.3f}   peak centered {c['peak_centered']:+.4f}   "
              f"cone {c['cone_norm']:.4f}   {c['minutes']:.1f} min\n")

    if not rows:
        return 1
    tcone = rows[0]["teacher_cone_norm"]
    base = next((r for r in rows if r["variant"] == "baseline"), None)
    print("=" * 78)
    print(f"{'variant':22}{'top1':>8}{'vs base':>9}{'centered':>11}"
          f"{'raw':>9}{'cone':>8}   (teacher cone {tcone:.4f})")
    for r in sorted(rows, key=lambda r: -r["peak_top1"]):
        rel = (f"{r['peak_top1'] / base['peak_top1']:6.1f}x"
               if base and base["peak_top1"] > 0 else "     -")
        print(f"{r['variant']:22}{r['peak_top1']:>8.3f}{rel:>9}"
              f"{r['peak_centered']:>+11.4f}{r['best_cosine']:>+9.4f}"
              f"{r['cone_norm']:>8.4f}")
    print("\nTake the top two into a full run. Raw cosine is expected to fall - "
          "spreading the\noutputs apart costs average agreement, and average "
          "agreement is what hid the bug.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
