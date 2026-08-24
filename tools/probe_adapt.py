#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Would a threshold that follows the scene recover what #19 throws away?

    uv run --script tools/probe_adapt.py bench/cue/*.log

WHERE THIS COMES FROM. `tools/probe_midpoint.py` reduced the shipped rule to one
number - with two queries, nearest-reference IS `u > t_rule`, and `t_rule` is
fixed at enrolment as the midpoint of the two references on the margin axis. It
then split `lost` into SWAP (the enrolment named the direction backwards, which
no threshold recovers) and `cut` (the threshold is in the wrong place), and
`cut` into ENROL and ASYM.

On 2026-08-25 five identical runs of the book pair were taken twenty-five
minutes apart. Four of them lost 0.0 to 9.1 points. The first lost 20.8, all of
it `cut`, ENROL -2.13 against a -0.62..+0.48 spread on its four siblings - and
its margin AUC was 0.962 with a 93.3% ceiling, the best of the session. Reading
the spans of that run:

    a closed book    +5.99   +8.13  +11.57   +9.48
    an opened book   -1.01   -1.17   +3.21   +2.05
    empty            -3.02   -0.59   +1.02   +1.01

Both classes AND THE EMPTY DESK translated about +4 up the axis after the
references were taken, and the gap between the classes barely moved (4.08 early,
3.94 late). The empty span has no book in it, so whatever moved is not the book,
the pose or the staging. The separation survived intact and the threshold did
not follow it.

WHAT THIS SCRIPT ASKS. If that is what happens, then subtracting a running
estimate of where the scene sits should put the cut back under it:

    t_n  =  t_rule + (c_n - c_enrol)

`c_n` is an exponential moving average of the margin over the frames seen SO
FAR, `c_enrol` is that same average frozen at the moment the rule engaged, and
the correction is the DIFFERENCE of the two. The difference is what makes this
implementable. `c_n` on its own is a bad threshold - it is a mixture of both
classes and the empty desk in whatever proportion the schedule happens to use -
but any constant bias from that mixture cancels in `c_n - c_enrol`, leaving the
part that moved.

CAUSAL, because firmware has no choice. The average is updated AFTER the frame
is classified, so no frame contributes to the threshold that judges it, and no
frame after `n` is visible at `n`. Nothing here needs the run to be over, which
is the difference between this and every other probe in this directory:
`probe_ceiling.py` and `probe_midpoint.py` are readings, and this is a proposal.

TAU IS NOT FITTED. It is 120 frames, which is one full rotation of the standard
schedule - `a closed book`, `an opened book`, `empty`, forty frames each - so
the average covers a whole cycle and the mixture proportion it sees is stable.
That number comes from host/cue.py, not from this data. The sweep at the bottom
is printed as sensitivity and the best row in it is NOT the headline; picking
tau from the sweep would be fitting a constant to twenty-odd benches, which is
the thing this repository keeps refusing to do.

THE ARMS, and the third one is the one that keeps this honest.

    rule     the shipped fixed cut. Must reproduce probe_ceiling.py's `state`
             on every bench or the whole table is void, same check as
             probe_midpoint.py's `=state`.
    adapt    t_rule + (c_n - c_enrol), the proposal.
    flip     t_rule - (c_n - c_enrol), the same correction with the sign
             reversed. IF FLIP ALSO WINS, THE WIN IS NOT THE CORRECTION - it is
             a threshold that wanders being better than one that sits still,
             and that would be a fact about the axis and not a fix.
    oracle   the best cut on the held-out frames, which is probe_midpoint.py's
             `held`. The ceiling `adapt` is trying to reach.

`recov` is (adapt - rule) / (oracle - rule): how much of what the rule threw
away came back. It is blank where the rule already had everything.

WHAT IT CANNOT DO. Nothing here touches SWAP. A run that enrolled backwards has
its references naming the classes in the order the frames contradict, and moving
the cut along the axis cannot undo that at any speed.

WHAT IT SAID ON 2026-08-25, over the thirty-two two-query benches
-------------------------------------------------------------------

**THE CORRECTION POINTS THE RIGHT WAY, IS AIMED AT THE RIGHT BENCHES, AND DOES
NOT CLEAR. It is not a fix and should not be built.**

    rule    74.7%      adapt   76.4%      flip  70.5%
    empty   73.8%      oracle  83.4%

    per bench, adapt - rule: mean +1.7, sd 9.3, t = 1.00 on 31 df
    16 up, 11 down, 5 unmoved; best +33.3, worst -23.3

+1.7 points with a standard deviation of 9.3 is not a result. What keeps this
file in the repository is the three things around it.

**THE SIGN MATTERS, so this is not a wandering threshold beating a still one.**
`flip` - the same correction, negated - loses 4.2 points and wins on 6 benches
of 32. Had the two arms been alike, the whole exercise would have measured
nothing but the axis being easier to hit from a moving cut.

**IT IS AIMED CORRECTLY.** Split by whether the bench threw its ceiling away:

    11 benches that lost 10 points or more    adapt  +6.5   empty  +3.7
    21 benches that kept most of theirs       adapt  -0.9   empty  -3.2

It helps exactly the benches #19 is about and is roughly free on the rest - but
"roughly free" hides `20260817-085747`, which goes 76.7% to 53.3%. A correction
that can cost a healthy bench 23 points is not shippable at +6.5 average on the
sick ones.

**AND PART OF THE SHIFT IS IN THE EMPTY DESK.** The `empty` arm - an average
that never sees a frame with an object in it - recovers +3.7 on the collapsed
group. Whatever moves is therefore not only the book: it moves the bare scene
too. That is the same thing the 2026-08-25 run 1 spans showed directly
(`empty` walked -3.02, -0.59, +1.02, +1.01 while both classes walked with it),
now measured across the archive instead of read off one run.

**THE TWO BENCHES #19 WAS OPENED ON BOTH RECOVER.** 08-16 17:22 goes 58.3% to
91.7%, past its own 87.5% fixed-cut ceiling, and 08-17 13:35 goes 57.5% to
74.2%. So the collapse those runs recorded is a threshold sitting still under a
scene that moved, and it is recoverable in replay. That is a diagnosis. It is
not yet a fix, because the estimator that recovers them is an average over all
frames, and an average over all frames is only a common-mode estimate WHILE THE
SCHEDULE KEEPS THE TWO CLASSES BALANCED. A deployed appliance pointed at one
scene for an hour would drive `c_n` to that scene's own level and turn the rule
into "is this frame above the recent average", which is not a classifier. The
arm that does not have that flaw is `empty`, and it needs a presence stage that
works - 0 of 90 on every recent bench, issues #18 and #21.
"""
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_midpoint import best_t, cut_acc
from probe_reject import BASELINE, EMPTY, Skip, load, references

# One full rotation of the standard cue schedule: three spans of forty frames.
# From host/cue.py's schedule, not from these logs. See the docstring.
TAU = 120.0
# The sweep is sensitivity only. The headline row is TAU.
SWEEP = (30.0, 60.0, 120.0, 240.0, 480.0)


def read(log: Path, taus: tuple[float, ...]) -> dict:
    """One bench, scored by the fixed rule and by a threshold that follows."""
    # REFUSED IN CODE, not in the docstring, and this tool is stricter than
    # probe_ceiling.py on purpose. That one prints a reading per bench, where a
    # doctored copy is a demonstration; this one pools 34 benches into a mean
    # and a t-test, and `m9_cue_fake_d.log` is a copy of 08-16 17:22 - the
    # single largest winner below. Counting it twice would put a duplicate of
    # the best result into a paired test of whether the result exists.
    if "fake" in log.stem or "smoke" in log.stem:
        raise Skip("synthetic or a smoke test; a pooled mean must not have it")
    spans, frames, enrol, window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    labels = sorted({lab for _a, _b, lab in spans if lab not in (EMPTY, BASELINE)})
    if len(labels) != 2 or len(names) != 2 or set(labels) != set(names):
        raise Skip("not a two-query bench with the cued labels as the queries")
    a_lab, b_lab = labels

    refs, ref_lab, _nv, _sc, _sep, engage = references(
        spans, frames, enrol, window, names)
    keys = sorted(refs)
    if len({ref_lab[k] for k in keys}) != 2:
        raise Skip("the references do not name the two cued classes")

    dref = {ref_lab[k]: 2 * refs[k][0] for k in keys}
    orient = 1.0 if dref[a_lab] >= dref[b_lab] else -1.0
    t_rule = orient * (dref[a_lab] + dref[b_lab]) / 2

    def u_of(i: int) -> float:
        z = frames[i][0]
        return orient * (z[a_lab] - z[b_lab])

    # The held-out population, built exactly as probe_midpoint.py builds it.
    taught = {(a, b) for a, b, _ in spans
              if any(a - 1 <= f + 2 <= b for f, k in enrol if k != "0")}
    scored: dict[int, str] = {}
    for a, b, lab in spans:
        if lab in (EMPTY, BASELINE) or (a, b) in taught:
            continue
        for i in range(a, b + 1):
            if i in frames and i >= engage:
                scored[i] = lab
    if len(set(scored.values())) < 2:
        raise Skip("no held-out frames of both classes after the rule engaged")

    # THE CONTROL ARM'S INPUT. Frames inside an `empty` or `baseline` span - no
    # book, no hand, no glass, nothing either query names. An average over only
    # these CANNOT KNOW WHICH CLASS IS ON SCREEN, so it cannot win by tracking
    # one, and that is the whole reason it is here.
    quiet = {i for a, b, lab in spans if lab in (EMPTY, BASELINE)
             for i in range(a, b + 1) if i in frames}

    # ONE CAUSAL PASS over every frame the camera produced, in order - empty
    # spans, baseline and taught visits included, because a running average in
    # the field sees all of them and excluding any would make this unbuildable.
    order = sorted(frames)
    ema = dict.fromkeys(taus)
    bgm = dict.fromkeys(taus)
    frozen: dict[float, float] = {}
    frozen_bg: dict[float, float] = {}
    # tau -> [adapt correct, flip correct, empty-only correct]
    hits = {t: [0, 0, 0] for t in taus}
    rule_ok = 0
    last_q = order[0]
    pos: list[float] = []
    neg: list[float] = []
    for i in order:
        u = u_of(i)
        # Freeze the reference level at the moment the rule engaged. Before
        # that the average is still warming up on the baseline and the taught
        # visits, which is exactly what firmware would have at that instant.
        if i >= engage:
            for t in taus:
                frozen.setdefault(t, ema[t] if ema[t] is not None else u)
                frozen_bg.setdefault(t, bgm[t] if bgm[t] is not None else u)
        if i in scored:
            lab = scored[i]
            want = lab == a_lab
            rule_ok += (u > t_rule) == want
            (pos if want else neg).append(u)
            for t in taus:
                shift = (ema[t] if ema[t] is not None else u) - frozen[t]
                qs = (bgm[t] if bgm[t] is not None else u) - frozen_bg[t]
                hits[t][0] += (u > t_rule + shift) == want
                hits[t][1] += (u > t_rule - shift) == want
                hits[t][2] += (u > t_rule + qs) == want
        # AFTER the decision, never before: no frame may inform the threshold
        # that judges it.
        for t in taus:
            ema[t] = u if ema[t] is None else ema[t] + (u - ema[t]) / t
        if i in quiet:
            # THE CONTROL'S AVERAGE IS ADVANCED BY ELAPSED FRAMES, not by empty
            # frames. Empty spans are about a fifth of a run, so stepping this
            # once per empty frame would give it an effective time constant five
            # times longer than the arm it is controlling for - it would sit
            # still, lose, and the loss would be the calibration and not the
            # question. `gap` is how many frames have passed since the last
            # empty one, and `1 - exp(-gap/tau)` is the same exponential the
            # other arm gets, sampled where the desk is clear.
            for t in taus:
                a = 1.0 - 2.718281828459045 ** (-(i - last_q) / t)
                bgm[t] = u if bgm[t] is None else bgm[t] + a * (u - bgm[t])
            last_q = i

    n = len(scored)
    t_bst = best_t(pos, neg)
    return {
        "name": log.stem.replace("m9_cue-", ""),
        "n": n, "state": rule_ok / n, "held": cut_acc(pos, neg, t_bst),
        "adapt": {t: hits[t][0] / n for t in taus},
        "flip": {t: hits[t][1] / n for t in taus},
        "bg": {t: hits[t][2] / n for t in taus},
        "quiet": len(quiet),
        "pair": f"{a_lab} / {b_lab}",
        "swap": st.mean(pos) <= st.mean(neg),
    }


def main(paths: list[str]) -> int:
    taus = tuple(sorted({TAU, *SWEEP}))
    rows, bad = [], []
    for p in paths:
        try:
            rows.append(read(Path(p), taus))
        except (Skip, SystemExit) as e:
            bad.append(f"  {Path(p).name:<42} skipped: {e}")
    if bad:
        print("\n".join(bad) + "\n")
    if not rows:
        sys.exit("no bench scored")
    rows.sort(key=lambda r: -(r["held"] - r["state"]))

    print(f"tau = {TAU:.0f} frames, one rotation of the standard schedule\n")
    print(f"{'bench':<16}{'n':>5}{'rule':>8}{'adapt':>8}{'flip':>8}{'empty':>8}"
          f"{'oracle':>8}{'recov':>8}  pair")
    for r in rows:
        gap = r["held"] - r["state"]
        rec = (r["adapt"][TAU] - r["state"]) / gap if gap > 1e-9 else None
        print(f"{r['name']:<16}{r['n']:>5}{100 * r['state']:>7.1f}%"
              f"{100 * r['adapt'][TAU]:>7.1f}%{100 * r['flip'][TAU]:>7.1f}%"
              f"{100 * r['bg'][TAU]:>7.1f}%{100 * r['held']:>7.1f}%"
              f"{'' if rec is None else f'{100 * rec:>7.0f}%':>8}"
              f"  {'BACKWARDS  ' if r['swap'] else ''}{r['pair']}")

    nf = sum(r["n"] for r in rows)

    def tot(key: str, t: float) -> float:
        return sum(r[key][t] * r["n"] for r in rows) / nf

    base = sum(r["state"] * r["n"] for r in rows) / nf
    ceil = sum(r["held"] * r["n"] for r in rows) / nf
    print(f"\n  pooled over {len(rows)} benches and {nf} held-out frames:")
    print(f"    rule   {100 * base:5.1f}%   the shipped fixed cut\n"
          f"    adapt  {100 * tot('adapt', TAU):5.1f}%   "
          f"the correction\n"
          f"    flip   {100 * tot('flip', TAU):5.1f}%   the correction, sign "
          f"reversed\n"
          f"    empty  {100 * tot('bg', TAU):5.1f}%   the correction, estimated "
          f"from empty spans only\n"
          f"    oracle {100 * ceil:5.1f}%   the best fixed cut, known only "
          f"afterwards")

    # THE POOLED NUMBER HIDES THE SHAPE, so print the shape. A correction that
    # gains three points on average by gaining twenty on some benches and losing
    # twenty on others is not a fix, and the pooled row cannot say which it is.
    d = sorted(100 * (r["adapt"][TAU] - r["state"]) for r in rows)
    print(f"\n  per bench, adapt - rule: mean {st.mean(d):+.1f}, "
          f"sd {st.stdev(d):.1f}, best {d[-1]:+.1f}, worst {d[0]:+.1f}, "
          f"median {st.median(d):+.1f}\n  moves by more than 5 points on "
          f"{sum(abs(x) > 5 for x in d)} of {len(d)} benches, "
          f"{sum(x < -5 for x in d)} of them downward.")

    # A PAIRED TEST, because 34 deltas with a standard deviation four times
    # their mean is what an effect that is not there also looks like.
    se = st.stdev(d) / len(d) ** 0.5
    print(f"  mean {st.mean(d):+.1f} against a standard error of {se:.1f} is "
          f"t = {st.mean(d) / se:.2f} on {len(d) - 1} df, which does not "
          f"clear.\n  Sign test: {sum(x > 0 for x in d)} up, "
          f"{sum(x < 0 for x in d)} down, {sum(x == 0 for x in d)} unmoved.")

    # AND THE SPLIT THAT MATTERS. The proposal is aimed at benches that threw a
    # ceiling away; it has nothing to offer one that already collected it, and
    # on those it can only do harm. Reported separately so the average of the
    # two cannot hide either.
    for name, keep in (("threw 10 points or more away",
                        lambda r: 100 * (r["held"] - r["state"]) >= 10.0),
                       ("kept most of their ceiling",
                        lambda r: 100 * (r["held"] - r["state"]) < 10.0)):
        g = [100 * (r["adapt"][TAU] - r["state"]) for r in rows if keep(r)]
        q = [100 * (r["bg"][TAU] - r["state"]) for r in rows if keep(r)]
        if len(g) > 1:
            print(f"    {len(g):>2} benches that {name:<30} adapt "
                  f"{st.mean(g):+6.1f}  sd {st.stdev(g):5.1f}  "
                  f"worst {min(g):+6.1f}   empty {st.mean(q):+6.1f}")

    win = sum(r["adapt"][TAU] > r["state"] for r in rows)
    lose = sum(r["adapt"][TAU] < r["state"] for r in rows)
    fwin = sum(r["flip"][TAU] > r["state"] for r in rows)
    qwin = sum(r["bg"][TAU] > r["state"] for r in rows)
    print(f"\n  adapt beats the rule on {win} of {len(rows)} benches and loses "
          f"on {lose}; flip on {fwin}.\n  **IF THOSE TWO COUNTS ARE ALIKE THE "
          f"CORRECTION IS NOT WHAT IS HELPING** - a threshold\n  that wanders "
          f"would be beating one that sits still, which is a fact about the "
          f"axis\n  and not a fix.")
    print(f"\n  THE ARM THAT CANNOT CHEAT: `empty` wins on {qwin} of "
          f"{len(rows)}. Its average is built\n  only from spans with nothing "
          f"in shot, so it has no way to know which class is\n  in front of the "
          f"camera and cannot win by tracking one. It is A CONTROL AND NOT A\n"
          f"  PROPOSAL: the appliance cannot currently tell an empty desk from "
          f"an occupied one\n  (0 of 90 on every recent bench - issues #18 and "
          f"#21), so nothing could compute it\n  online today.")

    print("\n  sensitivity to tau. NOT a menu - the headline is the row at "
          f"{TAU:.0f},\n  which comes from the schedule. Reading the best row "
          "off this sweep would be\n  fitting a constant to "
          f"{len(rows)} benches, which is the move this repository keeps\n  "
          "refusing to make.")
    print(f"    {'tau':>6}{'adapt':>9}{'flip':>9}{'empty':>9}{'won':>6}"
          f"{'lost':>6}")
    for t in taus:
        w = sum(r["adapt"][t] > r["state"] for r in rows)
        lo = sum(r["adapt"][t] < r["state"] for r in rows)
        print(f"    {t:>6.0f}{100 * tot('adapt', t):>8.1f}%"
              f"{100 * tot('flip', t):>8.1f}%{100 * tot('bg', t):>8.1f}%"
              f"{w:>6}{lo:>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
