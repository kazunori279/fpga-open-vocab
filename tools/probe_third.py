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
under this one something must, so a span is spent. This script enrols from one
empty span after the rule engages, using the same window length the board used
for the classes, and scores on everything else - the remaining empty spans and
all the class frames. Benches with only one empty span after engage are skipped
rather than scored against their own reference.

WHICH empty span enrols is ROTATED, and until 2026-09-08 it was not. The first
cut took `empties[0]` and held out the rest, so every number in the table was
one draw of "which span did the operator happen to show first" - on precisely
the reference this issue says wanders. The same shape of bug was found in
probe_multivisit.py the same day, where it was worth four points and a wrong
conclusion. Here it was worth less than one point of mean (77.5 -> 78.2) but a
good deal of spread: three-nn over shipped went from t = 6.12 to t = 7.62 on the
same 31 benches without the gap itself moving. The single draw was noise, not
bias. Every arm rotates, not only the enrolled one, so the arms that never look
at an empty reference - `mid`, `ship`, `oracle` - are still scored on the same
held-out empty frames as the arm that does.

FOUR ARMS, all on the same held-out frames, all as balanced accuracy so a
rotation with 30 empty frames and 60 class frames cannot be won by answering
"present":

    band-oracle   the best radius for THIS bench, fitted on the frames it is
                  scored on. Not shippable - it is the ceiling #18's shape can
                  never exceed, and the honest thing to beat.
    three-nn      the rule above. No threshold, nothing fitted but the third
                  reference itself.
    three-nn x2   the same rule with the empty reference averaged over TWO
                  spans. See below.
    midpoint      the same rule with the third reference NOT ENROLLED - pinned
                  at `(D_ref[a] + D_ref[b]) / 2` instead. See below.
    shipped       `FGX_ABSENT_TRIP = 2.0 sep`, what the board does today.

THE `midpoint` ARM, and why it is not a fourth idea. "If the two classes score
close together, do not light the LED" is the obvious heuristic, and written out
it is `|D - (D_ref[a] + D_ref[b]) / 2| < gap / 4`, an abstain region around the
centre. That is EXACTLY `three-nn` with the empty reference placed at the
midpoint by assumption rather than measured - the nearest-of-three cells put
their walls at the quarter and three-quarter marks all by themselves, so the
heuristic needs no width constant either. So the two arms differ in one thing
only: whether the appliance spends an enrolment visit finding out where the
empty desk really sits, or assumes it sits in the middle.

That is worth a column because the assumption looked good on the first board
bench - the empty desk landed 3.23 from the nearest class on a pair 6.66 apart -
and because if it holds, `'0'` stops being necessary: no visit spent, and the
rule is live from the first frame instead of a cycle later. `pos` is the
diagnostic, the measured reference's position as a fraction of the way from one
class reference to the other. 0.50 is the midpoint; a bench far from it is a
bench the heuristic is guessing wrong about.

AND ONE DIAGNOSTIC. The obvious way for this to fail is for the empty desk to
not stay put: on 2026-08-25 run 1 the four empty spans of one bench sat at
-3.02, -0.59, +1.02 and +1.01 on the margin axis while the classes translated
underneath them. `drift` is the spread of the empty span centres in units of the
classes' own frame scatter, so a bench where the empty desk wanders has a large
one, and it is the number that says whether a third reference could ever have
held.

THE `three-nn x2` ARM is the presence-stage twin of probe_multivisit.py's arm C,
and it exists because if the empty desk moves between visits then one visit is
the wrong thing to pin its cell to. It needs three empty spans - two to enrol
from, one to hold out - so it is scored on 29 benches rather than 31 and is
reported against three-nn on THOSE 29 only. Comparing it to the pooled mean
above would be comparing two different sets of benches.

WHAT IT SAYS OVER 33 BENCHES, 2026-09-08 evening, after the rotation and after
#30's paired bench added two more logs:

    band-oracle  73.7     three-nn  79.4     midpoint  55.0     shipped  53.8

**The third reference holds and the second span does not help.** three-nn over
shipped is +25.6 points, t = 8.16 on 32 df, winning 29 of 33 - and it beats even
the fitted band-oracle by 5.7, which is the point: no radius can do what a cell
does, so the best radius for a bench loses to a rule that fits nothing. But
three-nn x2 over three-nn is +0.2 at t = 0.67, winning 12 of 31, worst -7.2.
That is a coin flip. The second empty span buys nothing, and unlike #19's arm C
it is not even aimed - so #18 does not need a second `'0'` press.

