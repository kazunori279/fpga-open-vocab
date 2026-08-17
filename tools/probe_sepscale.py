# /// script
# requires-python = ">=3.11"
# ///
"""Is `sep` a scale you can trust? Issue #19, and the unit #18's radius is in.

    uv run --script tools/probe_sepscale.py /tmp/m9_cue.log [more logs...]

WHAT WENT WRONG WITH sep. Every distance the board quotes - the absent radius,
the enrolment guard's ratio, the LED's brightness - is in units of `sep`, the
gap between the two closest enrolled references. Those references each come
from ONE visit: the operator presses '1', twenty frames are averaged, and
wherever that visit happened to sit becomes the class. A visit sits at the edge
of its class's spread about as often as in the middle, so a single-visit `sep`
is the gap between two arbitrary points of two clouds. It can read large when
the clouds overlap and small when they do not.

It does exactly that. Every cue bench there is, sorted by the ratio this tool
proposes, scored with the board's own held-out figure from tools/score_cue.py:

    run          held out   sep(1 visit)  ratio(1)  ratio(2)  ratio(all)
    08-17 07:33    96.7 %       2.40         2.52      3.24      3.03
    08-11 07:22   100.0 %       2.28        13.91      2.94      3.49
    08-17 09:18    91.7 %       3.61         2.17      2.64      2.29
    -------------------------------------- the void ----------------------
    08-17 09:33    59.2 %       3.83         2.71      1.24      1.06
    08-17 09:57    74.2 %       3.69         1.81      0.94      1.05
    08-17 08:57    76.7 %       5.83         3.69      0.87      0.95
    08-17 09:55    47.5 %       0.84         0.67      0.44      0.04
    08-16 17:22    58.3 %       0.17         0.05      0.22      0.28
    08-16 17:35    57.5 %       0.26         0.10      0.15      0.09

ratio(2) separates the three runs that worked from the six that did not, with
nothing between 1.24 and 2.64 and every run on the correct side. `sep` on its
own does not: its largest value of all nine, 5.83, belongs to a 76.7% run, and
its smallest three include a 58.3%. Neither does ratio(1), the within-window
scatter the guard uses today - it puts 08:57 (3.69) above 09:18 (2.17) and
scores them 76.7% against 91.7%.

Read this as a two-sided sorter and not as a predictor. Inside each group the
ratio says nothing: 0.94 outscored 1.24 by fifteen points. What it gets right,
on nine runs out of nine, is which side of "worth running" a bench is on.

WHAT THE RATIO IS. Pool the first N visits of a class and take the mean: that
is the class centre rather than one visit's accident, and `sep` becomes the gap
between centres. `noise` is the RMS distance of one enrolled frame from its own
class's centre, pooled the same way - so from two visits on it contains the
BETWEEN-VISIT staging variance, which is the term that decided 09:18 against
09:33 and which a one-visit enrolment structurally cannot see. ratio is
sep/noise, and a frame lands nearer the wrong centre once noise exceeds half
the gap, so 2.0 is where it stops meaning anything. Every visit is trimmed to
WINDOW frames first, because that is all the board averages.

WHY THIS IS A TOOL AND NOT A NOTE. The board only has the visits it was shown,
so ratio(all) is not available to it at the bench - ratio(2) is, if enrolment
takes two key presses per class. Whether those two are enough, or whether the
third visit was quietly doing the work, is the question the firmware constant
has to be set against, so all three columns are printed side by side. They are
what FGX_ENROL_V and FGX_ENROL_SNR in firmware/m9.c were chosen on.
"""
import itertools
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_multivisit import WINDOW, mean_of, visits  # noqa: E402
from probe_reject import dist, load  # noqa: E402


def geometry(vis, labs, pick):
    """(sep, noise) over the visits `pick(label)` keeps, each trimmed to WINDOW.

    Trimming matters: the board averages FGX_ENROL_N frames of a visit, not the
    whole of one, and a visit is longer than that. Feeding whole visits here
    would measure a window the firmware cannot capture.
    """
    ref, noise = {}, {}
    for lab in labs:
        kept = [v[:WINDOW] for v in pick(lab)]
        ref[lab] = mean_of(kept)
        flat = [f for v in kept for f in v]
        noise[lab] = sum(st.mean((f[j] - ref[lab][j]) ** 2 for f in flat)
                         for j in range(len(ref[lab]))) ** 0.5
    sep = min(dist(ref[a], ref[b]) for a, b in itertools.combinations(labs, 2))
    return sep, max(noise.values())


def score(log: Path) -> None:
    spans, frames, _enrol, _window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    vis = visits(spans, frames, names)
    labs = sorted(vis)
    print(f"\n=== {log.name}")
    if len(labs) < 2:
        print("    fewer than two classes cued - nothing to be nearest to")
        return
    nv = min(len(vis[lab]) for lab in labs)
    if nv < 2:
        print("    a class visited once cannot say how far its visits move - "
              "skipped")
        return

    for lab in labs:
        cs = [mean_of([v[:WINDOW]]) for v in vis[lab]]
        print(f"    {lab:<18} visit centres " +
              "  ".join(f"{c[0]:+6.2f}" for c in cs))

    one, one_n = geometry(vis, labs, lambda lab: vis[lab][:1])
    two, two_n = geometry(vis, labs, lambda lab: vis[lab][:2])
    allv, all_n = geometry(vis, labs, lambda lab: vis[lab])

    print(f"    the board today   sep {one:5.2f}  noise {one_n:5.2f}  "
          f"ratio {one / one_n:5.2f}   (1 visit per class)")
    print(f"    two visits        sep {two:5.2f}  noise {two_n:5.2f}  "
          f"ratio {two / two_n:5.2f}   <- what the board could have")
    print(f"    every visit       sep {allv:5.2f}  noise {all_n:5.2f}  "
          f"ratio {allv / all_n:5.2f}   ({nv}+ visits per class)")
    if two / two_n < 2.0:
        print("      the two-visit ratio is under 2.0: at the bench this run "
              "would be\n      called overlapping and re-enrolled before it ran.")


def main() -> int:
    logs = [Path(a) for a in sys.argv[1:]]
    if not logs:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    for log in logs:
        score(log)
    print("\nCompare the two-visit ratio with the every-visit one. Where they "
          "agree, two\nkey presses buy the whole measurement; where the "
          "two-visit one reads high, the\nthird visit was carrying it and two "
          "presses are not enough.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
