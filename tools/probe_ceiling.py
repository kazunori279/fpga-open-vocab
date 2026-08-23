# /// script
# requires-python = ">=3.11"
# ///
"""What is the CEILING on a bench, before the decision rule touches it? Issue #23.

    uv run --script tools/probe_ceiling.py bench/cue/*.log

Two of the four benches on 2026-08-17 scored badly for completely different
reasons and looked identical in every table this repo had. Filing one under the
other wastes mornings, so this measures the ceiling first and the rule second.

THE CEILING IS THE MARGIN, AND WITH TWO QUERIES THAT IS EXACT. The board decides
in the centred space, `c[i] = z[i] - mean(z)`. With exactly two queries that
space is one-dimensional - c = [+D/2, -D/2] for D = z[A] - z[B] - so the margin
D carries EVERYTHING the board could possibly use. No rule, no enrolment and no
threshold downstream can separate two scenes better than D separates them. That
makes `|sep|` a hard ceiling and not an indicator.

IT IS NOT A GUARD AND CANNOT BECOME ONE. It needs held-out frames of both
classes, so it does not exist until the run is over - unlike `sep`, the two
ratios and `enrolled from`, which are all available at enrolment and have all
been wrong. There is no constant here to fit and nothing for firmware/m9.c to
do with it. It is a post-hoc reading of what a bench was ever able to show.

WHAT IT DOES NOT ISOLATE. D is the encoder AND the two phrases AND the staging,
together. A pair that reads low here has one of: an encoder that cannot see the
difference, a phrase that does not ask for it, or a scene that did not present
it. The third is separable from the first two by running the pair again - see
`pair best` below - and the first two need the frames re-encoded against other
wordings, which needs the images, which only exist for a run started with
`--preview`. Ask for the phrase first: it is free, and the text tower is the
teacher's own at full precision.

THE COLUMNS, and reading them in order is the whole method:

  sep AUC     P(a random A-frame's margin ranks above a random B-frame's), over
              every cued class frame. Orientation is fixed by sorting the two
              labels, so it is directional on purpose - see INVERTED below.
  |sep|       the same, folded: max(AUC, 1-AUC). How separable the two scenes
              are AT ALL, ignoring which way round. THIS is the ceiling.
  within      the same margin AUC computed inside matched visits - visit 1 of A
              against visit 1 of B, visit 2 against visit 2 - each folded, then
              averaged. It asks whether the scenes separate when nothing has had
              time to move.
  best        the accuracy the best threshold on that margin would have got,
              over every cued frame. An oracle: it is fitted to the frames it
              scores, on purpose.
  held        the same oracle, restricted to the frames `state` is scored on.
              It exists so that `lost` subtracts two numbers about the same
              population - see WHY THERE ARE TWO ORACLE COLUMNS below.
  state       what the nearest-reference rule got on held-out frames, with no
              presence gate. The number the ceiling bounds; the live `HELD OUT`
              figure tools/score_cue.py prints is lower because #18's gate is
              in it, and on some benches the gate is most of the difference.
  lost        `held - state`. WHAT THE DECISION RULE COST, and the column that
              answers the question. It cannot go negative.

WHY THERE ARE TWO ORACLE COLUMNS. `best` runs over all the cued frames, because
the ceiling is a property of the two scenes and nothing is fitted to it. `state`
runs only over what is left after the enrolment visits are dropped and the
frames before the rule engaged are dropped - typically half as many. Subtracting
one from the other was comparing two populations, and it showed: 08-20 06:37
printed `lost -2.9`, and the 08-17 11:26 and 15:42 rows carried a negative for
three days. A negative reads as the rule beating the margin ceiling, which in a
one-dimensional centred space is impossible - nearest-reference IS a threshold
on the margin, at the midpoint of the two references, so it is one of the cuts
the oracle already tried. `held` closes that, and `state <= held` is asserted on
every bench rather than argued. Read `best` for the ceiling and `held` for the
comparison; where they differ a lot, the held-out half of the run was easier or
harder than the taught half, which is itself worth noticing.

A per-pair summary follows the table with the highest |sep| each pair ever
reached, which is what turns a low ceiling from a fact about the model into a
fact about the morning.

A LOW CEILING IS NOT A LOW CEILING FOR THE PAIR. The book pair reads 1.000 on
2026-08-11 and 0.599 on 2026-08-16 17:35 - same two phrases, same book, same
desk, same firmware. So `|sep|` well under the same pair's best is a statement
about THAT MORNING and not about the model: the scene did not present the
difference. Only a pair whose best is itself low is a candidate for "the encoder
does not carry this", and one run cannot establish that. The glass pair had
exactly one run until 2026-08-20 and was therefore unproven rather than proven;
it now has three, two of them fenced by book controls, and the section further
down says what that bought.

That is why this script has no threshold. An absolute floor would have called
half the book runs a model limitation, and the book is the one pair that has
scored 100.0%.

INVERTED IS NOT ABSENT, and it is the other half of the trap. An AUC of 0.30 is
0.20 away from chance in the WRONG DIRECTION - the query naming a scene scores
lower on it than the other query does - which is a labelling failure on a real
signal, not an absent signal. 0.50 is absent. It costs the shipped rule nothing,
because nearest-reference learns the direction from the enrolment and never
reads the phrase's meaning; it would have broken M20's two-stage rule outright,
and #19's founding run is the recorded case (`a closed book` at AUC 0.954 with
the sign inverted, 46 of 66 closed-book frames called opened).

WHAT IT SAID, over the twenty-two scoreable benches, sorted by ceiling. `lost`
is `held - state`, the points the decision rule did not collect:

    bench          |sep|  within   best   held   state   lost   pair
    08-11 07:22    1.000   1.000  100.0% 100.0%  100.0%    0.0  book
    08-20 06:24    1.000   1.000  100.0% 100.0%   95.0%    5.0  book
    08-17 15:20    0.999   0.999   98.8%  99.2%   95.8%    3.3  hand
    08-17 07:33    0.994   0.995   99.4% 100.0%   96.7%    3.3  book
    08-17 09:18    0.971   0.975   94.4%  92.5%   91.7%    0.8  book
    08-17 13:35    0.970   0.975   93.8%  94.2%   57.5%   36.7  book   <-
    08-20 06:37    0.950   0.941   94.6%  99.2%   97.5%    1.7  book
    08-17 11:26    0.932   0.915   88.3%  95.0%   92.5%    2.5  book
    08-17 15:42    0.928   0.948   89.6%  91.7%   90.8%    0.8  bag
    08-17 13:39    0.916   0.956   82.1%  86.7%   74.2%   12.5  book
    08-17 09:57    0.895   0.887   86.7%  84.2%   74.2%   10.0  book
    08-17 08:55    0.873   0.836   88.9%  97.5%   25.8%   71.7  book   <-
    08-17 09:33    0.838   0.803   77.8%  73.3%   59.2%   14.2  book
    08-16 17:22    0.824!  0.819   85.6%  87.5%   58.3%   29.2  book   <-
    08-17 08:57    0.787   0.894   85.0%  77.5%   76.7%    0.8  book
    08-17 15:37    0.771!  0.863   80.4%  89.2%   78.3%   10.8  person
    08-17 11:44    0.746   0.769   72.5%  70.8%   68.3%    2.5  book
    08-17 15:27    0.699!  0.699   67.5%  75.0%   60.0%   15.0  glass  <-
    08-20 06:33    0.680   0.867   67.9%  60.0%   49.2%   10.8  glass
    08-16 17:35    0.599!  0.593   62.2%  65.8%   56.7%    9.2  book
    08-20 06:29    0.591!  0.701   61.7%  56.7%   51.7%    5.0  glass
    08-17 09:55    0.579   0.654   59.4%  57.5%   47.5%   10.0  book

THE FOUR ARROWS ARE ISSUE #19, AND THE REST ARE NOT. Only four benches threw
away a ceiling they had: 72, 37, 29 and 15 points. Everything else collected
what was on offer to within about a dozen points, and scored low because the
ceiling was low that morning. "Nothing since 08-11 reproduces it" was reading
one number - the rule's output - where two were needed.

STILL FOUR, BUT NOT THE SAME FOUR, once `lost` was put on like-for-like frames.
08-17 09:33 fell from 18.6 to 14.2 and is no longer one of them; 08-17 15:27,
the first glass run, rose from 7.5 to 15.0 and is. That last one sits exactly on
the line and is the weakest member of the set there has ever been - it is in
because LOST_19 is `>=`, and it is one rounding away from being out. Do not
build an argument on it. The three real ones - 72, 37 and 29 points - are as
clear as they were.

AND THE BOOK PAIR'S CEILING SWINGS FROM 1.000 TO 0.579 ACROSS THE SAME DESK,
which is the finding underneath that one. Sixteen runs of two phrases against
one book, and the ceiling alone spans 42 points. Whatever a bench measures, a
large part of it is decided before the decision rule is reached.

THE GLASS PAIR NOW HAS THREE RUNS AND IS STILL THE ONLY CANDIDATE FOR "the
encoder does not carry this". 0.699, 0.680, 0.591, and the two 08-20 runs were
fenced by book controls at 1.000 and 0.950 taken thirteen minutes apart on the
same desk - so "the scene did not show it" has been ruled out for those two by
the strongest control this repo can run. Note also that the glass margin does
not keep its sign: 0.301 and 0.409 inverted against 0.680 upright. Inverted is
a real signal named backwards and is repairable by renaming; a signal that
changes direction between runs of the same phrases is not, and there is nothing
to rename. See docs/bring-up-log.md, 2026-08-20.

PASS IT BENCHES, NOT THE GLOB, for anything quotable. `bench/cue/*.log` also
scores the 10:48, 10:52 and `2e48d86` smoke tests, which are too short to mean
anything, and `m9_cue_fake_d.log`, a doctored copy of 08-16 17:22 - it prints
identical figures to that run, which is a good demonstration and a bad average.
`bench/README.md` is the manifest.

Every frame of every cued class span counts towards `|sep|`, `within` and
`best`. Nothing is fitted to those, so there is nothing to hold out: they are a
property of the two scenes and the two phrases, not of an enrolment. `state` is
held out, and is computed the way tools/probe_reject.py computes it so the two
agree; `held` exists only to give it something to be subtracted from.
"""
import itertools
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_reject import (
    BASELINE,
    EMPTY,
    Skip,
    auc,
    centred,
    dist,
    load,
    references,
)

