# /// script
# requires-python = ">=3.11"
# ///
"""Compare decision rules on frames the board already recorded.

    uv run --script tools/probe_rule.py /tmp/m9_cue.log [more logs...]

Every rule here takes the same input - demo.py's per-frame z vector, one number
per query - and returns a label. They are scored on the same frames, so the
between-visit variance that dominates this bench (M19: 0.85 on an effect of
0.08) is differenced away rather than averaged over. A rule costs a laptop
second here and a 3.5-minute board session there, and M20 shipped a rule that
had only ever been measured the second way.

The rules:

  threshold   what M20 ships. Rank the state queries, require the weakest gate
              to clear its z threshold. No gate present -> plain ranking.
  rank        rank the state queries, no threshold anywhere. The rule M20
              replaced, and the one it lost to.
  enrol-1nn   THE PROPOSAL. The operator shows each class once; the board keeps
              the mean z VECTOR for that class and thereafter labels a frame by
              whichever stored vector is nearest. No threshold, no gate, and no
              assumption that the boundary sits at zero - which is the specific
              thing measurement says is false (AUC 1.000 and 79.4% at zero,
              because the right cut is -3.79).
  enrol-mid   the same enrolment, but reduced to one axis: the margin between
              two fixed queries, cut halfway between the two class means. This
              is what a two-query set collapses to, and it is here to say
              whether the vector form buys anything over the scalar one.

Enrolment is scored HELD OUT. It sees the first visit to each class and is
scored only on visits it has not seen, because a boundary fitted to all the
data and then scored on all the data is not a measurement of anything.
"""
import re
import statistics as st
import sys
from itertools import combinations
from pathlib import Path

FRAME = re.compile(r"^frame\s+(\d+) :\s+(.*?)\s+led")
SCORE = re.compile(r"(\S.*?)\s([-+]\d+\.\d+)\*?(?=\s|$)")
ROLE = re.compile(r"^\s+(\S.*?)\s{2,}(plain|presence|state)"
                  r"(?:,\s+(?:ranked|gates)|\s+z>\s*([-\d.]+))")


def read(log: Path):
    """(segments, roles, thresholds) - segments are (label, [z-vectors])."""
    cues = Path(str(log) + ".cues")
    if not cues.exists():
        raise SystemExit(f"{log}: no .cues sidecar, so the boundaries are opinion")
    # cue.py drops --settle frames after each cue because the operator's hand is
    # in shot, and the sidecar records the span it cued rather than the span it
    # kept. Counting them here scored 240 frames where score_cue.py scores 180,
    # and the two tools then disagreed by 11 points on the same run.
    settle = 0
    spans = []
    for line in cues.read_text().splitlines():
        if line.startswith("#"):
            m = re.search(r"settle (\d+)", line)
            if m:
                settle = int(m.group(1))
            continue
        if not line.strip():
            continue
        a, b, label = line.split("\t")
        spans.append((int(a) + settle, int(b), label))

    roles, thr, frames = {}, {}, {}
    for line in log.read_text(errors="replace").splitlines():
        m = ROLE.match(line)
        if m:
            roles[m.group(1)] = m.group(2)
            if m.group(3):
                thr[m.group(1)] = float(m.group(3))
        m = FRAME.match(line)
        if m:
            frames[int(m.group(1))] = {n: float(v)
                                       for n, v in SCORE.findall(m.group(2))}

    segs = []
    for a, b, label in spans:
        vecs = [frames[i] for i in range(a, b + 1) if i in frames]
        if vecs:
            segs.append((label, vecs))
    return segs, roles, thr


def rule_threshold(v, states, gates, thr):
    """M20: the weakest gate must clear, then rank the states."""
    if gates and min(v[g] - thr.get(g, 0.0) for g in gates) < 0:
        return None
    return max(states, key=lambda n: v[n])


def rule_rank(v, states):
    return max(states, key=lambda n: v[n])


def centroid(vecs, keys):
    return {k: st.mean(f[k] for f in vecs) for k in keys}


def dist(v, c, keys):
    return sum((v[k] - c[k]) ** 2 for k in keys)


def cv_hits(visits, names, ctr, k, fold):
    """Enrol on visit `fold` of each class using its first k frames; score the rest."""
    cents = {lab: ctr(centroid(v[fold][:k], names)) for lab, v in visits.items()}
    return sum(1 for lab, v in visits.items()
               for i, vecs in enumerate(v) if i != fold
               for f in vecs
               if min(cents, key=lambda c: dist(ctr(f), cents[c], names)) == lab)


def cv_total(visits, fold):
    return sum(len(vecs) for v in visits.values()
               for i, vecs in enumerate(v) if i != fold)


