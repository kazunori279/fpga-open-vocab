# /// script
# requires-python = ">=3.11"
# dependencies = ["scipy"]
# ///
"""Reproduce the two numbers this session's README quotes.

`tools/probe_camlock.py` is the only definition of `common` and `margin` in the
repo and stays that way - this shells out to it and reads its table rather than
re-implementing the walk, so the README cannot drift from the probe. The probe
deliberately stops at a median and a range ("anything that needs a p-value here
needs more runs first"); this session pre-registered eight pairs and a one-sided
Mann-Whitney U, so the p-value is computed here, in the session directory, and
not added to the shared tool.

ONE-SIDED, AND THE DIRECTION WAS FIXED BEFORE THE RUN. #30 predicts the lock
REDUCES the walk, so the alternative is lock < free. A two-sided p is printed
beside it because a result that runs the other way is worth seeing, but it is
not the pre-registered test and must not be quoted as one.

    uv run --script bench/soak/20260907-camlock-cold/score.py

Run it from the repo root; it finds its own directory and passes the sixteen
logs to the probe in session order.
"""
import re
import subprocess
import sys
from pathlib import Path

from scipy.stats import mannwhitneyu

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE = ROOT / "tools" / "probe_camlock.py"

# Session order: the arms alternate and the leading arm swaps every pair, so
# this is not free-1..8 then lock-1..8. Ordered as the morning ran them.
ORDER = ["free-1", "lock-1", "lock-2", "free-2", "free-3", "lock-3",
         "lock-4", "free-4", "free-5", "lock-5", "lock-6", "free-6",
         "free-7", "lock-7", "lock-8", "free-8"]

ROW = re.compile(r"^(\S+)\s+(free|exposure gain)\s+\d+\s+"
                 r"(-?[\d.]+)\s+(-?[\d.]+)\s")


def main():
    logs = [HERE / f"{n}.log" for n in ORDER]
    if missing := [p.name for p in logs if not p.exists()]:
        sys.exit(f"missing logs: {' '.join(missing)}")

    out = subprocess.run([sys.executable, str(PROBE), *map(str, logs)],
                         capture_output=True, text=True, check=True).stdout
    print(out)

    walks = {"common": {"lock": [], "free": []},
             "margin": {"lock": [], "free": []}}
    for line in out.splitlines():
        if m := ROW.match(line):
            _, arm, common, margin = m.groups()
            key = "free" if arm == "free" else "lock"
            walks["common"][key].append(float(common))
            walks["margin"][key].append(float(margin))

    for col in ("common", "margin"):
        lock, free = walks[col]["lock"], walks[col]["free"]
        if len(lock) != 8 or len(free) != 8:
            sys.exit(f"{col}: parsed {len(lock)} lock and {len(free)} free "
                     f"rows, expected eight of each - the probe's output "
                     f"format moved and this script did not follow it")
        one = mannwhitneyu(lock, free, alternative="less")
        two = mannwhitneyu(lock, free, alternative="two-sided")
        tag = "PRE-REGISTERED PRIMARY" if col == "common" else "secondary"
        print(f"{col}  ({tag})")
        print(f"  lock mean {sum(lock)/8:.2f}  ({min(lock):.2f}-{max(lock):.2f})")
        print(f"  free mean {sum(free)/8:.2f}  ({min(free):.2f}-{max(free):.2f})")
        print(f"  U {one.statistic:.1f}   p one-sided (lock < free) "
              f"{one.pvalue:.4f}   p two-sided {two.pvalue:.4f}\n")


if __name__ == "__main__":
    main()