# How far under its own pair's best a run has to read before the difference is
# worth a sentence rather than a shrug. Not a threshold on anything: it selects
# which line of prose gets printed, and the numbers are printed either way.
NOTABLE = 0.10

# The same, for `lost`: how many points a run has to throw away before the
# reading calls it #19. IN PRINTED POINTS, and compared against the rounded
# figure, so the prose and the table can never disagree - 08-17 15:27 lands on
# exactly 15.0, and under the old `lost > 0.15` on raw floats it qualified only
# because 0.75 - 0.60 evaluates to 0.15000000000000002. A bench decided by
# binary representation is a bench nobody can check. It is `>=` deliberately:
# on the line counts, and a run sitting there is the weakest member there is.
LOST_19 = 15.0


def fold(a):
    """How separable, ignoring direction. 0.5 is no signal either way."""
    return max(a, 1.0 - a)


def best_cut(pos, neg):
    """Best accuracy over any threshold, in whichever direction is better."""
    xs = sorted(set(pos + neg))
    cuts = ([xs[0] - 1.0]
            + [(x + y) / 2 for x, y in itertools.pairwise(xs)]
            + [xs[-1] + 1.0])
    best = 0.0
    for c in cuts:
        hi = sum(p > c for p in pos) + sum(n <= c for n in neg)
        best = max(best, hi, len(pos) + len(neg) - hi)
    return best / (len(pos) + len(neg))


