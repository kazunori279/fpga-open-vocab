#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Where does the empty desk land on the axis the rule decides in? Issue #18.

    uv run --script tools/probe_absent.py bench/cue/*.log

WHERE THIS PICKS UP. `tools/probe_presence.py` ends on a sentence it could not
finish: the shipped `FGX_ABSENT_TRIP = 2.0 sep` is not a constant waiting to be
retuned, the gain shrank to nothing as benches were added, and "what is left of
#18 is why some scenes invert, which is a geometry question and not a threshold
one." `tools/probe_origin.py` then tested the obvious candidate - #21's
reference-on-the-origin - and it is not the answer either: near/gap against
presence AUC is r = +0.146 over 28 benches, and 20260817-113304 sits at 0.216
with an AUC of 0.991 while 20260820-063704 sits at 0.704 with 0.474.

This script asks the geometry question directly, and the first thing it does is
establish that there IS only one dimension to ask it in.

THE IDENTITY. With two queries, `c[] = z[] - mean(z)` is `[+D/2, -D/2]` for the
margin `D = z[A] - z[B]`. Every reference is the same shape. So

    || c[] - qref[k] ||  ==  | D - D_ref[k] |  /  sqrt(2)

exactly, and #18's proposal

    absent  <=>  min_k || c[] - qref[k] ||  >  radius

is not a radius in a 512-d space, or even in a 2-d one. It is a BAND on the
margin axis, centred on the two references, and `radius` is its half-width. The
script recomputes both sides per frame and prints the largest disagreement; if
that number is not at machine epsilon, everything below is void.

WHAT THAT COSTS. A band has an inside and an outside and nothing else. The two
classes own the inside by construction - they are what the references were
averaged from. "Neither" has no orthogonal direction to occupy, so the empty
desk has to land somewhere on the same line, and where it lands is a property
of the text encoder, the two query strings and the room. Enrolment never sees
an empty desk under this rule (`references()` skips key '0'), so there is no
enrolment-time quantity that could have predicted it. The script measures the
landing site three ways per bench:

    presAUC     P(an empty frame is farther from both references than a class
                frame is). Above 0.5 the rule points the right way; below 0.5
                it is inverted, and the board would call an empty desk a book.
    empty is    whether the mean empty margin falls BETWEEN the two references
                - inside the band, where the rule says "present" - or outside.
    gap (SD)    how far the mean empty margin sits from the nearer of the two
                class means, in units of the classes' own frame scatter. This
                is the separation the rule has to work with, and unlike the
                distance to a REFERENCE it does not shrink just because a pair
                collapsed.

WHAT IT SAID ON 2026-08-25, over 28 benches (the 23 with a held-out empty
rotation, plus the five-run 08-25 repeat session):

    identity checked to 3.55e-15 over 8514 frame-reference pairs
    mean presence AUC 0.597, and 14 of the 28 are inverted

    empty outside the pair   n=18   mean AUC 0.687    6 inverted
    empty between the refs   n=10   mean AUC 0.434    8 inverted

    gap (SD) against presence AUC: r = +0.471, median gap 1.32 SD

So the rule is near chance archive-wide, and the thing that decides which way a
bench falls is whether the empty desk landed inside the band. Eight of the ten
that landed inside are inverted - not a tuning failure, a placement one. No
half-width separates a point from the interval it is sitting in.

THREE DIFFERENT INVERSION COUNTS ARE NOW ON RECORD AND THEY ARE NOT THE SAME
MEASUREMENT. `tools/probe_presence.py` says six of fourteen, issue #18's
comment says eight of 23, this says 14 of 28. The populations differ - fourteen
is the clean subset, 23 is the archive before 2026-08-25, 28 adds that day's
five - and so do the details: this script drops ten settle frames off the front
of every span and scores the AUC on `min_k` distance rather than per-class.
Subtracting the five new benches, which are ALL inverted, leaves nine against
#18's eight, so one bench's worth of the difference is definition and the rest
is population. Quote whichever you mean, with the population attached.

THE FIVE 08-25 BENCHES ARE THE SAME BENCH, five times in half an hour with
nothing changed, and the presence rule inverted on all five (0.196, 0.207,
0.263, 0.321, 0.426). `bench/README.md` records that this session spans 18
points of held-out accuracy, so it is the archive's noise floor made visible -
and the presence rule does not wobble across it, it is wrong on every one.
That is a repeat measurement, which is the one thing the single-bench counts
above could never be.
"""
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_reject import BASELINE, EMPTY, Skip, auc, centred, dist, load, references

SETTLE = 10             # frames dropped off the front of every span
MIN_FRAMES = 10         # per class, and for the empty rotation


def pearson(xs, ys):
    mx, my = st.mean(xs), st.mean(ys)
    sx = sum((a - mx) ** 2 for a in xs) ** 0.5
    sy = sum((b - my) ** 2 for b in ys) ** 0.5
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True)) / (sx * sy)


def measure(log: Path, checks: list):
    """One row, or Skip. Appends every identity residual it computes to `checks`."""
    if "fake" in log.stem or "smoke" in log.stem:
        raise Skip("synthetic or a smoke test; it would vote in the pooled mean")
    spans, frames, enrol, window = load(log)
    names = sorted(next(iter(frames.values()))[0])
    labels = sorted({lab for _a, _b, lab in spans if lab not in (EMPTY, BASELINE)})
    if len(labels) != 2 or set(labels) != set(names):
        raise Skip("the identity below is a two-query fact and this is not two queries")

    refs, ref_lab, _nvis, _scat, _sep, engage = references(spans, frames, enrol, window, names)
    if len({ref_lab[k] for k in refs}) != 2:
        raise Skip("both classes must have a reference to have a band between them")
    # c = [+D/2, -D/2] with names sorted, so the A component is half the margin.
    dref = {ref_lab[k]: 2 * refs[k][0] for k in refs}

    a_lab, b_lab = labels
    margins = {a_lab: [], b_lab: [], EMPTY: []}
    for a, b, lab in spans:
        if lab == BASELINE:
            continue
        for i in range(a + SETTLE, b + 1):
            if i not in frames or i < engage:
                continue
            z = frames[i][0]
            d = z[a_lab] - z[b_lab]
            c = centred(z, names)
            for k in refs:
                checks.append(abs(dist(c, refs[k]) - abs(d - dref[ref_lab[k]]) / 2 ** 0.5))
            margins[lab].append(d)
    if min(len(v) for v in margins.values()) < MIN_FRAMES:
        raise Skip(f"fewer than {MIN_FRAMES} held-out frames in one of the three states")

    band = lambda d: min(abs(d - dref[a_lab]), abs(d - dref[b_lab])) / 2 ** 0.5
    present = margins[a_lab] + margins[b_lab]
    pres = auc([band(d) for d in margins[EMPTY]], [band(d) for d in present])

    scat = (st.pstdev(margins[a_lab]) + st.pstdev(margins[b_lab])) / 2
    mid = st.mean(margins[EMPTY])
    gap = min(abs(mid - st.mean(margins[a_lab])), abs(mid - st.mean(margins[b_lab]))) / scat
    lo, hi = sorted(dref.values())
    return log.stem.replace("m9_cue-", ""), pres, gap, not (lo <= mid <= hi)


def main(argv):
    if not argv:
        raise SystemExit(__doc__.strip().splitlines()[2].strip())
    rows, checks = [], []
    for arg in argv:
        log = Path(arg)
        try:
            rows.append(measure(log, checks))
        except (Skip, SystemExit) as e:
            print(f"  skip {log.stem}: {e}", file=sys.stderr)
    if not rows:
        raise SystemExit("nothing scoreable")

    print(f"identity  max | ||c-qref|| - |D-Dref|/sqrt(2) |  =  {max(checks):.2e}"
          f"   over {len(checks)} pairs")
    print("  so #18's radius is a band on the margin axis, and nothing else\n")

    rows.sort(key=lambda r: r[1])
    print(f"{'bench':<18} {'presAUC':>8} {'gap (SD)':>9} {'empty is':>9}")
    for name, pres, gap, out in rows:
        print(f"{name:<18} {pres:8.3f} {gap:9.2f} {'outside' if out else 'BETWEEN':>9}")

    aucs = [r[1] for r in rows]
    gaps = [r[2] for r in rows]
    print(f"\nn = {len(rows)}   mean presence AUC = {st.mean(aucs):.3f}"
          f"   inverted (<0.5): {sum(a < 0.5 for a in aucs)}")
    print(f"gap (SD) against presence AUC : r = {pearson(gaps, aucs):+.3f}"
          f"   median gap {st.median(gaps):.2f} SD")
    for tag, want in (("outside the pair", True), ("between the refs", False)):
        g = [r for r in rows if r[3] is want]
        if g:
            print(f"empty {tag} (n={len(g):2d}): mean AUC {st.mean(r[1] for r in g):.3f},"
                  f" {sum(r[1] < 0.5 for r in g)} inverted")


if __name__ == "__main__":
    main(sys.argv[1:])
