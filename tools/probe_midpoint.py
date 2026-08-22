#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Why did the four #19 benches throw away a ceiling they had?

    uv run --script tools/probe_midpoint.py bench/cue/m9_cue-2026081*.log

`tools/probe_ceiling.py` narrowed #19 from "a regression since 08-11" to four
benches that lost 15 to 72 points of a margin their own scenes showed. It could
not say why, because `lost` is a difference of two accuracies and an accuracy
does not have a direction. This does, and the reason it can is arithmetic that
probe_ceiling.py already states in prose:

**WITH TWO QUERIES THE SHIPPED RULE IS A THRESHOLD, AND ITS VALUE IS DECIDED
ENTIRELY AT ENROLMENT.** The centred space is `c = [+D/2, -D/2]` for the margin
`D = z[A] - z[B]`, so nearest-reference is `D > (D_refA + D_refB) / 2` - one
number, the midpoint of the two references on the margin axis. The oracle is the
best cut on that same axis. So `lost` is not a property of the rule's shape at
all. It is the distance between two cuts, and the whole of #19 is the question
of what put the rule's cut in the wrong place.

That distance splits in two, exactly and with nothing left over:

    t_rule - t_best  =  (t_rule - t_mid)  +  (t_mid - t_best)
                             ENROL             ASYMMETRY

where `t_mid` is the midpoint of the two classes' held-out means - the cut the
references would have produced had they landed on the middle of the frames they
were meant to represent.

  ENROL      the references are not where the held-out frames are. The scene
             moved between being taught and being tested, or the six enrolment
             frames were not the middle of their own class.
  ASYMMETRY  even perfect references cut at the midpoint of the means, and that
             is not the best cut when the two classes have different spreads.
             This term is the price of the rule's shape and no enrolment fixes
             it.

Both are quoted in `s`, the pooled within-class standard deviation of the
held-out margins, so a bench with a wide margin axis and a bench with a narrow
one can be read on one page.

`dA` and `dB` split ENROL again, per reference: how far each reference sits from
its own class's held-out mean, in the direction that hurts. **A run where one is
large and the other is small is one object that moved. A run where both are
large and share a sign is the whole scene, or the encoder, drifting under both.**
Those are different bugs with different fixes and #19 has been carrying them in
one issue.

THE CHECK THAT MAKES THIS QUOTABLE. If the reduction above is right, scoring the
held-out frames against the single threshold `t_rule` must reproduce
probe_ceiling.py's `state` exactly, on every bench. That is asserted per bench
and printed as `=state`. A mismatch means the rule is not the threshold this
script thinks it is, and every number here is void.

WHAT IT IS NOT. Not a guard. `t_best`, `t_mid` and `s` all need held-out frames
of both classes, so none of them exists until the run is over - the same reason
probe_ceiling.py is a reading and not a check. There is no constant here and
nothing for firmware/m9.c to do with it.

WHAT IT SAID ON 2026-08-23, over the twenty-five two-query benches
-------------------------------------------------------------------

**#19 IS TWO BUGS, AND THE BIGGER ONE IS NOT A THRESHOLD PROBLEM.** Four benches
enrolled BACKWARDS - the references name the classes in the order the held-out
frames contradict - and on #19's two largest that is most of the loss:

    bench          lost   SWAP    cut
    08-17 08:55    71.7   47.5   24.2   <- backwards
    08-17 10:52    47.9   31.2   16.7   <- backwards
    08-17 13:35    36.7    0.0   36.7
    08-16 17:22    29.2    0.0   29.2
    08-17 10:48    25.0    0.0   25.0

No threshold placed anywhere on the axis recovers a backwards enrolment; it is
the failure #19 opened with (`a closed book` at AUC 0.954 with the sign
inverted) and it turns out to recur. `bench/README.md` has the full table.

**AND THE DISPLACEMENT IS A STEP, NOT A SLIDE.** `drift` correlates with ENROL
at -0.937, which is only the decomposition being consistent with itself - both
are built from the same frames. Split the slope across the taught/held-out
boundary, where the two halves share no frame, and it collapses: sign agreement
12 of 25 against a chance of 12.5, r = +0.201. **The margin axis does not slide
steadily through a run.** So the enrolment error is an offset between visits and
not a trend within them, and re-enrolling later in the same run - the obvious
fix, and one that would cost firmware - would not have helped.

