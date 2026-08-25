#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Can the drift correction find its own quiet frames, with no schedule?

    uv run --script tools/probe_selfquiet.py bench/cue/*.log

WHERE THIS COMES FROM, AND WHY IT COULD NOT BE ASKED UNTIL TODAY.
`tools/probe_adapt.py` proposed following the scene with a running average and
correcting the cut by how far it has moved. Its headline arm - `adapt`, an
average over every frame - is not shippable and the file says so: pointed at one
scene for an hour it drives the average to that scene's own level and the rule
degenerates into "is this frame above the recent average", which is not a
classifier. That file's own conclusion names the way out:

    The arm that does not have that flaw is `empty`, and it needs a presence
    stage that works - 0 of 90 on every recent bench, issues #18 and #21.

An average taken only over frames with nothing in shot cannot track a class,
because it never sees one. It recovered +3.7 on the eleven benches that threw
their ceiling away. But that arm reads the CUE SIDECAR to know which frames are
empty, and an appliance on a desk has no sidecar. It was a measurement of what
the idea could be worth, not of anything that could be built.

On 2026-08-25 the board got a presence stage that works: `'0'` enrols the empty
scene as a third reference and a frame is absent when that reference is the
nearest of the three (issue #18, commit 80f2c85, 83.3% on the first bench). So
the appliance can now label its own quiet frames. THIS SCRIPT ASKS WHETHER THE
LABEL IS GOOD ENOUGH: it runs the same correction with `quiet` supplied by the
rule instead of by the schedule, and puts the two side by side.

    cued   the average over sidecar `empty`/`baseline` spans. probe_adapt.py's
           arm. AN ORACLE - it is what the correction is worth if the quiet
           frames are handed to it, and it is here as the ceiling `self` is
           trying to reach, not as a proposal.
    self   the average over the frames THE RULE ITSELF called absent. Nothing
           outside the appliance is read. This is the buildable one.

THERE IS NO FEEDBACK LOOP, and that is not luck. The absent test is
`argmin_k |D - D_ref[k]| is the empty reference` - a comparison between three
fixed reference positions on the margin axis. It does not involve `t_rule`, so
moving the cut cannot change which frames feed the average that moves the cut.
Had the presence stage been #18's band expressed in units of `sep`, or anything
else that shares a constant with the state stage, this script would be measuring
a control system and would need to say so.

CAUSAL, like the file it descends from. The average is updated AFTER the frame
is classified; no frame informs the threshold that judges it and no frame after
`n` is visible at `n`. Both averages are advanced by ELAPSED frames rather than
by contributing frames - `1 - exp(-gap/tau)` - because quiet frames are about a
fifth of a run and stepping once per quiet frame would give these arms a time
constant five times longer than `adapt`'s. That would make them sit still, lose,
and the loss would be the calibration rather than the answer.

THE POPULATION IS NOT probe_adapt.py'S, so do not read the two `rule` columns
against each other. The third reference does not exist until its window closes,
so nothing here can be scored before that: `engage` moves from "the last class
reference landed" to "the empty reference landed", which on the standard
schedule is a whole cycle later and costs about a third of the held-out frames.
That is a real cost of the rule and not an artefact of the script - the board
pays it too.

TAU IS 120 FRAMES, one rotation of the standard schedule, taken from
host/cue.py. It is not fitted here and there is no sweep, because probe_adapt.py
already published one and picking from it is how a constant gets fitted to
twenty-odd benches.

WHAT ELSE IS PRINTED. `leak` is the fraction of the frames `self` averaged over
that were actually class frames - the failure mode that would break it, since a
class frame in the "quiet" average is exactly the contamination the arm exists
to avoid. `miss` is the fraction of true quiet frames the rule declined to use;
that one only costs responsiveness.
"""
import statistics as st
import sys
from itertools import pairwise
from math import exp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_reject import BASELINE, EMPTY, Skip, load, references

TAU = 120.0             # frames, one rotation of host/cue.py's schedule
SETTLE = 10             # dropped off the enrolment span, as probe_third.py does


def cut_acc(pos, neg, t):
    return (sum(u > t for u in pos) + sum(u <= t for u in neg)) / (len(pos) + len(neg))


def best_t(pos, neg):
    xs = sorted(set(pos) | set(neg))
    cuts = [(a + b) / 2 for a, b in pairwise(xs)] or xs
    return max(cuts, key=lambda t: cut_acc(pos, neg, t))


def read(log: Path) -> dict:
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
    d_a, d_b = orient * dref[a_lab], orient * dref[b_lab]

    def u_of(i: int) -> float:
        z = frames[i][0]
        return orient * (z[a_lab] - z[b_lab])

    # THE THIRD REFERENCE, enrolled the way tools/probe_third.py enrols it and
    # the way host/cue.py now presses it: the first empty span after the class
    # rule engages, one window long, after SETTLE. `eng_e` is the frame it lands
    # on, and nothing before that frame can be scored by a rule that does not
    # exist yet.
    e_span = next(((a, b) for a, b, lab in spans
                   if lab == EMPTY and a + SETTLE >= engage), None)
    if e_span is None:
        raise Skip("no empty span after the class rule engaged; nothing to enrol")
    a_e, b_e = e_span
    win = [u_of(i) for i in range(a_e + SETTLE, b_e + 1) if i in frames][:window]
    if len(win) < window // 2:
        raise Skip("the first empty span is too short to enrol a reference from")
    d_empty = st.mean(win)
    eng_e = a_e + SETTLE + len(win)

    def absent(u: float) -> bool:
        return abs(u - d_empty) < min(abs(u - d_a), abs(u - d_b))

    # The held-out population, built as probe_adapt.py builds it and then cut
    # again at `eng_e`. The enrolment span itself is excluded on both counts.
    taught = {(a, b) for a, b, _ in spans
              if any(a - 1 <= f + 2 <= b for f, k in enrol if k != "0")}
    scored: dict[int, str] = {}
    for a, b, lab in spans:
        if lab in (EMPTY, BASELINE) or (a, b) in taught:
            continue
        for i in range(a, b + 1):
            if i in frames and i >= eng_e:
                scored[i] = lab
    if len(set(scored.values())) < 2:
        raise Skip("no held-out frames of both classes after the empty "
                   "reference landed")

    cued = {i for a, b, lab in spans if lab in (EMPTY, BASELINE)
            for i in range(a, b + 1) if i in frames}
    truly_quiet = cued

    order = sorted(frames)
    ema = bg_c = bg_s = None            # adapt, cued, self
    fz: dict[str, float] = {}
    last_c = last_s = order[0]
    hits = {"adapt": 0, "cued": 0, "self": 0}
    # HOW FAR EACH ARM EVER MOVED THE CUT, in the same units as `sep`. An arm
    # can be flat because the drift is not there to correct, or because it
    # cannot see it; these two look identical in an accuracy column and not in
    # this one.
    swing = {"adapt": 0.0, "cued": 0.0, "self": 0.0}
    rule_ok = 0
    pos: list[float] = []
    neg: list[float] = []
    used = 0                            # frames `self` averaged over
    leak = 0                            # ... of which were class frames
    seen_q = 0                          # true quiet frames after the ref lands
    took_q = 0                          # ... that the rule agreed were quiet
    for i in order:
        u = u_of(i)
        live = i >= eng_e
        if live:
            fz.setdefault("a", ema if ema is not None else u)
            fz.setdefault("c", bg_c if bg_c is not None else u)
            fz.setdefault("s", bg_s if bg_s is not None else u)
        if i in scored:
            want = scored[i] == a_lab
            rule_ok += (u > t_rule) == want
            (pos if want else neg).append(u)
            for tag, cur, key in (("adapt", ema, "a"), ("cued", bg_c, "c"),
                                  ("self", bg_s, "s")):
                shift = (cur if cur is not None else u) - fz[key]
                hits[tag] += (u > t_rule + shift) == want
                swing[tag] = max(swing[tag], abs(shift))
        # AFTER the decision, never before.
        ema = u if ema is None else ema + (u - ema) / TAU
        if i in cued:
            a = 1.0 - exp(-(i - last_c) / TAU)
            bg_c = u if bg_c is None else bg_c + a * (u - bg_c)
            last_c = i
        # THE SELF-LABELLED ARM. Only after the reference exists, because before
        # that the board has no absent call to make - which is also why `leak`
        # and `miss` are counted from `eng_e` and not from frame zero.
        if live:
            q = absent(u)
            if i in truly_quiet:
                seen_q += 1
                took_q += q
            if q:
                used += 1
                leak += i not in truly_quiet
                a = 1.0 - exp(-(i - last_s) / TAU)
                bg_s = u if bg_s is None else bg_s + a * (u - bg_s)
                last_s = i

    n = len(scored)
    return {
        "name": log.stem.replace("m9_cue-", ""),
        "n": n,
        "rule": 100 * rule_ok / n,
        "adapt": 100 * hits["adapt"] / n,
        "cued": 100 * hits["cued"] / n,
        "self": 100 * hits["self"] / n,
        "oracle": 100 * cut_acc(pos, neg, best_t(pos, neg)),
        "leak": 100 * leak / used if used else float("nan"),
        "miss": 100 * (1 - took_q / seen_q) if seen_q else float("nan"),
        "used": used,
        "sw_a": swing["adapt"],
        "sw_c": swing["cued"],
        "sw_s": swing["self"],
        # THE CEILING ON `self`'s SWING, and it is arithmetic, not empirical.
        # `absent` admits a frame only while `u` is nearer `d_empty` than either
        # class reference, so every frame the arm averages lies inside a cell of
        # this half-width around `d_empty`. The average of points in that cell
        # cannot leave it. Whatever drift is larger than `room` is invisible to
        # this arm BY CONSTRUCTION - it is not a tuning problem.
        "room": min(abs(d_empty - d_a), abs(d_empty - d_b)) / 2,
    }


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit("pass bench logs")
    rows, bad = [], []
    for p in argv:
        try:
            rows.append(read(Path(p)))
        except (Skip, SystemExit) as e:
            bad.append(f"  {Path(p).name:<42} skipped: {e}")
    if bad:
        print("\n".join(bad) + "\n")
    if not rows:
        raise SystemExit("no bench scored")

    rows.sort(key=lambda r: -(r["oracle"] - r["rule"]))
    print(f"tau = {TAU:.0f} frames; `cued` is an oracle, `self` is the "
          f"buildable one\n")
    print(f"{'bench':<18}{'n':>5}{'rule':>7}{'adapt':>7}{'cued':>7}{'self':>7}"
          f"{'oracle':>8}{'leak':>7}{'miss':>7}")
    for r in rows:
        print(f"{r['name']:<18}{r['n']:>5}{r['rule']:>7.1f}{r['adapt']:>7.1f}"
              f"{r['cued']:>7.1f}{r['self']:>7.1f}{r['oracle']:>8.1f}"
              f"{r['leak']:>7.1f}{r['miss']:>7.1f}")

    n = len(rows)
    m = {k: st.mean(r[k] for r in rows)
         for k in ("rule", "adapt", "cued", "self", "oracle")}
    print(f"\nn = {n}   mean over benches, held out")
    print(f"  rule    {m['rule']:5.1f}      adapt   {m['adapt']:5.1f}")
    print(f"  cued    {m['cued']:5.1f}      self    {m['self']:5.1f}"
          f"      oracle  {m['oracle']:5.1f}")

    for tag in ("cued", "self", "adapt"):
        d = [r[tag] - r["rule"] for r in rows]
        sd = st.stdev(d) if n > 1 else float("nan")
        print(f"\n{tag:<6} against rule: {st.mean(d):+.1f} points, sd {sd:.1f}, "
              f"t = {st.mean(d) / (sd / n ** 0.5):.2f} on {n - 1} df, "
              f"wins {sum(x > 0 for x in d)}/{n}")

    # THE SPLIT THAT MATTERS. probe_adapt.py's finding was not the pooled mean,
    # it was that the correction is aimed at the benches #19 is about. If `self`
    # keeps that aim it is worth building even at a flat pooled mean; if it does
    # not, the label is the reason and the arm is dead.
    sick = [r for r in rows if r["oracle"] - r["rule"] >= 10]
    well = [r for r in rows if r["oracle"] - r["rule"] < 10]
    print("\nsplit by whether the bench threw its ceiling away")
    for tag, g in ((f"lost 10+ points (n={len(sick):2d})", sick),
                   (f"kept most of it (n={len(well):2d})", well)):
        if not g:
            continue
        print(f"  {tag}   adapt {st.mean(r['adapt'] - r['rule'] for r in g):+5.1f}"
              f"   cued {st.mean(r['cued'] - r['rule'] for r in g):+5.1f}"
              f"   self {st.mean(r['self'] - r['rule'] for r in g):+5.1f}")

    lk = [r["leak"] for r in rows if r["leak"] == r["leak"]]
    ms = [r["miss"] for r in rows if r["miss"] == r["miss"]]
    if lk:
        print(f"\nthe label itself: leak median {st.median(lk):.1f}% "
              f"(class frames inside the quiet average), worst {max(lk):.1f}%")
        print(f"                  miss median {st.median(ms):.1f}% "
              f"(quiet frames the rule declined), worst {max(ms):.1f}%")
    print(f"\nhow far the cut ever moved: adapt {st.median(r['sw_a'] for r in rows):.2f}"
          f"   cued {st.median(r['sw_c'] for r in rows):.2f}"
          f"   self {st.median(r['sw_s'] for r in rows):.2f}"
          f"   (median peak |shift|)")
    print(f"  `self` cannot exceed `room` = "
          f"{st.median(r['room'] for r in rows):.2f} median, "
          f"{max(r['room'] for r in rows):.2f} worst; "
          f"{sum(r['sw_s'] >= 0.9 * r['room'] for r in rows)}/{n} benches "
          f"pinned at 90% of it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