def ceiling(log: Path):
    """(labels, sep_auc, within, cut, held, state, n) or Skip."""
    spans, frames, enrol, window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    labels = sorted({lab for _a, _b, lab in spans
                     if lab not in (EMPTY, BASELINE)})
    if len(labels) != 2:
        raise Skip(f"{len(labels)} cued classes - the margin needs exactly two")
    if len(names) != 2:
        raise Skip(f"{len(names)} queries - the centred space is not 1-D")
    # The cue labels and the query phrases are the same strings, which is what
    # makes the margin's orientation knowable. If a bench ever cues a label that
    # is not also a query, refuse rather than guess which way round it goes.
    if set(labels) != set(names):
        raise Skip("the cued labels are not the queries - no unambiguous margin")
    a_lab, b_lab = labels

    def margin(i):
        z = frames[i][0]
        return z[a_lab] - z[b_lab]

    # Grouped per span as well as pooled, because the difference between those
    # two is the whole of the `within` column.
    per_span = []
    for a, b, lab in spans:
        if lab in (EMPTY, BASELINE):
            continue
        xs = [margin(i) for i in range(a, b + 1) if i in frames]
        if xs:
            per_span.append((lab, xs))
    pos = [x for lab, xs in per_span if lab == a_lab for x in xs]
    neg = [x for lab, xs in per_span if lab == b_lab for x in xs]
    if not pos or not neg:
        raise Skip("only one of the two classes was ever cued")

    # WITHIN A VISIT. Pair the i-th visit of A with the i-th visit of B - cue.py
    # interleaves them, so the two members of a pair are minutes apart rather
    # than a whole run apart. Each pair is folded BEFORE averaging: two visits
    # that separate in opposite directions is a staging failure, not a
    # cancellation, and folding first is what makes the average say so.
    va = [xs for lab, xs in per_span if lab == a_lab]
    vb = [xs for lab, xs in per_span if lab == b_lab]
    pairs = [fold(auc(p, n)) for p, n in zip(va, vb, strict=False)]
    within = st.mean(pairs) if pairs else float("nan")

    # WHAT THE RULE GOT: nearest reference in the centred space, held out, and
    # deliberately WITHOUT the presence gate, which is issue #18's and would
    # confound this. Same arithmetic as probe_reject.py's closing line.
    #
    # The oracle is run a SECOND time here, over exactly these frames, and that
    # is the `held` column. `best` is over all 240; the rule is scored on the
    # ~120 that survive dropping the enrolment visits and everything before the
    # rule engaged. Subtracting one from the other was comparing two different
    # populations, and it showed: 08-20 06:37 printed `lost -2.9`, which reads
    # as the rule beating the margin ceiling. In a 1-D centred space it cannot -
    # nearest-reference IS a threshold on the margin, at the midpoint of the two
    # references, so it is one of the cuts the oracle already tried. A negative
    # `lost` was never a finding, only a mismatched denominator, and the 08-17
    # 11:26 and 15:42 rows carried one for three days.
    state = held = float("nan")
    try:
        refs, ref_lab, _nv, _sc, _sep, engage = references(
            spans, frames, enrol, window, names)
        keys = sorted(refs)
        taught = {(a, b) for a, b, _ in spans
                  if any(a - 1 <= f + 2 <= b for f, k in enrol if k != "0")}
        ok = n = 0
        hpos: list[float] = []
        hneg: list[float] = []
        for a, b, lab in spans:
            if lab in (EMPTY, BASELINE) or (a, b) in taught:
                continue
            for i in range(a, b + 1):
                if i not in frames or i < engage:
                    continue
                c = centred(frames[i][0], names)
                k = min(keys, key=lambda k: dist(c, refs[k]))
                ok += ref_lab[k] == lab
                n += 1
                (hpos if lab == a_lab else hneg).append(margin(i))
        if n:
            state = ok / n
        if hpos and hneg:
            held = best_cut(hpos, hneg)
            # The invariant above, asserted rather than trusted. If this ever
            # fires, the rule is not a threshold on the margin any more and the
            # word "ceiling" in this file's title has stopped being true.
            assert state <= held + 1e-9, f"{log.name}: state {state} > held {held}"
    except Skip:
        pass                            # no references; the ceiling still stands

    return (labels, auc(pos, neg), within, best_cut(pos, neg), held, state,
            len(pos) + len(neg))