def score(log: Path) -> None:
    segs, roles, thr = read(log)
    names = sorted(segs[0][1][0])
    labels = [lab for lab, _ in segs]
    # A query is "a state" if a scene is named after it - the M19 lesson that
    # reading it off the declared role skipped every plain-query run.
    states = [n for n in names if n.rstrip("~").strip() in labels]
    gates = [n for n in names if roles.get(n) == "presence"]
    classes = [lab for lab in dict.fromkeys(labels) if lab in
               {n.rstrip("~").strip() for n in states}]

    print(f"\n=== {log}")
    print(f"    queries {names}")
    print(f"    ranking {states}   gates {gates or '(none)'}")
    print(f"    classes {classes}, {len(segs)} segments")
    if len(classes) < 2:
        print("    fewer than two classes named by a query - nothing to score")
        return

    def truth(name):
        return {n: n.rstrip("~").strip() for n in states}[name]

    scored = [(lab, v) for lab, vecs in segs if lab in classes for v in vecs]
    n = len(scored)

    # --- the two rules that need no calibration -------------------------
    for title, fn in (("threshold (M20)", lambda v: rule_threshold(v, states, gates, thr)),
                      ("rank", lambda v: rule_rank(v, states))):
        ok = sum(1 for lab, v in scored
                 if (p := fn(v)) is not None and truth(p) == lab)
        print(f"    {title:<22} {ok:>4}/{n}  {100*ok/n:5.1f}%")

    # --- enrolment, held out --------------------------------------------
    # The first visit to each class is what the operator would have shown the
    # board. Everything after it is unseen, and only that is scored.
    seen, train = set(), {}
    used = []
    for i, (lab, vecs) in enumerate(segs):
        if lab in classes and lab not in seen:
            seen.add(lab)
            train[lab] = vecs
            used.append(i)
    held = [(lab, v) for i, (lab, vecs) in enumerate(segs)
            if i not in used and lab in classes for v in vecs]
    if len(train) < 2 or not held:
        print("    enrolment: not enough repeat visits to hold anything out")
        return

    cents = {lab: centroid(vecs, names) for lab, vecs in train.items()}
    ok = sum(1 for lab, v in held
             if min(cents, key=lambda c: dist(v, cents[c], names)) == lab)
    print(f"    {'enrol-1nn (held out)':<22} {ok:>4}/{len(held)}  "
          f"{100*ok/len(held):5.1f}%   from {len(used)} segments, "
          f"{sum(len(v) for v in train.values())} frames")

    # Same rule with the frame's mean z across queries removed, from both the
    # frame and the reference. Today's drift run says the sensor moves every
    # query together - 1.5 z of common mode in four minutes - and Euclidean
    # distance in raw z charges a frame for that, while a difference of two
    # queries cancels it exactly. This is the difference, generalised past two:
    # subtract the mean and the common mode is gone whatever the query count.
    def ctr(v):
        m = st.mean(v[k] for k in names)
        return {k: v[k] - m for k in names}

    ccents = {lab: ctr(c) for lab, c in cents.items()}
    ok = sum(1 for lab, v in held
             if min(ccents, key=lambda c: dist(ctr(v), ccents[c], names)) == lab)
    print(f"    {'enrol-1nn, centred':<22} {ok:>4}/{len(held)}  "
          f"{100*ok/len(held):5.1f}%   <- common mode removed")

    # The scalar form, for every pair of queries, so "the vector buys nothing"
    # is a measurement rather than an assumption.
    best = None
    for a, b in combinations(names, 2):
        mids = {lab: st.mean(f[a] - f[b] for f in vecs)
                for lab, vecs in train.items()}
        if len(mids) != 2:
            continue
        (la, ma), (lb, mb) = sorted(mids.items(), key=lambda kv: kv[1])
        cut = (ma + mb) / 2
        hit = sum(1 for lab, v in held
                  if lab == (la if (v[a] - v[b]) < cut else lb))
        if best is None or hit > best[0]:
            best = (hit, a, b, cut)
    hit, a, b, cut = best
    print(f"    {'enrol-mid (held out)':<22} {hit:>4}/{len(held)}  "
          f"{100*hit/len(held):5.1f}%   on ({a}) - ({b}) at {cut:+.2f}")

    # How many frames the operator has to hold still for. 30 was cue.py's
    # --hold, not a requirement, and an appliance that needs half a minute per
    # class to enrol is a different product from one that needs two seconds.
    #
    # Rotated over which visit is the enrolment, not measured on the first one
    # alone. A single split of a bench with three visits per class is the exact
    # shape of evidence that has already been wrong twice here: the first cut
    # of this said 1 frame beats 30, from one fold, which is a claim about one
    # lucky frame and not about the rule.
    visits = {}
    for lab, vecs in segs:
        if lab in classes:
            visits.setdefault(lab, []).append(vecs)
    folds = min(len(v) for v in visits.values())

    # Pooled over folds, not averaged over them: the folds do not all have the
    # same number of held-out frames, so a mean of per-fold percentages would
    # weight a short visit like a long one.
    #
    # This is a named function rather than an expression inside the f-string
    # because the expression needed a line break to fit, and a line break inside
    # a replacement field is Python 3.12 syntax (PEP 701). This file declares
    # >=3.11 and was a SyntaxError on it - i.e. it did not run at all on the
    # version it advertises. Keep the arithmetic out here.
    def held_out_pct(k):
        hit = sum(cv_hits(visits, names, ctr, k, f) for f in range(folds))
        return 100 * hit / sum(cv_total(visits, f) for f in range(folds))

    print(f"    {'enrolment frames':<22} " + "   ".join(
        f"{k}f {held_out_pct(k):.0f}%"
        for k in (1, 2, 5, 10, 20, 30)
        if all(len(vv) >= k for v in visits.values() for vv in v))
        + f"   ({folds}-fold, each visit used as the enrolment in turn)")

    # The baseline segment is the scene with nothing in it, which is what a
    # presence answer has to be against. Enrolling it as a third class turns
    # the gate M20 bolted on the side into one more reference - and unlike a
    # threshold, a reference cannot shut on a class it was meant to admit.
    base = [vecs for lab, vecs in segs if lab not in classes]
    if base:
        c3 = dict(ccents)
        c3["(absent)"] = ctr(centroid(base[0], names))
        truth3 = held + [("(absent)", v) for v in base[0][len(base[0]) // 2:]]
        hit = sum(1 for lab, v in truth3
                  if min(c3, key=lambda c: dist(ctr(v), c3[c], names)) == lab)
        miss = sum(1 for lab, v in truth3 if lab != "(absent)"
                   and min(c3, key=lambda c: dist(ctr(v), c3[c], names)) == "(absent)")
        print(f"    {'+ (absent), centred':<22} {hit:>4}/{len(truth3)}  "
              f"{100*hit/len(truth3):5.1f}%   3-way, {miss} real frames "
              f"lost to (absent)")

        # Centring subtracts the frame's overall level - and "is anything here
        # at all" IS the overall level, so the state axis and the presence axis
        # are not the same axis and cannot share a rule. Measured rather than
        # argued: the mean z across queries, empty scene against object scenes.
        lvl = lambda v: st.mean(v[k] for k in names)
        empty = [lvl(v) for v in base[0]]
        obj = [lvl(v) for lab, vecs in segs if lab in classes for v in vecs]
        cut = (st.mean(empty) + st.mean(obj)) / 2
        up = st.mean(obj) > st.mean(empty)
        ok = (sum(1 for x in obj if (x > cut) == up)
              + sum(1 for x in empty if (x > cut) != up))
        print(f"    {'presence = mean z':<22} {ok:>4}/{len(obj)+len(empty)}  "
              f"{100*ok/(len(obj)+len(empty)):5.1f}%   empty {st.mean(empty):+.2f} "
              f"vs object {st.mean(obj):+.2f}, cut {cut:+.2f}")
        # That percentage is a majority-class score when the axis is flat, and
        # flat is exactly what a two-phrase contrast set produces: the queries
        # come out exact negatives, every frame's mean z is 0, and `x > cut` is
        # then a coin the tie-break always calls the same way. The 06:41 run
        # scored 90.0% that way on 180 object frames out of 200. Say so, because
        # 90% next to 81% reads as an improvement.
        if abs(st.mean(obj) - st.mean(empty)) < 0.05:
            share = max(len(obj), len(empty)) / (len(obj) + len(empty))
            print(f"    {'':<22} {'':>4} {'':>5}    ^ THE AXIS IS FLAT - empty "
                  f"and object are the same level, so this\n"
                  f"    {'':<22} {'':>4} {'':>5}      is the majority class "
                  f"({100*share:.1f}%) and not a measurement. Two contrast\n"
                  f"    {'':<22} {'':>4} {'':>5}      queries from two phrases "
                  f"are negatives; send them bare.")

    # What the enrolment actually stored, because a rule whose references sit
    # on top of each other is one bad frame from flipping.
    print("    references:")
    for lab, c in cents.items():
        print(f"      {lab:<18} " + "  ".join(f"{k}={c[k]:+.2f}" for k in names))
    if len(cents) == 2:
        (_x, cx), (_y, cy) = cents.items()
        sep = dist(cx, cy, names) ** 0.5
        spread = st.mean(dist(v, cents[lab], names) ** 0.5
                         for lab, v in held)
        print(f"      separation {sep:.2f} against a mean frame-to-reference "
              f"distance of {spread:.2f}")


if __name__ == "__main__":
    args = sys.argv[1:] or ["/tmp/m9_cue.log"]
    for p in args:
        score(Path(p))
