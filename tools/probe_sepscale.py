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
proposes, scored with the board's own held-out figure - the live `HELD OUT` line
that tools/score_cue.py reprints, NOT the `one visit per state` replay under it,
which is a different measurement and disagrees by up to 83 points. The logs
themselves are in bench/cue/ and bench/README.md lists both columns for each:

    run          held out   sep(1 visit)  ratio(1)  ratio(2)  ratio(all)
    08-17 07:33    96.7 %       2.40         2.52      3.24      3.03
    08-11 07:22   100.0 %       2.28        13.91      2.94      3.49
    08-17 09:18    91.7 %       3.61         2.17      2.64      2.29
    ------------------ 2.6, and everything above it worked ----------------
    08-17 13:35    57.5 %       5.46         2.49      2.08      1.70
    08-17 09:33    59.2 %       3.83         2.71      1.24      1.06
    08-17 11:26    92.5 %       0.07         0.09      1.12      2.15
    08-17 09:57    74.2 %       3.69         1.81      0.94      1.05
    08-17 11:44    68.3 %       0.55         0.24      0.92      0.58
    08-17 13:39    74.2 %       3.73         2.93      1.89      1.73
    08-17 08:57    76.7 %       5.83         3.69      0.87      0.95
    08-17 09:55    47.5 %       0.84         0.67      0.44      0.04
    08-16 17:22    58.3 %       0.17         0.05      0.22      0.28
    08-16 17:35    57.5 %       0.26         0.10      0.15      0.09

THIS TABLE HAD A VOID IN IT AND 11:26 FILLED IT. Written on the first nine rows,
this tool claimed ratio(2) put every run on the correct side with nothing
between 1.24 and 2.64. 11:26 is the first bench whose references the board
itself built from two visits - the first prospective test rather than a replay -
and it read 1.12 here and scored 92.5%, the best in the project. The board
printed THE CLASSES OVERLAP and told the operator to throw it away.

SO IT WAS READ ONE-SIDED FOR HALF A DAY - above 2.6 three runs had scored 91.7%,
96.7% and 100.0%, so high certified and low said nothing - AND 13:35 ENDED THAT
TOO. Beware which column you check that against: the bar lived in the firmware
and ran on the BOARD's ratio, from the 20 frames after the key press, not on
this tool's, which pools the first 20 of each cued span. 11:26 replays at 1.12
here and the board printed 1.8x. Four benches have ever produced a board-side
two-visit ratio - every prospective test the bar has had - and they run
3.7 -> 57.5%, 2.3 -> 74.2%, 1.8 -> 92.5%, 1.2 -> 68.3%: backwards at both ends.
So this tool no longer prints a verdict either, and m9.c has no constant.

`sep` on its own is worse still - its largest value, 5.83, belongs to a 76.7%
run and 11:26 scored 92.5% at 0.07 - and so is ratio(1), which puts 08:57 (3.69)
above 09:18 (2.17) and scores them 76.7% against 91.7%. Four quantities
measurable at enrolment now, four failures of the same shape: what decides a run
is where the object lands on visits that have not happened yet. 13:35 is the
cleanest demonstration in the tree, and it is in the visit centres this tool
prints first rather than in any ratio below them - its opened book walked +1.96,
+3.25, +4.64, +4.39 across four visits while its closed book sat at +5.8, so the
reference built from visits 1 and 2 described neither of the visits it was
scored on. Read those rows before the ratios.

WHAT THE RATIO IS. Pool the first N visits of a class and take the mean: that
is the class centre rather than one visit's accident, and `sep` becomes the gap
between centres. `noise` is the RMS distance of one enrolled frame from its own
class's centre, pooled the same way - so from two visits on it contains the
BETWEEN-VISIT staging variance, which is the term that decided 09:18 against
09:33 and which a one-visit enrolment structurally cannot see. ratio is
sep/noise, and a frame lands nearer the wrong centre once noise exceeds half the
gap - which is where the 2.0 this shipped with came from, and 11:26 is why that
argument is not enough on its own: a small gap pointed the right way still
classifies. Every visit is trimmed to WINDOW frames first, because that is all
the board averages.

WHY THIS IS A TOOL AND NOT A NOTE. The board only has the visits it was shown,
so ratio(all) is not available to it at the bench - ratio(2) is, if enrolment
takes two key presses per class. Whether those two are enough, or whether the
third visit was quietly doing the work, is what the three columns printed side
by side are for. They are what FGX_ENROL_V in firmware/m9.c was chosen on. There
is no longer a threshold for them to set: see THE ENROLMENT RATIO there.
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
    # NO VERDICT. There was a bar here at 2.6 and m9.c had one to match; both
    # went on 2026-08-17, when the highest ratio the board has ever computed
    # certified a 57.5% run. See THE ENROLMENT RATIO in firmware/m9.c.
    print("      no verdict on that ratio: high and low have each now been "
          "wrong. On the\n      board's own column 3.7 scored 57.5% and 1.8 "
          "scored 92.5%.")


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