**WHAT IS STILL OPEN.** Whether that step is the whole scene or one object.
Both references are displaced the same way on 7 of the 9 worst benches against a
60% base rate, which is a lean and not a finding. That is issue #22, and it
needs a run designed to answer it rather than more replay of these.
"""
import itertools
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_ceiling import best_cut, ceiling  # noqa: F401  (import guard below)
from probe_reject import BASELINE, EMPTY, Skip, centred, dist, load, references


def cut_acc(pos: list[float], neg: list[float], t: float) -> float:
    """Accuracy of `D > t means class A`, the orientation the rule uses."""
    return (sum(p > t for p in pos) + sum(n <= t for n in neg)) / (len(pos) + len(neg))


def best_t(pos: list[float], neg: list[float]) -> float:
    """The cut the oracle would choose, in the same orientation."""
    xs = sorted(set(pos + neg))
    cuts = [xs[0] - 1.0] + [(x + y) / 2 for x, y in itertools.pairwise(xs)]
    return max(cuts, key=lambda c: cut_acc(pos, neg, c))


def read(log: Path) -> dict:
    """One bench, decomposed. Raises Skip for anything not a two-query bench."""
    spans, frames, enrol, window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    labels = sorted({lab for _a, _b, lab in spans if lab not in (EMPTY, BASELINE)})
    if len(labels) != 2 or len(names) != 2 or set(labels) != set(names):
        raise Skip("not a two-query bench with the cued labels as the queries")
    a_lab, b_lab = labels

    def margin(i: int) -> float:
        z = frames[i][0]
        return z[a_lab] - z[b_lab]

    refs, ref_lab, _nv, _sc, _sep, engage = references(
        spans, frames, enrol, window, names)
    keys = sorted(refs)
    if len({ref_lab[k] for k in keys}) != 2:
        raise Skip("the references do not name the two cued classes")

    # Each reference back on the margin axis. names[0] is a_lab because both
    # lists are sorted and asserted equal above, so c[0] = +D/2 and D = 2*c[0].
    dref = {ref_lab[k]: 2 * refs[k][0] for k in keys}

    # WHICH WAY ROUND THE RULE READS THE AXIS IS SET BY THE ENROLMENT, not by
    # the phrases. Nearest-reference calls a frame A when it is nearer ref A, so
    # the direction is the sign of (D_refA - D_refB) and nothing else - which is
    # exactly why probe_ceiling.py can say an inverted margin costs the shipped
    # rule nothing. Work on `u`, the margin turned so that ref A is always the
    # high one; then `u > t means A` is true on every bench and one orientation
    # serves the rule, the oracle and the signs of dA and dB alike.
    #
    # An earlier version of this script hardcoded the upright orientation, and
    # the `=state` check below refused six benches until this was here. It was
    # right to: on those, `lost` came out negative, which is the rule beating
    # the ceiling and is impossible.
    orient = 1.0 if dref[a_lab] >= dref[b_lab] else -1.0
    # THE HELD-OUT POPULATION, character for character as probe_ceiling.py
    # builds it, so `state` here and `state` there are the same number.
    taught = {(a, b) for a, b, _ in spans
              if any(a - 1 <= f + 2 <= b for f, k in enrol if k != "0")}
    pos: list[float] = []
    neg: list[float] = []
    ok = n = 0
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
            (pos if lab == a_lab else neg).append(orient * margin(i))
    if not pos or not neg:
        raise Skip("no held-out frames of both classes after the rule engaged")

    t_rule = orient * (dref[a_lab] + dref[b_lab]) / 2
    m_pos, m_neg = st.mean(pos), st.mean(neg)
    t_mid = (m_pos + m_neg) / 2
    t_bst = best_t(pos, neg)

    # Pooled within-class SD - the unit. A class that never moved would give
    # zero and make every ratio infinite, so single-frame classes are refused.
    if len(pos) < 2 or len(neg) < 2:
        raise Skip("a class with fewer than two held-out frames has no spread")
    s = ((st.pstdev(pos) ** 2 * len(pos) + st.pstdev(neg) ** 2 * len(neg))
         / (len(pos) + len(neg))) ** 0.5
    if s == 0:
        raise Skip("the held-out margins have no spread")

    # THE ORACLE TWICE, and the difference between them is the third term.
    # `held` is restricted to the direction the enrolment chose, so it is the
    # best the shipped rule could have done with its references pointing the way
    # they do. `free` is probe_ceiling.py's oracle, which may cut the other way.
    # On a bench where the enrolment picked the direction the frames disagree
    # with, `free` is far higher, and probe_ceiling.py's `lost` silently
    # contains that gap on top of the misplaced cut.
    held = cut_acc(pos, neg, t_bst)
    free = max(held, 1.0 - cut_acc(pos, neg, best_t(neg, pos)))

    # DOES THE AXIS ITSELF MOVE DURING A RUN? The references come out of the
    # taught visits and are scored against the other ones, so a margin that
    # slides monotonically would put both references on the early side of both
    # classes at once - which is what ENROL measures and dA, dB attribute. Take
    # every cued frame of either class, subtract that class's own mean so the
    # between-class signal is gone, and regress what is left on the frame index.
    # What survives is common to both classes and can only be the run.
    #
    # MEASURED TWICE, ON DISJOINT FRAMES, because ENROL is built from the taught
    # frames and the held-out ones together and a slope over all of them would
    # be partly the same arithmetic read back. `dr_t` uses only the spans the
    # references came from and `dr_h` only the spans the rule was scored on.
    # They share no frame, so their agreeing is a fact about the run and not
    # about this decomposition.
    def slope_over(keep) -> float:
        f = [(i, orient * margin(i), lab)
             for a, b, lab in spans if lab not in (EMPTY, BASELINE) and keep(a, b)
             for i in range(a, b + 1) if i in frames]
        if len({la for _i, _u, la in f}) < 2:
            return float("nan")
        cm = {lab: st.mean(u for _i, u, la in f if la == lab)
              for lab in (a_lab, b_lab)}
        xs = [i for i, _u, _la in f]
        ys = [u - cm[la] for _i, u, la in f]
        mx, my = st.mean(xs), st.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        return (sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
                / sxx) if sxx else 0.0

    slope = slope_over(lambda a, b: True)
    slope_t = slope_over(lambda a, b: (a, b) in taught)
    slope_h = slope_over(lambda a, b: (a, b) not in taught)

    # Signed so that positive always means "displaced the way that costs
    # accuracy": ref A too low, or ref B too high, both drag the cut off the
    # side of A's frames and lose A.
    return {
        "name": log.stem.replace("m9_cue-", ""),
        "state": ok / n, "held": held, "free": free, "n": n,
        "t_rule": t_rule, "t_mid": t_mid, "t_best": t_bst, "s": s,
        "enrol": (t_rule - t_mid) / s, "asym": (t_mid - t_bst) / s,
        "dA": (m_pos - orient * dref[a_lab]) / s,
        "dB": (orient * dref[b_lab] - m_neg) / s,
        "check": cut_acc(pos, neg, t_rule),
        # In `s` per hundred frames, so it is the same unit as ENROL and can be
        # read against it: a run 300 frames long with drift 1.0 has moved the
        # axis by about 3 s from end to end.
        "drift": 100 * slope / s,
        "dr_t": 100 * slope_t / s, "dr_h": 100 * slope_h / s,
        # The enrolment chose a direction the held-out frames contradict: class
        # A's frames sit BELOW class B's on the axis the rule reads as "more A".
        # This is #19's founding observation - 08-17 08:55 called 46 of 66
        # closed-book frames opened - and it is not a misplaced cut at all.
        "swap": m_pos <= m_neg,
        "pair": f"{a_lab} / {b_lab}",
    }


def main(paths: list[str]) -> int:
    rows, bad = [], []
    for p in paths:
        try:
            rows.append(read(Path(p)))
        except (Skip, SystemExit) as e:
            bad.append(f"  {Path(p).name:<42} skipped: {e}")
    if bad:
        print("\n".join(bad) + "\n")
    if not rows:
        sys.exit("no bench decomposed")
    rows.sort(key=lambda r: -(r["free"] - r["state"]))

    print(f"{'bench':<16}{'lost':>6}{'SWAP':>6}{'cut':>6}{'=state':>8}"
          f"{'ENROL':>8}{'ASYM':>7}{'dA':>7}{'dB':>7}{'drift':>7}{'s':>7}  pair")
    voids = 0
    for r in rows:
        agree = abs(r["check"] - r["state"]) < 5e-4
        voids += not agree
        print(f"{r['name']:<16}{100 * (r['free'] - r['state']):>6.1f}"
              f"{100 * (r['free'] - r['held']):>6.1f}"
              f"{100 * (r['held'] - r['state']):>6.1f}"
              f"{'ok' if agree else 'VOID':>8}"
              f"{r['enrol']:>8.2f}{r['asym']:>7.2f}{r['dA']:>7.2f}"
              f"{r['dB']:>7.2f}{r['drift']:>7.2f}{r['s']:>7.2f}"
              f"  {'BACKWARDS  ' if r['swap'] else ''}{r['pair']}")
    print("\n  lost   = probe_ceiling.py's column: its oracle minus the rule, "
          "in points.\n"
          "  SWAP   = of those points, the ones lost to the enrolment choosing "
          "the direction\n           the held-out frames disagree with. Not a "
          "misplaced cut - a backwards one.\n"
          "  cut    = the rest: the best cut in the rule's own direction, minus "
          "the rule.\n"
          "           lost = SWAP + cut, exactly.\n"
          "  =state = scoring the held-out frames against the single threshold "
          "t_rule\n           reproduces the rule's own accuracy. If this is "
          "not `ok` everywhere,\n           the reduction is wrong and the rest "
          "of the table means nothing.\n"
          "  ENROL  = (t_rule - t_mid)/s, the references not being where the "
          "frames are.\n"
          "  ASYM   = (t_mid - t_best)/s, the price of cutting at the midpoint "
          "of the means.\n"
          "           ENROL + ASYM is the whole distance from the rule's cut to "
          "the best one\n           in its own direction, and it is what `cut` "
          "is made of.\n"
          "  dA, dB = each reference's own displacement, signed so positive "
          "costs accuracy.\n"
          "  drift  = the margin axis's own slope over the run, in s per 100 "
          "frames, after\n           each class's mean is removed - so what is "
          "left is shared by both classes\n           and is the run rather "
          "than the objects.\n"
          "  s      = pooled within-class SD of the held-out margins, the unit "
          "for all of them.")
    if voids:
        sys.exit(f"\n{voids} bench(es) VOID - the rule is not the threshold "
                 f"this script models")

    def pearson(xs: list[float], ys: list[float]) -> float:
        mx, my = st.mean(xs), st.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
        den = (sum((x - mx) ** 2 for x in xs)
               * sum((y - my) ** 2 for y in ys)) ** 0.5
        return num / den if den else float("nan")

    sw = [r for r in rows if r["swap"]]
    print(f"\n  {len(sw)} of {len(rows)} benches enrolled BACKWARDS"
          + (": " + ", ".join(r["name"] for r in sw) if sw else "")
          + "\n  On those, SWAP is a labelling failure on a signal that was "
            "there, and no\n  threshold placed anywhere on the axis would have "
            "recovered it.")

    # THE MECHANISM CHECK. If ENROL is the run drifting under the references
    # rather than the objects being restaged, then a run whose axis rises must
    # leave its references BELOW the later frames, and ENROL must fall as drift
    # rises. That is one sign, predicted before it was measured, and it is the
    # difference between "the enrolment was unlucky" and "the enrolment was
    # early".
    r = pearson([x["drift"] for x in rows], [x["enrol"] for x in rows])
    print(f"\n  drift vs ENROL: r = {r:+.3f} over {len(rows)} benches"
          f"\n  Predicted negative: an axis rising during the run leaves both "
          f"references\n  below the frames they are later scored against. "
          f"ENROL is built from both\n  halves of the run, so this is the "
          f"decomposition being consistent, not evidence.")

    both = [x for x in rows if x["dr_t"] == x["dr_t"] and x["dr_h"] == x["dr_h"]]
    if len(both) > 2:
        rr = pearson([x["dr_t"] for x in both], [x["dr_h"] for x in both])
        agree = sum((x["dr_t"] > 0) == (x["dr_h"] > 0) for x in both)
        print(f"\n  AND THE SLOPE DOES NOT SURVIVE BEING SPLIT. Measured on the "
              f"taught spans and\n  on the held-out ones - disjoint frames, "
              f"nothing shared - it agrees in sign on\n  {agree} of "
              f"{len(both)}, r = {rr:+.3f}. Chance is {len(both) / 2:.1f}. So "
              f"the axis is NOT sliding\n  steadily through a run: the "
              f"displacement is a step between visits, not a trend\n  within "
              f"them, and re-enrolling later in the same run would not have "
              f"fixed it.")

    same = [x for x in rows if (x["dA"] > 0) != (x["dB"] > 0)]
    big = [x for x in rows if 100 * (x["held"] - x["state"]) >= 10.0]
    bs = sum((x["dA"] > 0) != (x["dB"] > 0) for x in big)
    print(f"\n  WHICH REFERENCE MOVED, and this one is NOT settled: both "
          f"displaced the same way\n  on {len(same)} of {len(rows)} benches, "
          f"and on {bs} of the {len(big)} that lost 10 points or more to "
          f"`cut`.\n  {bs}/{len(big)} against a {100 * len(same) / len(rows):.0f}% "
          f"base rate is a lean, not a finding - too few benches to\n  "
          f"separate 'the whole scene sat differently that visit' from 'one "
          f"object was\n  restaged'. That separation is issue #22 and it needs "
          f"a run designed for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
