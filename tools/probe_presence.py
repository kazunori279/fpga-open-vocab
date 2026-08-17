# /// script
# requires-python = ">=3.11"
# ///
"""Is #18's presence stage a radius problem or a geometry problem? Every bench.

    uv run --script tools/probe_presence.py bench/cue/*.log

WHY THIS EXISTS. On 2026-08-17 three benches were read as showing the empty desk
sitting FURTHER from the class references than the class frames do, and that was
written up as "the ordering is inverted, so no radius fixes it" - which is
backwards twice over. Further IS the direction the rule wants: it cuts on
`absent <=> min_k || c[] - qref[k] || > radius`. What those three benches
actually showed is a small separation being missed by a large threshold. The
claim went onto issue #18 before anyone replayed the other benches, and this
script is what replaying them looks like.

WHAT IT MEASURES, per bench, on held-out frames only:

  AUC       P(a random empty frame is further from every reference than a
            random class frame). 1.0 is perfect, 0.5 is no signal, and BELOW
            0.5 is the genuine inversion - the empty desk sitting nearer the
            references than the objects do, which no threshold can repair.
  best r    the radius that maximises balanced accuracy, quoted BOTH as a
            fraction of `sep` and in absolute distance, because sep is not a
            scale (tools/probe_sepscale.py) and quoting a threshold in it is
            the same mistake the enrolment guard already made.
  shipped   what FGX_ABSENT_TRIP = 2.0 sep does on the same frames.

AND THEN THE PART THAT MATTERS. Four constants have now been fitted to these
benches and each one broke on the next bench, so this script does not propose a
fifth. It runs leave-one-bench-out instead: fit the radius on every bench but
one, score it on the one, and report the gap. A constant that survives that is
worth a bench to confirm; a constant that does not is the same mistake again,
and the honest output is to say so and stop.

Balanced accuracy is the mean of the two rates, not overall accuracy: the two
populations differ in size and the interesting failure is asymmetric - keeping
every class frame while holding no empty one scores 50% here and looks like 57%
under a naive count.
"""
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_reject import EMPTY, Skip, auc, centred, dist, load, references

# A fraction of sep from 0.05 to 4.0, and an absolute distance over the range
# the logs actually span. Both grids are deliberately fine: the question is
# whether a best radius EXISTS and transfers, so a coarse grid that missed one
# would answer it wrongly.
FRACS = [0.05 * i for i in range(1, 81)]
ABSOL = [0.05 * i for i in range(1, 161)]


def bench(log: Path):
    """(sep, scat, empty distances, class distances), or Skip if not scoreable.

    Held out means held out: the enrolment windows are gone with `engage`, and
    nothing is ever fit to the empty scene under this rule - key '0' is not
    enrolled - so every empty frame here is test data.

    `scat` is the worst class's RMS spread at enrolment - the same quantity the
    board already divides by for FGX_ENROL_SNR. It is here as a third unit to
    quote the radius in, because unlike `sep` it is a spread and a threshold on
    distances is a spread.
    """
    spans, frames, enrol, window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    refs, _lab, _nv, scat, sep, engage = references(
        spans, frames, enrol, window, names)
    keys = sorted(refs)
    # The enrolled spans are training data for the class side. The empty side
    # has none by construction.
    taught = {(a, b) for a, b, _ in spans
              if any(a - 1 <= f + 2 <= b for f, k in enrol if k != "0")}
    db, dc = [], []
    for a, b, lab in spans:
        for i in range(a, b + 1):
            if i not in frames or i < engage:
                continue
            c = centred(frames[i][0], names)
            d = min(dist(c, refs[k]) for k in keys)
            if lab == EMPTY:
                db.append(d)
            elif (a, b) not in taught:
                dc.append(d)
    # NO BASELINE FALLBACK, unlike probe_reject.py. The baseline sits where the
    # background was just frozen, i.e. at the origin by construction, so a
    # distance rule scores it trivially and the answer is about the origin
    # rather than about an empty desk. A bench without the empty rotation
    # cannot answer this question and is dropped rather than averaged in.
    if not db or not dc:
        raise Skip("no held-out empty scene - #15's rotation was not running")
    return sep, max(scat.values()), db, dc


def balanced(db, dc, r):
    return (sum(x > r for x in db) / len(db) + sum(x <= r for x in dc) / len(dc)) / 2


def best(db, dc, grid, scale=1.0):
    """(balanced, r) over the grid, r in the grid's own units."""
    return max(((balanced(db, dc, f * scale), f) for f in grid), key=lambda t: t[0])