**Drift is still the thing that decides a bench** and a second span does not
touch it: steady (n=17) 84.7, wanders (n=16) 73.7, r = -0.395 against three-nn.
The remaining loss is class frames crossing into the empty cell on runs where
the desk moves under them, and averaging two positions of a moving desk gives
you a position the desk is not at either. That is why this arm was flat. What
would move it is the desk not moving - #30's territory, not this one's.

**`midpoint` is dead.** -24.4 points against three-nn, t = -6.19, winning 4 of
33, and `pos` has median 0.78 with 25 of 33 outside 0.25-0.75. The empty desk is
not in the middle and is not reliably anywhere, so the `'0'` press cannot be
skipped even though the second one is not worth making.

At 31 benches these read 78.2 / +24.2 t 7.62 / 72.0 / 55.3 and steady 84.1
against wanders 71.9. Two runs moved every one of them the favourable way,
which is the size of the noise on a pool this small and the reason no single
bench's figure is quoted anywhere.
"""
import statistics as st
import sys
from itertools import combinations, pairwise
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

    present = [d for lab, m in runs if lab != EMPTY for d in m]
    if min(len(present), sum(len(m) for m in empties)) < MIN_FRAMES:
        raise Skip(f"fewer than {MIN_FRAMES} held-out frames on one side")

    scat = st.mean(st.pstdev([d for lab, m in runs if lab == lab_i for d in m])
                   for lab_i in labels)
    band = lambda d: min(abs(d - dref[a_lab]), abs(d - dref[b_lab])) / 2 ** 0.5
    d_mid = (dref[a_lab] + dref[b_lab]) / 2
    gap = dref[b_lab] - dref[a_lab]

    # ROTATE WHICH EMPTY SPAN ENROLS. The first cut of this took `empties[0]`
    # and scored on `empties[1:]`, so the whole table was one draw of "which
    # span did the operator show first" - and this issue's complaint is exactly
    # that the empty desk does not stay put between spans. The same shape of
    # bug was found in probe_multivisit.py on 2026-09-08, where it was worth
    # four points and a wrong conclusion.
    #
    # Every arm rotates, not just the enrolled one, so they stay on the same
    # held-out frames per fold: the arms that never look at an empty reference
    # (`mid`, `ship`, `oracle`) still have to be scored on the same empty
    # frames as the arm that does, or the comparison is between two test sets.
    acc = {k: [0, 0, 0, 0] for k in ("oracle", "three", "three2", "mid", "ship")}
    poss = []

    def add(key, call, empty_d):
        a = acc[key]
        a[0] += sum(call(d) for d in empty_d)
        a[1] += len(empty_d)
        a[2] += sum(not call(d) for d in present)
        a[3] += len(present)

    def nearest_empty(d, d_ref):
        return abs(d - d_ref) < min(abs(d - dref[a_lab]), abs(d - dref[b_lab]))

    for e_i in range(len(empties)):
        empty = [d for j, m in enumerate(empties) if j != e_i for d in m]
        # The board would enrol from a window, not from a whole span.
        d_ref = st.mean(empties[e_i][:window])
        poss.append((d_ref - dref[a_lab]) / gap if gap else float("nan"))

        # band-oracle: sweep every midpoint between adjacent observed distances.
        ds = sorted({band(d) for d in empty + present})
        cuts = [(x + y) / 2 for x, y in pairwise(ds)] or [ds[0]]
        e_b, p_b = [band(d) for d in empty], [band(d) for d in present]
        r = max(cuts, key=lambda r: balanced(lambda v, r=r: v > r, e_b, p_b))
        add("oracle", lambda d, r=r: band(d) > r, empty)

        # three-nn: nearest of three references, no threshold anywhere.
        add("three", lambda d, q=d_ref: nearest_empty(d, q), empty)

        # midpoint: the same cells, with the third reference assumed rather
        # than enrolled. Nothing is read from the empty spans at all.
        add("mid", lambda d: nearest_empty(d, d_mid), empty)
        add("ship", lambda d: band(d) > SHIPPED * sep, empty)

    # AND THE ARM THIS ISSUE NEEDS: the empty reference from TWO spans. It is
    # the presence-stage twin of probe_multivisit.py's arm C, and it exists
    # because #18's remaining loss is class frames drifting into a cell pinned
    # by one visit to a desk that moves. Needs three spans - two to enrol from
    # and one to hold out - so it is scored on fewer benches and reported with
    # its own n rather than folded into the table above.
    for p, q in combinations(range(len(empties)), 2):
        rest = [d for j, m in enumerate(empties) if j not in (p, q) for d in m]
        if not rest:
            continue
        d_ref = st.mean(empties[p][:window] + empties[q][:window])
        add("three2", lambda d, r=d_ref: nearest_empty(d, r), rest)

    def bal(key):
        e, en, pc, pn = acc[key]
        return 50.0 * (e / en + pc / pn) if en and pn else float("nan")

    oracle, three, mid, ship = (bal(k) for k in ("oracle", "three", "mid", "ship"))
    three2 = bal("three2")
    pos = st.mean(poss)

    centres = [st.mean(m) for m in empties]
    drift = (max(centres) - min(centres)) / scat if scat else float("nan")
    return (log.stem.replace("m9_cue-", ""), oracle, three, ship, drift,
            len(empties), mid, pos, three2)


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
    print(f"{'bench':<18} {'oracle':>7} {'three':>7} {'three2':>7} {'mid':>7} "
          f"{'ship':>7} {'three-ship':>10} {'pos':>6} {'drift':>6} {'spans':>5}")
    for name, orc, thr, shp, dft, ns, mid, pos, th2 in rows:
        t2 = f"{th2:7.1f}" if th2 == th2 else f"{'-':>7}"
        print(f"{name:<18} {orc:7.1f} {thr:7.1f} {t2} {mid:7.1f} {shp:7.1f} "
              f"{thr - shp:+10.1f} {pos:6.2f} {dft:6.2f} {ns:5d}")

    n = len(rows)
    orc = [r[1] for r in rows]
    thr = [r[2] for r in rows]
    shp = [r[3] for r in rows]
    dft = [r[4] for r in rows]
    mids = [r[6] for r in rows]
    poss = [r[7] for r in rows]
    print(f"\nn = {n}   balanced accuracy, held out, mean over benches")
    print(f"  band-oracle (unshippable ceiling)   {st.mean(orc):5.1f}")
    print(f"  three-nn    (a third reference)     {st.mean(thr):5.1f}")
    print(f"  midpoint    (assumed, no '0' press) {st.mean(mids):5.1f}")
    print(f"  shipped     (2.0 sep)               {st.mean(shp):5.1f}")

    # The two-span arm, on the benches that have a third empty span to hold out.
    # Paired against three-nn on those benches only - comparing its mean to the
    # mean above would be comparing two different sets of benches.
    pair = [(r[8], r[2]) for r in rows if r[8] == r[8]]
    if pair:
        d2 = [a - b for a, b in pair]
        m2, n2 = st.mean(d2), len(d2)
        print(f"\ntwo empty spans instead of one, on the {n2} benches with a "
              f"third span to hold out:\n  three-nn x2 {st.mean(a for a, _ in pair):5.1f} "
              f"against {st.mean(b for _, b in pair):5.1f} for one span, "
              f"{m2:+.1f} points")
        if n2 > 1:
            sd2 = st.stdev(d2)
            print(f"  sd {sd2:.1f}, t = {m2 / (sd2 / n2 ** 0.5):.2f} on {n2 - 1} df,"
                  f" wins {sum(d > 0 for d in d2)}/{n2}, worst {min(d2):+.1f}")
    md = [m - t for m, t in zip(mids, thr, strict=True)]
    sdm = st.stdev(md)
    print(f"\nmidpoint against three-nn: {st.mean(md):+.1f} points, sd {sdm:.1f},"
          f" t = {st.mean(md) / (sdm / n ** 0.5):.2f} on {n - 1} df,"
          f" wins {sum(d > 0 for d in md)}/{n}")
    print(f"where the enrolled empty reference actually sits: pos median "
          f"{st.median(poss):.2f}, IQR {sorted(poss)[n // 4]:.2f}-"
          f"{sorted(poss)[3 * n // 4]:.2f}, "
          f"{sum(not 0.25 <= p <= 0.75 for p in poss)}/{n} outside the middle "
          f"half\n  (0.50 is the midpoint the heuristic assumes; outside "
          f"0.25-0.75 it is not even in its own cell)")
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