def verdict(sep, within, held, state, pair_best, runs):
    """The reading, in one phrase. Relative to the bench, never to a constant."""
    f, deficit = fold(sep), pair_best - fold(sep)
    if math.isnan(state) or math.isnan(held):
        return "ceiling intact; the rule never engaged, so nothing to compare"
    if state >= 0.85:
        return "the pair works"
    # THE PRIMARY SPLIT, and it is a subtraction rather than a threshold: how
    # much of what was on offer did the rule fail to collect? Everything else
    # here is context for that one number. `held` and not `best`, so that the
    # oracle and the rule are scored on the same frames - see ceiling().
    lost = round(100 * (held - state), 1)
    if lost >= LOST_19:
        why = ("and the scene under-showed it too" if deficit > NOTABLE
               else "on a scene that showed it")
        return f"lost {lost:.0f} points the ceiling allowed, {why} - #19"
    if within - f > NOTABLE:
        # This line used to end "- the staging moved", which is one cause of the
        # gap and was the wrong one on the 2026-08-20 glass runs: all four
        # archived visits were rendered and the glass had not been touched
        # between them. An unmoved scene's embedding drifts over a run as well,
        # and this column cannot tell the two apart - it is computed from the
        # margins and never sees a pixel. So it now reports what it measured and
        # leaves the cause to the frames.
        return (f"separates within a visit ({within:.3f}) and not across them "
                f"({f:.3f}) - render the visits before naming a cause")
    if deficit > NOTABLE:
        return (f"the rule collected the ceiling; the scene did not show it "
                f"this run - this pair has reached {pair_best:.3f}")
    if runs == 1:
        return ("the rule collected the ceiling; one run, so the ceiling "
                "itself is unproven - run it again")
    return (f"the rule collected the ceiling; this run's own {f:.3f} is what "
            f"there was to collect")


