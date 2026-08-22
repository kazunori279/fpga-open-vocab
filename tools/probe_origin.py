#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Is there a unit in which "this reference enrolled on the origin" is a rule?

    uv run --script tools/probe_origin.py bench/cue/m9_cue-*.log

Issue #21 records the observation and then blocks itself on one condition:
**quote the threshold in something that is not `sep`, or the constant is broken
before it ships.** `2.0 sep` means 0.52 absolute on one bench and 5.80 on
another, because a collapsed pair shrinks `sep`, which is the denominator. This
script is that condition, tested.

It runs `tools/probe_reject.py` over every bench log named, reads three numbers
out of what that script already prints - the nearest pair `sep`, the distance
from the origin to each reference in units of `sep`, and each reference's own
frame scatter - and scores three candidate units against the presence AUC of
the same run:

    d/sep    what the board prints today
    d raw    the same distance with no denominator, in background-sigma
    d/scat   over the spread of the frames that reference was averaged from

A bench counts as inverted when its presence AUC is below 0.5 - the empty desk
sitting nearer the references than the class frames do, which is the failure
#21 wants to catch at enrolment.

WHAT IT SAID ON 2026-08-22
---------------------------
Every bench in `bench/cue/` that scores - 29 of the 37 logs, the other 8 having
no `.cues` sidecar, a VOID one, or no frames after the rule engages. No
hand-picking, because choosing which benches a guard is allowed to be judged on
is itself a fit.

Ten inverted, nineteen not.

    unit     rank-AUC   best in-sample threshold
    d/sep      0.642    refuse below 0.33 - catches 6/10, refuses  3/19 healthy
    d raw      0.668    refuse below 1.96 - catches 10/10, refuses 11/19 healthy
    d/scat     0.805    refuse below 1.38 - catches 10/10, refuses  7/19 healthy

**All three overlap, and the unit already printed is not the worst of them.**
Dropping the denominator is no better than keeping it. `d/scat` ranks best and
then picks up the same disease from the other side: to catch all ten it has to
refuse seven good benches, and the second-largest `d/scat` in the table belongs
to 08-11 07:22 only because that bench's scatter of 0.13 is four times smaller
than any other's - the mirror image of the collapse that broke `sep`.

**And every unit has a counterexample at its own extreme.** The smallest origin
distance in the whole archive is not an inverted bench in any of the three
units: `d/sep` 0.01 is 08-20 06:24 at AUC 0.536, and `d raw` 0.02 and `d/scat`
0.03 are both 08-17 15:27's `a glass with tea` at AUC 0.754. Whichever unit a
guard is quoted in, the first bench it refuses is one that worked.

So the answer to #21's precondition is no, and the reason is not the unit.
"""
import re
import subprocess
import sys
from pathlib import Path

REJECT = Path(__file__).with_name("probe_reject.py")

# probe_reject.py prints these; this script owns none of the arithmetic. If its
# output format moves, this breaks loudly here rather than reporting a wrong
# number, which is why every field is required below.
RE_SCAT = re.compile(
    r"^      (\S.*?)\s+((?:[-+][\d.]+\s+)+)\+-([\d.]+)\s+\(\d+ visit", re.MULTILINE)
RE_SEP = re.compile(r"nearest pair ([\d.]+) apart")
RE_AUC = re.compile(r"AUC ([\d.]+)  <- 1\.000")
RE_DIST = re.compile(r"^      (\S.*?)\s+([\d.]+)\s*$", re.MULTILINE)


def read(path: Path) -> dict | None:
    """One bench, or None if probe_reject could not score it."""
    r = subprocess.run([sys.executable, str(REJECT), str(path)],
                       capture_output=True, text=True, check=False)
    b = r.stdout
    sep, auc = RE_SEP.search(b), RE_AUC.search(b)
    if not sep or not auc or "distance from the origin" not in b:
        return None
    scat = {m.group(1).strip(): float(m.group(3)) for m in RE_SCAT.finditer(b)}
    seg = b.split("distance from the origin")[1].split("reconstruction")[0]
    dist = {k.strip(): float(v) for k, v in RE_DIST.findall(seg)}
    if not dist:
        return None
    # The reference NEAREST the origin is the one that can absorb empty frames,
    # so it is the one a guard would fire on. The others do not matter.
    k = min(dist, key=dist.get)
    s = float(sep.group(1))
    return {"name": path.stem.replace("m9_cue-", ""), "auc": float(auc.group(1)),
            "ref": k, "sep": s, "scat": scat[k], "d/sep": dist[k],
            "d raw": dist[k] * s, "d/scat": dist[k] * s / scat[k]}


def main(paths: list[str]) -> int:
    rows = [r for r in (read(Path(p)) for p in paths) if r]
    if not rows:
        sys.exit("no bench scored - probe_reject.py wanted logs with a .cues "
                 "sidecar and an empty span")
    rows.sort(key=lambda r: r["auc"])
    units = ("d/sep", "d raw", "d/scat")
    print(f"{'bench':<16}{'AUC':>7} {'sep':>5} {'scat':>5}  "
          f"{'reference nearest the origin':<24}" +
          "".join(f"{u:>8}" for u in units))
    for r in rows:
        print(f"{r['name']:<16}{r['auc']:>7.3f}{'*' if r['auc'] < 0.5 else ' '}"
              f"{r['sep']:>5.2f}{r['scat']:>6.2f}  {r['ref']:<24}" +
              "".join(f"{r[u]:>8.2f}" for u in units))

    inv = [r for r in rows if r["auc"] < 0.5]
    ok = [r for r in rows if r["auc"] >= 0.5]
    print(f"\n* inverted: the empty desk nearer the references than the class "
          f"frames are.\n  {len(inv)} inverted, {len(ok)} not.\n")
    if not inv or not ok:
        return 0
    for u in units:
        a, b = [r[u] for r in inv], [r[u] for r in ok]
        rank = sum((x < y) + 0.5 * (x == y) for x in a for y in b) / (len(a) * len(b))
        best = max(
            (((sum(1 for r in inv if r[u] <= t),
               sum(1 for r in ok if r[u] <= t)), t) for t in sorted(r[u] for r in rows)),
            key=lambda c: c[0][0] / len(inv) + (len(ok) - c[0][1]) / len(ok))
        (tp, fp), t = best
        bal = 100 * (tp / len(inv) + (len(ok) - fp) / len(ok)) / 2
        print(f"  {u:<7} rank-AUC {rank:.3f}   inverted {min(a):.2f}-{max(a):.2f}, "
              f"healthy {min(b):.2f}-{max(b):.2f}   "
              f"{'clean split' if max(a) < min(b) else 'THEY OVERLAP'}")
        print(f"          best in-sample: refuse below {t:.2f} - catches "
              f"{tp}/{len(inv)}, wrongly refuses {fp}/{len(ok)}, "
              f"balanced {bal:.1f}%")
    print("\n  Every threshold above is fitted to the benches in hand, which is "
          "the sequence\n  that has now deleted sep, ratio(1), ratio(2) and "
          "FGX_ENROL_SNR. Read them as\n  ceilings on what a guard could do, "
          "not as candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
