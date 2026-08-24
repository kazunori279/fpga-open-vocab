#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Give "neither" a reference of its own, and does #18's presence stage work?

    uv run --script tools/probe_third.py bench/cue/*.log

WHERE THIS COMES FROM. `tools/probe_absent.py` showed that #18's rule, with two
queries, is a BAND on the margin axis: `min_k ||c[] - qref[k]||` is exactly
`min_k |D - D_ref[k]| / sqrt(2)`, so `radius` is the half-width of an interval
centred on the two references. The two classes own the inside of that interval
by construction, "neither" has no orthogonal direction to occupy, and on ten
benches of 28 the empty desk lands inside the band - eight of those ten invert.
No half-width separates a point from the interval it is sitting in.

So stop asking for a half-width. **Enrol the empty desk as a third reference and
take the nearest of three.** The axis is still one-dimensional and the empty
desk is still a point on it, but the rule no longer requires it to be FAR - only
to be somewhere the other two are not. That is a weaker demand, and the archive
can answer whether it is weak enough without reflashing anything.

    absent  <=>  argmin_k | D - D_ref[k] |  is the empty reference

WHAT IT COSTS TO ASK. Under #18's rule nothing enrolled ever sees an empty desk;
under this one something must, so a span is spent. This script enrols from the
FIRST empty span after the rule engages, using the same window length the board
used for the classes, and scores on everything else - the remaining empty spans
and all the class frames. Benches with only one empty span after engage are
skipped rather than scored against their own reference.

THREE ARMS, all on the same held-out frames, all as balanced accuracy so a
rotation with 30 empty frames and 60 class frames cannot be won by answering
"present":

    band-oracle   the best radius for THIS bench, fitted on the frames it is
                  scored on. Not shippable - it is the ceiling #18's shape can
                  never exceed, and the honest thing to beat.
    three-nn      the rule above. No threshold, nothing fitted but the third
                  reference itself.
    shipped       `FGX_ABSENT_TRIP = 2.0 sep`, what the board does today.

AND ONE DIAGNOSTIC. The obvious way for this to fail is for the empty desk to
not stay put: on 2026-08-25 run 1 the four empty spans of one bench sat at
-3.02, -0.59, +1.02 and +1.01 on the margin axis while the classes translated
underneath them. `drift` is the spread of the empty span centres in units of the
classes' own frame scatter, so a bench where the empty desk wanders has a large
one, and it is the number that says whether a third reference could ever have
held.
"""
import statistics as st
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_reject import BASELINE, EMPTY, Skip, load, references

SETTLE = 10             # dropped off every span, on top of the sidecar's own
MIN_FRAMES = 10
SHIPPED = 2.0           # FGX_ABSENT_TRIP, in sep


def balanced(call, empty_d, present_d):
    """Mean of the two recalls. `call(d)` is True for 'absent'."""
    e = sum(call(d) for d in empty_d) / len(empty_d)
    p = sum(not call(d) for d in present_d) / len(present_d)
    return 50.0 * (e + p)


def measure(log: Path):
    if "fake" in log.stem or "smoke" in log.stem:
        raise Skip("synthetic or a smoke test; it would vote in the pooled mean")
    spans, frames, enrol, window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    labels = sorted({lab for _a, _b, lab in spans if lab not in (EMPTY, BASELINE)})
    if len(labels) != 2 or set(labels) != set(names):
        raise Skip("the margin identity this rests on is a two-query fact")

    refs, ref_lab, _nvis, _scat, sep, engage = references(spans, frames, enrol, window, names)
    if len({ref_lab[k] for k in refs}) != 2:
        raise Skip("both classes need a reference")
    a_lab, b_lab = labels
    dref = {ref_lab[k]: 2 * refs[k][0] for k in refs}     # c = [+D/2, -D/2]

    # Every usable span, in schedule order, as (label, [margins]).
    runs = []
    for a, b, lab in spans:
        if lab == BASELINE:
            continue
        m = [frames[i][0][a_lab] - frames[i][0][b_lab]
             for i in range(a + SETTLE, b + 1) if i in frames and i >= engage]
        if m:
            runs.append((lab, m))

    empties = [m for lab, m in runs if lab == EMPTY]
    if len(empties) < 2:
        raise Skip("one empty span or none after engage; nothing left to test on")
    # The board would enrol from a window, not from a whole span.
    held_empty = empties[1:]
    d_empty_ref = st.mean(empties[0][:window])

    present = [d for lab, m in runs if lab != EMPTY for d in m]
    empty = [d for m in held_empty for d in m]
    if min(len(present), len(empty)) < MIN_FRAMES:
        raise Skip(f"fewer than {MIN_FRAMES} held-out frames on one side")

    scat = st.mean(st.pstdev([d for lab, m in runs if lab == lab_i for d in m])
                   for lab_i in labels)
    band = lambda d: min(abs(d - dref[a_lab]), abs(d - dref[b_lab])) / 2 ** 0.5

    # band-oracle: sweep every midpoint between adjacent observed distances.
    ds = sorted({band(d) for d in empty + present})
    cuts = [(x + y) / 2 for x, y in pairwise(ds)] or [ds[0]]
    e_b, p_b = [band(d) for d in empty], [band(d) for d in present]
    oracle = max(balanced(lambda v, r=r: v > r, e_b, p_b) for r in cuts)

    # three-nn: nearest of three references, no threshold anywhere.
    third = lambda d: (abs(d - d_empty_ref)
                       < min(abs(d - dref[a_lab]), abs(d - dref[b_lab])))
    three = balanced(third, empty, present)

    ship = balanced(lambda d: band(d) > SHIPPED * sep, empty, present)

    centres = [st.mean(m) for m in empties]
    drift = (max(centres) - min(centres)) / scat if scat else float("nan")
    return log.stem.replace("m9_cue-", ""), oracle, three, ship, drift, len(empties)


def pearson(xs, ys):
    mx, my = st.mean(xs), st.mean(ys)
    sx = sum((a - mx) ** 2 for a in xs) ** 0.5
    sy = sum((b - my) ** 2 for b in ys) ** 0.5
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True)) / (sx * sy)


def main(argv):
    if not argv:
        raise SystemExit("pass bench logs")
    rows = []
    for arg in argv:
        log = Path(arg)
        try:
            rows.append(measure(log))
        except (Skip, SystemExit) as e:
            print(f"  skip {log.stem}: {e}", file=sys.stderr)
    if not rows:
        raise SystemExit("nothing scoreable")

    rows.sort(key=lambda r: r[2] - r[3])
    print(f"{'bench':<18} {'oracle':>7} {'three':>7} {'ship':>7} "
          f"{'three-ship':>10} {'drift':>6} {'spans':>5}")
    for name, orc, thr, shp, dft, ns in rows:
        print(f"{name:<18} {orc:7.1f} {thr:7.1f} {shp:7.1f} {thr - shp:+10.1f} "
              f"{dft:6.2f} {ns:5d}")

    n = len(rows)
    orc = [r[1] for r in rows]
    thr = [r[2] for r in rows]
    shp = [r[3] for r in rows]
    dft = [r[4] for r in rows]
    print(f"\nn = {n}   balanced accuracy, held out, mean over benches")
    print(f"  band-oracle (unshippable ceiling)   {st.mean(orc):5.1f}")
    print(f"  three-nn    (a third reference)     {st.mean(thr):5.1f}")
    print(f"  shipped     (2.0 sep)               {st.mean(shp):5.1f}")
    diff = [t - s for t, s in zip(thr, shp, strict=True)]
    sd = st.stdev(diff)
    print(f"\nthree-nn against shipped: {st.mean(diff):+.1f} points, sd {sd:.1f},"
          f" t = {st.mean(diff) / (sd / n ** 0.5):.2f} on {n - 1} df,"
          f" wins {sum(d > 0 for d in diff)}/{n}")
    print(f"three-nn against the band's own ceiling:"
          f" {st.mean(t - o for t, o in zip(thr, orc, strict=True)):+.1f} points,"
          f" beats it on {sum(t > o for t, o in zip(thr, orc, strict=True))}/{n}")
    print(f"\nempty-span drift (class SD): median {st.median(dft):.2f},"
          f" max {max(dft):.2f}")
    print(f"drift against three-nn: r = {pearson(dft, thr):+.3f}")
    steady = [r for r in rows if r[4] <= st.median(dft)]
    wander = [r for r in rows if r[4] > st.median(dft)]
    for tag, g in (("steady", steady), ("wanders", wander)):
        if g:
            print(f"  {tag:8s} (n={len(g):2d}): three-nn {st.mean(r[2] for r in g):5.1f},"
                  f" shipped {st.mean(r[3] for r in g):5.1f}")


if __name__ == "__main__":
    main(sys.argv[1:])