def main() -> int:
    logs = [Path(a) for a in sys.argv[1:]]
    if not logs:
        print(__doc__.strip().splitlines()[2].strip())
        return 2

    rows = []
    for log in logs:
        try:
            rows.append((log.name, *ceiling(log)))
        except (Skip, SystemExit) as why:
            print(f"{log.name:<42} skipped: {why}")
    if not rows:
        print("\nnothing with a two-class margin")
        return 1

    # The best any run of the same pair reached, which is what turns a low
    # ceiling from a fact about the model into a fact about the morning.
    pair_best, pair_runs = {}, {}
    for _n, labels, sep, *_ in rows:
        key = tuple(labels)
        pair_best[key] = max(pair_best.get(key, 0.0), fold(sep))
        pair_runs[key] = pair_runs.get(key, 0) + 1

    rows.sort(key=lambda r: -fold(r[2]))
    print(f"\n{'bench':<34}{'sep AUC':>9}{'|sep|':>7}{'within':>8}{'best':>7}"
          f"{'held':>7}{'state':>7}{'lost':>7}{'n':>6}  the pair")
    for name, labels, sep, within, cut, held, state, n in rows:
        s = f"{100 * state:5.1f}%" if not math.isnan(state) else "    - "
        h = f"{100 * held:5.1f}%" if not math.isnan(held) else "    - "
        lost = (f"{100 * (held - state):5.1f}"
                if not (math.isnan(state) or math.isnan(held)) else "    -")
        w = f"{within:6.3f}" if not math.isnan(within) else "     -"
        print(f"{name[:34]:<34}{sep:8.3f}{'!' if sep < 0.5 else ' '}"
              f"{fold(sep):7.3f}{w}{100 * cut:6.1f}%{h:>7}{s:>7}{lost:>7}{n:6d}  "
              f"{labels[0]} / {labels[1]}")
    print("  `!` is an INVERTED margin: separable, and the phrases name it "
          "backwards. Costs the\n  shipped rule nothing. `best` is the best any "
          "threshold on the margin could do over\n  every cued frame; `held` is "
          "that same oracle over only the frames the rule was\n  scored on, so "
          "`lost` = held - state is WHAT THE DECISION RULE COST on like for\n  "
          "like, and it is the column to read. It cannot go negative.")

    print("\nreading, per bench")
    for name, labels, sep, within, _cut, held, state, _n in rows:
        key = tuple(labels)
        print(f"  {name[:34]:<34}"
              f"{verdict(sep, within, held, state, pair_best[key], pair_runs[key])}")

    print("\nby pair, best ceiling reached")
    for key in sorted(pair_best, key=lambda k: -pair_best[k]):
        print(f"  {key[0] + ' / ' + key[1]:<44}{pair_best[key]:.3f}   "
              f"over {pair_runs[key]} run{'' if pair_runs[key] == 1 else 's'}")
    print("  A pair with one run and a low best is not evidence about the "
          "encoder yet. A pair\n  with many runs and a high best has no encoder "
          "problem, whatever its worst run did.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