def main() -> int:
    logs = [Path(a) for a in sys.argv[1:]]
    if not logs:
        print(__doc__.strip().splitlines()[2].strip())
        return 2

    runs = []
    for log in logs:
        try:
            runs.append((log.name, *bench(log)))
        except (Skip, SystemExit) as why:
            print(f"{log.name:<42} skipped: {why}")
    if not runs:
        print("\nnothing scoreable")
        return 1

    print(f"\n{'bench':<34}{'AUC':>7}{'emp':>7}{'cls':>7}"
          f"{'sep':>7}{'scat':>7}{'r*':>7}{'bal':>7}{'@2.0sep':>9}")
    for name, sep, scat, db, dc in runs:
        a = auc(db, dc)
        bf, rf = best(db, dc, ABSOL)
        print(f"{name[:34]:<34}{a:7.3f}{st.median(db):7.2f}{st.median(dc):7.2f}"
              f"{sep:7.2f}{scat:7.2f}{rf:7.2f}{100 * bf:6.1f}%"
              f"{100 * balanced(db, dc, 2.0 * sep):8.1f}%")
    print("  emp/cls are median distances to the nearest reference and r* is "
          "the best radius,\n  all absolute; @2.0sep is what the shipped "
          "FGX_ABSENT_TRIP scores on these frames.")

    inv = [r for r in runs if auc(r[3], r[4]) < 0.5]
    print(f"\n{len(inv)} of {len(runs)} benches are genuinely inverted "
          f"(AUC < 0.5, the empty desk NEARER the\nreferences than the objects "
          f"- no radius repairs those):")
    for name, _sep, _sc, db, dc in inv:
        print(f"  {name}  AUC {auc(db, dc):.3f}")
    if not inv:
        print("  none")

    # LEAVE ONE BENCH OUT. This is the whole point: `sep`, ratio(1), ratio(2)
    # and the held-out column have each been got wrong by fitting to the benches
    # in hand, so the question is not "what radius is best here" but "does a
    # radius fitted here survive a bench it has not seen".
    # Three units to quote the radius in. `sep` is what the firmware ships,
    # `scat` is the enrolment spread the board already computes for the guard,
    # and absolute is the control - if a raw distance transfers as well as
    # either scaled one, then neither scaling is buying anything.
    UNITS = (("sep, as FGX_ABSENT_TRIP does", FRACS, lambda s, c: s),
             ("scat, the enrolment spread", FRACS, lambda s, c: c),
             ("absolute distance", ABSOL, lambda s, c: 1.0))
    summary = []
    for unit, grid, scale in UNITS:
        print(f"\nleave-one-out, radius in {unit}")
        print(f"  {'held-out bench':<34}{'fitted r':>10}{'on the rest':>13}"
              f"{'on it':>8}{'its own best':>14}")
        gaps = []
        for i, (name, sep, scat, db, dc) in enumerate(runs):
            rest = runs[:i] + runs[i + 1:]
            fit, on_rest = None, None
            for f in grid:
                m = st.mean(balanced(d1, d2, f * scale(s, c))
                            for _n, s, c, d1, d2 in rest)
                if on_rest is None or m > on_rest:
                    fit, on_rest = f, m
            got = balanced(db, dc, fit * scale(sep, scat))
            own = best(db, dc, grid, scale(sep, scat))[0]
            gaps.append((own - got, got))
            print(f"  {name[:34]:<34}{fit:10.2f}{100 * on_rest:12.1f}%"
                  f"{100 * got:7.1f}%{100 * own:13.1f}%")
        cost = st.mean(g for g, _ in gaps)
        summary.append((unit, cost, max(g for g, _ in gaps),
                        st.mean(b for _, b in gaps)))
        print(f"  mean cost of not having seen the bench: {100 * cost:.1f} "
              f"points   (worst {100 * max(g for g, _ in gaps):.1f})")

    # AND THE ONE THAT IS ACTUALLY SHIPPED, on the same frames and the same
    # column, because "a better radius would score X" means nothing without it.
    shipped = st.mean(balanced(db, dc, 2.0 * sep)
                      for _n, sep, _c, db, dc in runs)
    print(f"\n{'unit':<32}{'blind mean':>12}{'LOO cost':>11}{'worst':>8}")
    for unit, cost, worst, blind in summary:
        print(f"{unit:<32}{100 * blind:11.1f}%{100 * cost:10.1f}{100 * worst:8.1f}")
    print(f"{'FGX_ABSENT_TRIP = 2.0 sep, today':<32}{100 * shipped:11.1f}%")
    print("  `blind mean` is what a radius chosen WITHOUT seeing the bench "
          "scores on it, which\n  is the only column that describes the "
          "appliance. 50% is the floor: keep every\n  class frame, hold no "
          "empty one - which is what the shipped constant does on five\n"
          "  of these nine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
